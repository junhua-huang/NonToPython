from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin
from app.models.models import (
    AdminAuditLog,
    AdminSetting,
    Comment,
    ModerationEvent,
    Post,
    Report,
    Role,
    RoleApplication,
    SensitiveWord,
    User,
    UserRole,
)
from app.routers.auth import LoginRequest, login as auth_login
from app.serializers.user import serialize_user_admin
from app.services.admin_audit_service import record_required_admin_audit
from app.services.admin_governance_notification_service import (
    create_governance_notification,
    dispatch_governance_notifications,
    dispatch_in_app_and_push,
)
from app.services.admin_settings_service import (
    serialize_moderation_settings,
    set_image_moderation_enabled,
)

router = APIRouter(prefix="/api/admin", tags=["Admin Panel"])


def _require_reason(payload: dict, *, field: str = "reason") -> str:
    reason = payload.get(field)
    if type(reason) is not str or not reason.strip():
        raise HTTPException(status_code=422, detail=f"{field} is required")
    return reason.strip()[:500]


def _admin_reason(payload: dict) -> str:
    if isinstance(payload.get("admin_reason"), str) and payload["admin_reason"].strip():
        return payload["admin_reason"].strip()[:500]
    return _require_reason(payload)


def _user_notice(payload: dict) -> str | None:
    value = payload.get("user_notice")
    return value.strip()[:500] if isinstance(value, str) and value.strip() else None


def _notify_channels(payload: dict) -> list[str] | None:
    value = payload.get("notify_channels")
    if not isinstance(value, list):
        return None
    return [str(item) for item in value]


def _dispatch_after_commit(deliveries, notifications) -> None:
    dispatch_governance_notifications(deliveries)
    for notification in notifications:
        dispatch_in_app_and_push(notification.user_id, notification)


def _page_result(query, *, page: int, page_size: int, serializer):
    total = query.count()
    rows = query.offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [serializer(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _like(value: str) -> str:
    return f"%{value.strip()}%"


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_bool(value) -> bool | None:
    if value is None:
        return None
    if type(value) is bool:
        return value
    normalized = str(value).strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def _json_array_has_values(column):
    return column.isnot(None) & (column != "") & (column != "[]")


def _user_roles(user: User) -> list[str]:
    return [ur.role.name for ur in getattr(user, "user_roles", []) if getattr(ur, "role", None)]


def _serialize_user_admin(user: User, db: Session | None = None) -> dict:
    posts_count = None
    reports_received_count = None
    if db is not None:
        posts_count = db.query(Post).filter(Post.user_id == user.id).count()
        reports_received_count = db.query(Report).filter(
            Report.target_type == "user",
            Report.target_id == user.id,
        ).count()
    payload = serialize_user_admin(user)
    payload.update({
        "posts_count": posts_count,
        "reports_received_count": reports_received_count,
    })
    return payload


def _serialize_post_admin(post: Post, db: Session | None = None) -> dict:
    author = db.query(User).filter(User.id == post.user_id).first() if db is not None else None
    return {
        "id": post.id,
        "user_id": post.user_id,
        "author": _serialize_user_admin(author, db) if author else None,
        "content": post.content,
        "images": json.loads(post.images) if post.images else [],
        "video_url": post.video_url,
        "post_type": post.post_type,
        "visibility": post.visibility,
        "quoted_post_id": post.quoted_post_id,
        "community_only": bool(post.community_only),
        "hidden_by_admin": bool(post.hidden_by_admin),
        "hidden_by": post.hidden_by,
        "hidden_at": post.hidden_at.isoformat() if post.hidden_at else None,
        "created_at": post.created_at.isoformat() if post.created_at else None,
    }


def _serialize_quoted_post_admin(post: Post, db: Session) -> dict:
    return _serialize_post_admin(post, db)


def _serialize_post_detail_admin(post: Post, db: Session) -> dict:
    payload = _serialize_post_admin(post, db)
    author = db.query(User).filter(User.id == post.user_id).first()
    quoted_post = db.query(Post).filter(Post.id == post.quoted_post_id).first() if post.quoted_post_id else None
    payload.update({
        "author": _serialize_user_admin(author, db) if author else None,
        "quoted_post": _serialize_quoted_post_admin(quoted_post, db) if quoted_post else None,
        "comments_count": db.query(Comment).filter(Comment.post_id == post.id).count(),
        "reports_count": db.query(Report).filter(Report.target_type == "post", Report.target_id == post.id).count(),
        "moderation_events": [
            _serialize_moderation_event(event)
            for event in db.query(ModerationEvent).filter(
                ModerationEvent.target_type == "post",
                ModerationEvent.target_id == str(post.id),
            ).order_by(ModerationEvent.created_at.desc()).limit(20).all()
        ],
    })
    return payload


def _serialize_comment_admin(comment: Comment, db: Session | None = None) -> dict:
    author = db.query(User).filter(User.id == comment.user_id).first() if db is not None else None
    return {
        "id": comment.id,
        "post_id": comment.post_id,
        "user_id": comment.user_id,
        "author": _serialize_user_admin(author, db) if author else None,
        "parent_id": comment.parent_id,
        "content": comment.content,
        "hidden_by_admin": bool(getattr(comment, "hidden_by_admin", False)),
        "hidden_by": getattr(comment, "hidden_by", None),
        "hidden_reason": getattr(comment, "hidden_reason", None),
        "hidden_at": comment.hidden_at.isoformat() if getattr(comment, "hidden_at", None) else None,
        "created_at": comment.created_at.isoformat() if comment.created_at else None,
    }


def _serialize_comment_detail_admin(comment: Comment, db: Session) -> dict:
    payload = _serialize_comment_admin(comment, db)
    author = db.query(User).filter(User.id == comment.user_id).first()
    post = db.query(Post).filter(Post.id == comment.post_id).first()
    payload.update({
        "author": _serialize_user_admin(author, db) if author else None,
        "post": _serialize_post_admin(post) if post else None,
        "reports_count": db.query(Report).filter(Report.target_type == "comment", Report.target_id == comment.id).count(),
        "replies_count": db.query(Comment).filter(Comment.parent_id == comment.id).count(),
        "moderation_events": [
            _serialize_moderation_event(event)
            for event in db.query(ModerationEvent).filter(
                ModerationEvent.target_type == "comment",
                ModerationEvent.target_id == str(comment.id),
            ).order_by(ModerationEvent.created_at.desc()).limit(20).all()
        ],
    })
    return payload


def _serialize_report_admin(report: Report, db: Session | None = None) -> dict:
    payload = report.to_dict()
    if db is not None:
        reporter = db.query(User).filter(User.id == report.reporter_id).first()
        payload["reporter"] = _serialize_user_admin(reporter, db) if reporter else None
    return payload


def _report_target_snapshot(db: Session, report: Report) -> dict | None:
    if report.target_type == "post":
        post = db.query(Post).filter(Post.id == report.target_id).first()
        return _serialize_post_admin(post) if post else None
    if report.target_type == "comment":
        comment = db.query(Comment).filter(Comment.id == report.target_id).first()
        return _serialize_comment_admin(comment) if comment else None
    if report.target_type == "user":
        user = db.query(User).filter(User.id == report.target_id).first()
        return _serialize_user_admin(user, db) if user else None
    return None


def _serialize_report_detail_admin(report: Report, db: Session) -> dict:
    payload = _serialize_report_admin(report)
    reporter = db.query(User).filter(User.id == report.reporter_id).first()
    payload.update({
        "reporter": _serialize_user_admin(reporter, db) if reporter else None,
        "target_snapshot": _report_target_snapshot(db, report),
    })
    return payload


def _serialize_identity_application(application: RoleApplication) -> dict:
    return application.to_dict(include_private_user=True)


def _serialize_audit_log(log: AdminAuditLog) -> dict:
    metadata = None
    if log.metadata_json:
        try:
            metadata = json.loads(log.metadata_json)
        except json.JSONDecodeError:
            metadata = None
    return {
        "id": log.id,
        "admin_user_id": log.admin_user_id,
        "action": log.action,
        "target_type": log.target_type,
        "target_id": log.target_id,
        "result": log.result,
        "reason": log.reason,
        "metadata": metadata,
        "ip_address": log.ip_address,
        "user_agent": log.user_agent,
        "created_at": log.created_at.isoformat() if log.created_at else None,
    }


def _user_has_admin_role(db: Session, user_id: int) -> bool:
    return db.query(UserRole).join(Role, UserRole.role_id == Role.id).filter(
        UserRole.user_id == user_id,
        Role.name == "admin",
    ).first() is not None



def _serialize_moderation_event(event: ModerationEvent) -> dict:
    return {
        "id": event.id,
        "provider": event.provider,
        "content_type": event.content_type,
        "route_key": event.route_key,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "actor_user_id": event.actor_user_id,
        "decision": event.decision,
        "error_code": event.error_code,
        "label": event.label,
        "category": event.category,
        "score": event.score,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


@router.post("/auth/login")
def admin_login(data: LoginRequest, request: Request, db: Session = Depends(get_db)):
    response = auth_login(data, request, db)
    if response.user_id is None or not _user_has_admin_role(db, response.user_id):
        raise HTTPException(status_code=403, detail="Required role(s): admin")
    return response


@router.get("/auth/me")
def admin_me(admin: User = Depends(require_admin)):
    payload = serialize_user_admin(admin)
    payload["permissions"] = ["admin:*"]
    return payload


@router.get("/dashboard/summary")
def dashboard_summary(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    today = datetime.utcnow().date()
    start = datetime.combine(today, datetime.min.time())
    return {
        "users_total": db.query(User).count(),
        "users_today": db.query(User).filter(User.created_at >= start).count(),
        "posts_today": db.query(Post).filter(Post.created_at >= start).count(),
        "comments_today": db.query(Comment).filter(Comment.created_at >= start).count(),
        "reports_pending": db.query(Report).filter(Report.status == "pending").count(),
        "identity_applications_pending": db.query(RoleApplication).filter(RoleApplication.status == "pending").count(),
        "sensitive_words_active": db.query(SensitiveWord).filter(SensitiveWord.is_active.is_(True)).count(),
        "cos_image_audit_enabled": serialize_moderation_settings(db)["image_moderation_enabled"],
    }


@router.get("/settings/moderation")
def get_moderation_settings(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return serialize_moderation_settings(db)


@router.patch("/settings/moderation")
def update_moderation_settings(payload: dict = Body(...), request: Request = None, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if type(payload.get("image_moderation_enabled")) is not bool:
        raise HTTPException(status_code=422, detail="image_moderation_enabled must be boolean")
    reason = _require_reason(payload)
    previous, current = set_image_moderation_enabled(
        db,
        enabled=payload["image_moderation_enabled"],
        admin_user_id=admin.id,
    )
    record_required_admin_audit(
        db,
        admin_user_id=admin.id,
        action="update_moderation_settings",
        target_type="admin_setting",
        target_id="image_moderation_enabled",
        reason=reason,
        metadata={"status_before": previous, "status_after": current},
        request=request,
    )
    db.commit()
    return serialize_moderation_settings(db)


@router.get("/audit-logs")
def list_audit_logs(
    q: str | None = Query(None),
    admin_user_id: int | None = Query(None),
    action: str | None = Query(None),
    target_type: str | None = Query(None),
    result: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(AdminAuditLog)
    if q:
        term = _like(q)
        numeric_id = _parse_int(q)
        clauses = [
            AdminAuditLog.action.like(term),
            AdminAuditLog.target_type.like(term),
            AdminAuditLog.target_id.like(term),
            AdminAuditLog.reason.like(term),
        ]
        if numeric_id is not None:
            clauses.append(AdminAuditLog.admin_user_id == numeric_id)
        query = query.filter(or_(*clauses))
    if admin_user_id is not None:
        query = query.filter(AdminAuditLog.admin_user_id == admin_user_id)
    if action:
        query = query.filter(AdminAuditLog.action == action)
    if target_type:
        query = query.filter(AdminAuditLog.target_type == target_type)
    if result:
        query = query.filter(AdminAuditLog.result == result)
    return _page_result(
        query.order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc()),
        page=page,
        page_size=page_size,
        serializer=_serialize_audit_log,
    )


@router.get("/users")
def list_users(
    q: str | None = Query(None),
    status: str | None = Query(None),
    role: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(User)
    if q:
        term = _like(q)
        numeric_id = _parse_int(q)
        clauses = [User.username.like(term), User.email.like(term)]
        if numeric_id is not None:
            clauses.append(User.id == numeric_id)
        query = query.filter(or_(*clauses))
    if status == "active":
        query = query.filter(User.is_active.is_(True))
    elif status == "inactive":
        query = query.filter(User.is_active.is_(False))
    if role:
        query = query.join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id).filter(Role.name == role)
    return _page_result(
        query.order_by(User.created_at.desc(), User.id.desc()),
        page=page,
        page_size=page_size,
        serializer=lambda user: _serialize_user_admin(user, db),
    )


@router.get("/users/{user_id}")
def get_user_detail(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _serialize_user_admin(user, db)


@router.post("/users/{user_id}/deactivate")
def deactivate_user(
    user_id: int,
    payload: dict = Body(...),
    request: Request = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    reason = _admin_reason(payload)
    if user_id == admin.id:
        raise HTTPException(status_code=422, detail="Cannot deactivate yourself")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    status_before = "active" if user.is_active else "inactive"
    user.is_active = False
    deliveries, notifications = create_governance_notification(
        db,
        user=user,
        event_type="account_deactivated",
        target_type="user",
        target_id=user.id,
        user_notice=_user_notice(payload),
        notify_channels=_notify_channels(payload),
    )
    record_required_admin_audit(
        db,
        admin_user_id=admin.id,
        action="deactivate_user",
        target_type="user",
        target_id=user.id,
        reason=reason,
        metadata={"status_before": status_before, "status_after": "inactive"},
        request=request,
    )
    db.commit()
    _dispatch_after_commit(deliveries, notifications)
    return {"message": "User deactivated", "user": _serialize_user_admin(user, db)}


@router.post("/users/{user_id}/reactivate")
def reactivate_user(
    user_id: int,
    payload: dict = Body(...),
    request: Request = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    reason = _admin_reason(payload)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    status_before = "active" if user.is_active else "inactive"
    user.is_active = True
    deliveries, notifications = create_governance_notification(
        db,
        user=user,
        event_type="account_reactivated",
        target_type="user",
        target_id=user.id,
        user_notice=_user_notice(payload),
        notify_channels=_notify_channels(payload),
    )
    record_required_admin_audit(
        db,
        admin_user_id=admin.id,
        action="reactivate_user",
        target_type="user",
        target_id=user.id,
        reason=reason,
        metadata={"status_before": status_before, "status_after": "active"},
        request=request,
    )
    db.commit()
    _dispatch_after_commit(deliveries, notifications)
    return {"message": "User reactivated", "user": _serialize_user_admin(user, db)}


@router.get("/posts")
def list_posts(
    q: str | None = Query(None),
    hidden: bool | None = Query(None),
    visibility: str | None = Query(None),
    has_image: bool | None = Query(None),
    has_video: bool | None = Query(None),
    is_quote: bool | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(Post)
    if q:
        term = _like(q)
        numeric_id = _parse_int(q)
        query = query.outerjoin(User, User.id == Post.user_id)
        clauses = [Post.content.like(term), User.username.like(term), User.email.like(term)]
        if numeric_id is not None:
            clauses.extend([Post.id == numeric_id, Post.user_id == numeric_id])
        query = query.filter(or_(*clauses))
    if hidden is not None:
        query = query.filter(Post.hidden_by_admin.is_(hidden))
    if visibility:
        query = query.filter(Post.visibility == visibility)
    if has_image is True:
        query = query.filter(_json_array_has_values(Post.images))
    elif has_image is False:
        query = query.filter(or_(Post.images.is_(None), Post.images == "", Post.images == "[]"))
    if has_video is True:
        query = query.filter(Post.video_url.isnot(None), Post.video_url != "")
    elif has_video is False:
        query = query.filter(or_(Post.video_url.is_(None), Post.video_url == ""))
    if is_quote is True:
        query = query.filter(Post.quoted_post_id.isnot(None))
    elif is_quote is False:
        query = query.filter(Post.quoted_post_id.is_(None))
    return _page_result(query.order_by(Post.created_at.desc(), Post.id.desc()), page=page, page_size=page_size, serializer=lambda post: _serialize_post_admin(post, db))


@router.get("/posts/{post_id}")
def get_post_detail(post_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return _serialize_post_detail_admin(post, db)


@router.post("/posts/{post_id}/hide")
def hide_post(post_id: int, payload: dict = Body(...), request: Request = None, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    reason = _admin_reason(payload)
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    status_before = "hidden" if post.hidden_by_admin else "visible"
    post.hidden_by_admin = True
    post.hidden_by = admin.id
    post.hidden_at = datetime.utcnow()
    author = db.query(User).filter(User.id == post.user_id).first()
    deliveries, notifications = create_governance_notification(db, user=author, event_type="post_hidden", target_type="post", target_id=post.id, user_notice=_user_notice(payload), notify_channels=_notify_channels(payload))
    record_required_admin_audit(db, admin_user_id=admin.id, action="hide_post", target_type="post", target_id=post.id, reason=reason, metadata={"status_before": status_before, "status_after": "hidden"}, request=request)
    db.commit()
    _dispatch_after_commit(deliveries, notifications)
    return {"message": "Post hidden", "post": _serialize_post_admin(post)}


@router.post("/posts/{post_id}/restore")
def restore_post(post_id: int, payload: dict = Body(...), request: Request = None, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    reason = _admin_reason(payload)
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    status_before = "hidden" if post.hidden_by_admin else "visible"
    post.hidden_by_admin = False
    post.hidden_by = None
    post.hidden_at = None
    author = db.query(User).filter(User.id == post.user_id).first()
    deliveries, notifications = create_governance_notification(db, user=author, event_type="post_restored", target_type="post", target_id=post.id, user_notice=_user_notice(payload), notify_channels=_notify_channels(payload))
    record_required_admin_audit(db, admin_user_id=admin.id, action="restore_post", target_type="post", target_id=post.id, reason=reason, metadata={"status_before": status_before, "status_after": "visible"}, request=request)
    db.commit()
    _dispatch_after_commit(deliveries, notifications)
    return {"message": "Post restored", "post": _serialize_post_admin(post)}


@router.get("/comments")
def list_comments(q: str | None = Query(None), post_id: int | None = Query(None), hidden: bool | None = Query(None), is_reply: bool | None = Query(None), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    query = db.query(Comment)
    if q:
        term = _like(q)
        numeric_id = _parse_int(q)
        query = query.outerjoin(User, User.id == Comment.user_id)
        clauses = [Comment.content.like(term), User.username.like(term), User.email.like(term)]
        if numeric_id is not None:
            clauses.extend([Comment.id == numeric_id, Comment.post_id == numeric_id, Comment.user_id == numeric_id])
        query = query.filter(or_(*clauses))
    if post_id is not None:
        query = query.filter(Comment.post_id == post_id)
    if hidden is not None:
        query = query.filter(Comment.hidden_by_admin.is_(hidden))
    if is_reply is True:
        query = query.filter(Comment.parent_id.isnot(None))
    elif is_reply is False:
        query = query.filter(Comment.parent_id.is_(None))
    return _page_result(query.order_by(Comment.created_at.desc(), Comment.id.desc()), page=page, page_size=page_size, serializer=lambda comment: _serialize_comment_admin(comment, db))


@router.get("/comments/{comment_id}")
def get_comment_detail(comment_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    return _serialize_comment_detail_admin(comment, db)


@router.post("/comments/{comment_id}/hide")
def hide_comment(comment_id: int, payload: dict = Body(...), request: Request = None, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    reason = _admin_reason(payload)
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    status_before = "hidden" if comment.hidden_by_admin else "visible"
    comment.hidden_by_admin = True
    comment.hidden_by = admin.id
    comment.hidden_reason = reason
    comment.hidden_at = datetime.utcnow()
    author = db.query(User).filter(User.id == comment.user_id).first()
    deliveries, notifications = create_governance_notification(db, user=author, event_type="comment_hidden", target_type="comment", target_id=comment.id, user_notice=_user_notice(payload), notify_channels=_notify_channels(payload))
    record_required_admin_audit(db, admin_user_id=admin.id, action="hide_comment", target_type="comment", target_id=comment.id, reason=reason, metadata={"status_before": status_before, "status_after": "hidden"}, request=request)
    db.commit()
    _dispatch_after_commit(deliveries, notifications)
    return {"message": "Comment hidden", "comment": _serialize_comment_admin(comment)}


@router.post("/comments/{comment_id}/restore")
def restore_comment(comment_id: int, payload: dict = Body(...), request: Request = None, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    reason = _admin_reason(payload)
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    status_before = "hidden" if comment.hidden_by_admin else "visible"
    comment.hidden_by_admin = False
    comment.hidden_by = None
    comment.hidden_reason = None
    comment.hidden_at = None
    author = db.query(User).filter(User.id == comment.user_id).first()
    deliveries, notifications = create_governance_notification(db, user=author, event_type="comment_restored", target_type="comment", target_id=comment.id, user_notice=_user_notice(payload), notify_channels=_notify_channels(payload))
    record_required_admin_audit(db, admin_user_id=admin.id, action="restore_comment", target_type="comment", target_id=comment.id, reason=reason, metadata={"status_before": status_before, "status_after": "visible"}, request=request)
    db.commit()
    _dispatch_after_commit(deliveries, notifications)
    return {"message": "Comment restored", "comment": _serialize_comment_admin(comment)}


@router.get("/reports")
def list_reports(q: str | None = Query(None), status: str | None = Query(None), target_type: str | None = Query(None), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    query = db.query(Report)
    if q:
        term = _like(q)
        numeric_id = _parse_int(q)
        query = query.outerjoin(User, User.id == Report.reporter_id)
        clauses = [Report.reason.like(term), User.username.like(term), User.email.like(term)]
        if numeric_id is not None:
            clauses.extend([Report.id == numeric_id, Report.target_id == numeric_id, Report.reporter_id == numeric_id])
        query = query.filter(or_(*clauses))
    if status:
        query = query.filter(Report.status == status)
    if target_type:
        query = query.filter(Report.target_type == target_type)
    return _page_result(query.order_by(Report.created_at.desc(), Report.id.desc()), page=page, page_size=page_size, serializer=lambda report: _serialize_report_admin(report, db))


@router.get("/reports/{report_id}")
def get_report_detail(report_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return _serialize_report_detail_admin(report, db)


def _apply_report_action(db: Session, report: Report, action_taken: str | None, admin: User, reason: str, payload: dict, request: Request | None):
    deliveries = []
    notifications = []
    target_notice = payload.get("target_notice") if isinstance(payload.get("target_notice"), str) else None
    if action_taken == "hide_post" and report.target_type == "post":
        post = db.query(Post).filter(Post.id == report.target_id).first()
        if post:
            post.hidden_by_admin = True
            post.hidden_by = admin.id
            post.hidden_at = datetime.utcnow()
            author = db.query(User).filter(User.id == post.user_id).first()
            next_deliveries, next_notifications = create_governance_notification(db, user=author, event_type="post_hidden", target_type="post", target_id=post.id, user_notice=target_notice, notify_channels=_notify_channels(payload))
            deliveries.extend(next_deliveries)
            notifications.extend(next_notifications)
            record_required_admin_audit(db, admin_user_id=admin.id, action="hide_post", target_type="post", target_id=post.id, reason=reason, metadata={"status_after": "hidden"}, request=request)
    elif action_taken == "hide_comment" and report.target_type == "comment":
        comment = db.query(Comment).filter(Comment.id == report.target_id).first()
        if comment:
            comment.hidden_by_admin = True
            comment.hidden_by = admin.id
            comment.hidden_reason = "report_resolved"
            comment.hidden_at = datetime.utcnow()
            author = db.query(User).filter(User.id == comment.user_id).first()
            next_deliveries, next_notifications = create_governance_notification(db, user=author, event_type="comment_hidden", target_type="comment", target_id=comment.id, user_notice=target_notice, notify_channels=_notify_channels(payload))
            deliveries.extend(next_deliveries)
            notifications.extend(next_notifications)
            record_required_admin_audit(db, admin_user_id=admin.id, action="hide_comment", target_type="comment", target_id=comment.id, reason=reason, metadata={"status_after": "hidden"}, request=request)
    elif action_taken == "deactivate_user" and report.target_type == "user":
        user = db.query(User).filter(User.id == report.target_id).first()
        if user and user.id != admin.id:
            user.is_active = False
            next_deliveries, next_notifications = create_governance_notification(db, user=user, event_type="account_deactivated", target_type="user", target_id=user.id, user_notice=target_notice, notify_channels=_notify_channels(payload))
            deliveries.extend(next_deliveries)
            notifications.extend(next_notifications)
            record_required_admin_audit(db, admin_user_id=admin.id, action="deactivate_user", target_type="user", target_id=user.id, reason=reason, metadata={"status_after": "inactive"}, request=request)
    return deliveries, notifications


@router.post("/reports/{report_id}/resolve")
def resolve_report(report_id: int, payload: dict = Body(...), request: Request = None, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    reason = _admin_reason(payload)
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    action_taken = payload.get("action_taken") or "none"
    deliveries, notifications = _apply_report_action(db, report, action_taken, admin, reason, payload, request)
    reporter = db.query(User).filter(User.id == report.reporter_id).first()
    reporter_notice = payload.get("reporter_notice") if isinstance(payload.get("reporter_notice"), str) else _user_notice(payload)
    reporter_deliveries, reporter_notifications = create_governance_notification(db, user=reporter, event_type="report_resolved", target_type="report", target_id=report.id, user_notice=reporter_notice, notify_channels=_notify_channels(payload))
    deliveries.extend(reporter_deliveries)
    notifications.extend(reporter_notifications)
    report.status = "resolved"
    report.resolution = "resolved"
    report.resolution_note = reason
    report.action_taken = action_taken
    report.resolved_by = admin.id
    report.resolved_at = datetime.utcnow()
    record_required_admin_audit(db, admin_user_id=admin.id, action="resolve_report", target_type="report", target_id=report.id, reason=reason, metadata={"report_status": "resolved", "status_after": "resolved"}, request=request)
    db.commit()
    _dispatch_after_commit(deliveries, notifications)
    return {"message": "Report resolved", "report": _serialize_report_admin(report)}


@router.post("/reports/{report_id}/reject")
def reject_report(report_id: int, payload: dict = Body(...), request: Request = None, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    reason = _admin_reason(payload)
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    reporter = db.query(User).filter(User.id == report.reporter_id).first()
    reporter_notice = payload.get("reporter_notice") if isinstance(payload.get("reporter_notice"), str) else _user_notice(payload)
    deliveries, notifications = create_governance_notification(db, user=reporter, event_type="report_rejected", target_type="report", target_id=report.id, user_notice=reporter_notice, notify_channels=_notify_channels(payload))
    report.status = "rejected"
    report.resolution = "rejected"
    report.resolution_note = reason
    report.action_taken = "none"
    report.resolved_by = admin.id
    report.resolved_at = datetime.utcnow()
    record_required_admin_audit(db, admin_user_id=admin.id, action="reject_report", target_type="report", target_id=report.id, reason=reason, metadata={"report_status": "rejected", "status_after": "rejected"}, request=request)
    db.commit()
    _dispatch_after_commit(deliveries, notifications)
    return {"message": "Report rejected", "report": _serialize_report_admin(report)}


@router.get("/identity-applications")
def list_identity_applications(q: str | None = Query(None), status: str | None = Query(None), role_id: int | None = Query(None), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    query = db.query(RoleApplication)
    if q:
        term = _like(q)
        numeric_id = _parse_int(q)
        query = query.outerjoin(User, User.id == RoleApplication.user_id)
        clauses = [
            RoleApplication.reason.like(term),
            RoleApplication.application_text.like(term),
            User.username.like(term),
            User.email.like(term),
        ]
        if numeric_id is not None:
            clauses.extend([RoleApplication.id == numeric_id, RoleApplication.user_id == numeric_id])
        query = query.filter(or_(*clauses))
    if status:
        query = query.filter(RoleApplication.status == status)
    if role_id is not None:
        query = query.filter(RoleApplication.role_id == role_id)
    return _page_result(query.order_by(RoleApplication.created_at.desc(), RoleApplication.id.desc()), page=page, page_size=page_size, serializer=_serialize_identity_application)


@router.get("/identity-applications/{application_id}")
def get_identity_application(application_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    application = db.query(RoleApplication).filter(RoleApplication.id == application_id).first()
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    return _serialize_identity_application(application)


def _sync_identity_role(db: Session, application: RoleApplication, status: str) -> None:
    existing = db.query(UserRole).filter(
        UserRole.user_id == application.user_id,
        UserRole.role_id == application.role_id,
    ).first()
    if status == "verified" and not existing:
        db.add(UserRole(user_id=application.user_id, role_id=application.role_id))
    elif status in {"rejected", "suspended"} and existing:
        db.delete(existing)



def _review_identity_application(db: Session, application_id: int, admin: User, status: str, payload: dict, request: Request | None):
    application = db.query(RoleApplication).filter(RoleApplication.id == application_id).first()
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    reason = _admin_reason(payload) if status in {"rejected", "suspended"} else (payload.get("admin_reason") or payload.get("reason"))
    reason = reason.strip()[:500] if isinstance(reason, str) and reason.strip() else None
    status_before = application.status
    application.status = status
    application.reviewer_id = admin.id
    application.reviewed_at = datetime.utcnow()
    if reason is not None:
        application.review_comment = reason
    _sync_identity_role(db, application, status)
    applicant = db.query(User).filter(User.id == application.user_id).first()
    deliveries, notifications = create_governance_notification(
        db,
        user=applicant,
        event_type=f"identity_{status}",
        target_type="identity_application",
        target_id=application.id,
        user_notice=_user_notice(payload),
        notify_channels=_notify_channels(payload),
    )
    record_required_admin_audit(db, admin_user_id=admin.id, action=f"identity_{status}", target_type="identity_application", target_id=application.id, reason=reason, metadata={"status_before": status_before, "status_after": status}, request=request)
    db.commit()
    _dispatch_after_commit(deliveries, notifications)
    return {"message": f"Application {status}", "application": _serialize_identity_application(application)}


@router.post("/identity-applications/{application_id}/approve")
def approve_identity_application(application_id: int, payload: dict = Body(default={}), request: Request = None, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _review_identity_application(db, application_id, admin, "verified", payload, request)


@router.post("/identity-applications/{application_id}/reject")
def reject_identity_application(application_id: int, payload: dict = Body(...), request: Request = None, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _review_identity_application(db, application_id, admin, "rejected", payload, request)


@router.post("/identity-applications/{application_id}/suspend")
def suspend_identity_application(application_id: int, payload: dict = Body(...), request: Request = None, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _review_identity_application(db, application_id, admin, "suspended", payload, request)


@router.get("/moderation/events")
def list_moderation_events(q: str | None = Query(None), content_type: str | None = Query(None), decision: str | None = Query(None), provider: str | None = Query(None), target_type: str | None = Query(None), actor_user_id: int | None = Query(None), page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    query = db.query(ModerationEvent)
    if q:
        term = _like(q)
        query = query.filter(or_(
            ModerationEvent.provider.like(term),
            ModerationEvent.route_key.like(term),
            ModerationEvent.target_type.like(term),
            ModerationEvent.target_id.like(term),
            ModerationEvent.error_code.like(term),
            ModerationEvent.label.like(term),
            ModerationEvent.category.like(term),
        ))
    if content_type:
        query = query.filter(ModerationEvent.content_type == content_type)
    if decision:
        query = query.filter(ModerationEvent.decision == decision)
    if provider:
        query = query.filter(ModerationEvent.provider == provider)
    if target_type:
        query = query.filter(ModerationEvent.target_type == target_type)
    if actor_user_id is not None:
        query = query.filter(ModerationEvent.actor_user_id == actor_user_id)
    return _page_result(query.order_by(ModerationEvent.created_at.desc(), ModerationEvent.id.desc()), page=page, page_size=page_size, serializer=_serialize_moderation_event)


@router.get("/moderation/events/{event_id}")
def get_moderation_event(event_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    event = db.query(ModerationEvent).filter(ModerationEvent.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Moderation event not found")
    return _serialize_moderation_event(event)
