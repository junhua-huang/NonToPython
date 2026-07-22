"""
举报路由 - FastAPI 重构版
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Body, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import User, Report
from app.services.moderation_route_helpers import moderate_route_fields
from app.services.moderation_service import moderation_service

logger = logging.getLogger(__name__)
router = APIRouter()


def _handle_report(
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

    existing = db.query(Report).filter(
        Report.reporter_id == user.id,
        Report.target_type == report_type,
        Report.target_id == target_id,
    ).first()
    if existing:
        return {"message": "You have already reported this target", "report": existing.to_dict()}

    report = Report(
        reporter_id=user.id,
        target_type=report_type,
        target_id=target_id,
        reason=reason,
    )
    try:
        db.add(report)
        db.commit()
        return {"message": "Report submitted successfully", "report": report.to_dict()}
    except Exception as e:
        db.rollback()
        logger.error(
            "Submit report failed user_id=%s target_type=%s error_type=%s",
            user.id,
            report_type,
            type(e).__name__,
        )
        raise HTTPException(status_code=500, detail="An error occurred while submitting the report")


@router.post("")
def submit_report(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """提交举报（统一入口：{type, target_id, reason}）"""
    moderate_route_fields(
        moderation_service,
        "POST /api/reports",
        payload,
        actor_user_id=user.id,
        is_public=True,
    )
    return _handle_report(
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
    moderate_route_fields(
        moderation_service,
        "POST /api/reports/post",
        payload,
        actor_user_id=user.id,
        is_public=True,
    )
    return _handle_report(
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
    moderate_route_fields(
        moderation_service,
        "POST /api/reports/comment",
        payload,
        actor_user_id=user.id,
        is_public=True,
    )
    return _handle_report(
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
    moderate_route_fields(
        moderation_service,
        "POST /api/reports/user",
        payload,
        actor_user_id=user.id,
        is_public=True,
    )
    return _handle_report(
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