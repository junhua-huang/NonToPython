"""
通知路由 - FastAPI 重构版
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Body, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import User, Notification
from app.services.notification_query_service import (
    visible_notification_query,
    visible_unread_count,
)
from app.ws_manager import ws_manager

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/")
def get_notifications(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    unread_only: bool = Query(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取当前用户的通知列表"""
    query = visible_notification_query(db, user.id)
    if unread_only:
        query = query.filter(Notification.is_read == False)
    query = query.order_by(Notification.created_at.desc())

    total = query.count()
    notifications = query.offset((page - 1) * per_page).limit(per_page).all()
    pages = (total + per_page - 1) // per_page if total > 0 else 0

    # 批量查询发送者信息（避免 N+1）
    sender_ids = list({n.sender_id for n in notifications if n.sender_id})
    senders = {}
    if sender_ids:
        sender_users = db.query(User).filter(User.id.in_(sender_ids)).all()
        senders = {
            u.id: {
                "username": u.username,
                "avatar_url": u.avatar_url,
            }
            for u in sender_users
        }

    unread_count = visible_unread_count(db, user.id)

    return {
        "notifications": [
            {**n.to_dict(), "sender": senders.get(n.sender_id) if n.sender_id else None}
            for n in notifications
        ],
        "total": total,
        "pages": pages,
        "current_page": page,
        "per_page": per_page,
        "has_more": page < pages,
        "unread_count": unread_count,
    }


@router.get("/unread-count")
def get_unread_count(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取未读通知数量"""
    return {"unread_count": visible_unread_count(db, user.id)}


@router.post("/{notification_id}/read")
async def mark_as_read(
    notification_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """标记单个通知为已读"""
    notification = visible_notification_query(db, user.id).filter(
        Notification.id == notification_id
    ).first()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    notification.is_read = True
    try:
        db.flush()
        unread_count = visible_unread_count(db, user.id)
        notification_payload = notification.to_dict()
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    try:
        await ws_manager.send_with_seq(user.id, "notifications_read", {
            "notification_ids": [notification_id],
            "unread_count": unread_count,
        })
    except Exception:
        logger.warning(
            "Failed to deliver notifications_read event uid=%s notification_id=%s",
            user.id,
            notification_id,
            exc_info=True,
        )

    return {
        "message": "Notification marked as read",
        "notification": notification_payload,
        "unread_count": unread_count,
    }


@router.post("/mark-all-read")
async def mark_all_as_read(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """标记所有通知为已读"""
    try:
        visible_notification_query(db, user.id).filter(
            Notification.is_read == False
        ).update({"is_read": True}, synchronize_session=False)
        unread_count = visible_unread_count(db, user.id)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    try:
        await ws_manager.send_with_seq(user.id, "notifications_read", {
            "unread_count": unread_count,
        })
    except Exception:
        logger.warning(
            "Failed to deliver notifications_read event uid=%s after mark-all",
            user.id,
            exc_info=True,
        )

    return {"message": "All notifications marked as read", "unread_count": unread_count}


@router.delete("/clear-all")
def clear_all_notifications(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """清空当前可见通知，保留被屏蔽发送者的隐藏通知。"""
    try:
        visible_notification_query(db, user.id).delete(synchronize_session=False)
        db.commit()
        return {"message": "All notifications cleared"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{notification_id}")
def delete_notification(
    notification_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除当前可见通知。"""
    notification = visible_notification_query(db, user.id).filter(
        Notification.id == notification_id
    ).first()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")

    try:
        db.delete(notification)
        db.commit()
        return {"message": "Notification deleted successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/settings")
def get_notification_settings(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取通知偏好设置"""
    target = db.query(User).filter(User.id == user.id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "settings": {
            "notify_push": target.notify_push,
            "notify_message": target.notify_message,
            "notify_sound": target.notify_sound,
        }
    }


@router.put("/settings")
def update_notification_settings(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新通知偏好设置"""
    target = db.query(User).filter(User.id == user.id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    if "notify_push" in payload:
        target.notify_push = bool(payload["notify_push"])
    if "notify_message" in payload:
        target.notify_message = bool(payload["notify_message"])
    if "notify_sound" in payload:
        target.notify_sound = bool(payload["notify_sound"])

    try:
        db.commit()
        return {
            "message": "Notification settings updated successfully",
            "settings": {
                "notify_push": target.notify_push,
                "notify_message": target.notify_message,
                "notify_sound": target.notify_sound,
            },
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
