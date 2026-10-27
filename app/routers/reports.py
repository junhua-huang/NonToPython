"""
举报路由 - FastAPI 重构版
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Body, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import User, Report, Notification
from app.services.moderation_errors import AppContractError, ModerationUnavailable, to_http_exception
from app.services.moderation_service import moderation_service
from app.services.moderation_types import ModerationContext
from app.services.notification_messages import REPORT_RECEIVED_TITLE, REPORT_RECEIVED_CONTENT
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)
router = APIRouter()

_REPORT_MODERATION_ROUTES = (
    "POST /api/reports",
    "POST /api/reports/post",
    "POST /api/reports/comment",
    "POST /api/reports/user",
)


def _moderate_report_reason(route_key: str, reason: object, *, actor_user_id: int):
    if route_key not in _REPORT_MODERATION_ROUTES:
        raise to_http_exception(ModerationUnavailable(ValueError("unknown moderation route")))
    if reason is None:
        return
    if not isinstance(reason, str):
        raise to_http_exception(ModerationUnavailable(TypeError("moderation field must be a string")))
    if not reason.strip():
        return
    try:
        moderation_service.moderate_fields(
            {"reason": reason},
            ModerationContext(
                target_type="report_reason",
                actor_user_id=actor_user_id,
                is_public=False,
            ),
        )
    except AppContractError as error:
        raise to_http_exception(error) from None
    except Exception as exc:
        raise to_http_exception(ModerationUnavailable(exc)) from None


def _handle_report(
    route_key: str,
    report_type: str,
    target_id: int,
    reason: str,
    user: User,
    db: Session,
) -> dict:
    """内部复用：提交举报的公共逻辑"""
    if not report_type:
        raise HTTPException(status_code=400, detail="Report type is required")
    if report_type not in ("post", "comment", "user"):
        raise HTTPException(status_code=400, detail="Invalid report type")
    if not target_id:
        raise HTTPException(status_code=400, detail="Target ID is required")
    _moderate_report_reason(route_key, reason, actor_user_id=user.id)

    reporter_id = user.id
    try:
        # Serialize submissions from the same reporter. Locking reads also see
        # a concurrent commit under MySQL REPEATABLE READ, avoiding duplicate
        # reports/receipts when a client retries an in-flight request.
        reporter = db.query(User).filter(User.id == reporter_id).with_for_update().first()
        if reporter is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        existing = db.query(Report).filter(
            Report.reporter_id == reporter_id,
            Report.target_type == report_type,
            Report.target_id == target_id,
        ).with_for_update().first()
        if existing:
            result = {"message": "You have already reported this target", "report": existing.to_dict()}
            db.rollback()  # Release the read locks without creating another receipt.
            return result

        report = Report(
            reporter_id=reporter_id,
            target_type=report_type,
            target_id=target_id,
            reason=reason,
        )
        db.add(report)
        db.flush()
        notification = Notification(
            user_id=reporter_id,
            sender_id=None,
            notification_type="report_received",
            title=REPORT_RECEIVED_TITLE,
            content=REPORT_RECEIVED_CONTENT,
            related_id=report.id,
            related_type="report",
        )
        db.add(notification)
        db.flush()
        # Capture public response data before commit expires ORM attributes.
        report_dict = report.to_dict()
        notification_dict = notification.to_dict()
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(
            "Submit report failed user_id=%s target_type=%s error_type=%s",
            reporter_id,
            report_type,
            type(e).__name__,
        )
        raise HTTPException(status_code=500, detail="An error occurred while submitting the report")

    # The in-app receipt is durable regardless of push preferences/provider
    # availability. Never alert the reported user or expose the report reason.
    NotificationService.dispatch_notification(reporter_id, notification_dict)
    return {
        "message": "Report submitted successfully",
        "report": report_dict,
        "notification": notification_dict,
    }


@router.post("")
def submit_report(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """提交举报（统一入口：{type, target_id, reason}）"""
    return _handle_report(
        route_key="POST /api/reports",
        report_type=payload.get("type", ""),
        target_id=payload.get("target_id"),
        reason=payload.get("reason", ""),
        user=user,
        db=db,
    )


@router.post("/post")
def report_post(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """举报帖子（别名：前端 POST /reports/post {post_id, reason}）"""
    return _handle_report(
        route_key="POST /api/reports/post",
        report_type="post",
        target_id=payload.get("post_id"),
        reason=payload.get("reason", ""),
        user=user,
        db=db,
    )


@router.post("/comment")
def report_comment(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """举报评论（别名：前端 POST /reports/comment {comment_id, reason}）"""
    return _handle_report(
        route_key="POST /api/reports/comment",
        report_type="comment",
        target_id=payload.get("comment_id"),
        reason=payload.get("reason", ""),
        user=user,
        db=db,
    )


@router.post("/user")
def report_user(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """举报用户（别名：前端 POST /reports/user {user_id, reason}）"""
    return _handle_report(
        route_key="POST /api/reports/user",
        report_type="user",
        target_id=payload.get("user_id"),
        reason=payload.get("reason", ""),
        user=user,
        db=db,
    )


@router.get("/")
def get_my_reports(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取我的举报记录"""
    reports_query = db.query(Report).filter(Report.reporter_id == user.id).order_by(Report.created_at.desc())
    total = reports_query.count()
    reports = reports_query.offset((page - 1) * per_page).limit(per_page).all()
    pages = (total + per_page - 1) // per_page if total > 0 else 0

    return {
        "reports": [r.to_dict() for r in reports],
        "total": total,
        "pages": pages,
        "current_page": page,
        "per_page": per_page,
    }


@router.get("/check")
def check_report_status(
    report_type: str = Query(...),
    target_id: int = Query(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """检查是否已举报"""
    existing = db.query(Report).filter(
        Report.reporter_id == user.id,
        Report.target_type == report_type,
        Report.target_id == target_id,
    ).first()
    return {"has_reported": existing is not None}
