"""
通知路由 - FastAPI 重构版
"""
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import User, Notification
from app.ws_manager import ws_manager

router = APIRouter()


@router.get("/")
def get_notifications(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    unread_only: bool = Query(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取当前用户的通知列表"""
    # 屏蔽检查：过滤已屏蔽用户发送的通知
    from app.ws_manager import ws_manager
    blocked_ids = ws_manager.get_blocked_user_ids(user.id)

    query = db.query(Notification).filter(Notification.user_id == user.id)
    if blocked_ids:
        query = query.filter(or_(
            Notification.sender_id == None,
            ~Notification.sender_id.in_(blocked_ids),
        ))
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

    unread_count = (
        db.query(Notification)
        .filter(Notification.user_id == user.id, Notification.is_read == False)
        .count()
    )

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
    from app.ws_manager import ws_manager
    blocked_ids = ws_manager.get_blocked_user_ids(user.id)
    query = db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read == False)
    if blocked_ids:
        query = query.filter(or_(
            Notification.sender_id == None,
            ~Notification.sender_id.in_(blocked_ids),
        ))
    count = query.count()
    return {"unread_count": count}


@router.post("/{notification_id}/read")
async def mark_as_read(
    notification_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """标记单个通知为已读"""
    notification = db.query(Notification).filter(Notification.id == notification_id).first()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    if notification.user_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    notification.is_read = True
    try:
        db.commit()

        # --- WebSocket 实时推送 ---
        unread_count = (
            db.query(Notification)
            .filter(Notification.user_id == user.id, Notification.is_read == False)
            .count()
        )
        await ws_manager.send_with_seq(user.id, "notifications_read", {
            "notification_ids": [notification_id],
            "unread_count": unread_count,
        })

        return {"message": "Notification marked as read", "notification": notification.to_dict(), "unread_count": unread_count}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mark-all-read")
async def mark_all_as_read(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """标记所有通知为已读"""
    try:
        db.query(Notification).filter(
            Notification.user_id == user.id, Notification.is_read == False
        ).update({"is_read": True})
        db.commit()

        # --- WebSocket 实时推送 ---
        await ws_manager.send_with_seq(user.id, "notifications_read", {
            "unread_count": 0,
        })

        return {"message": "All notifications marked as read", "unread_count": 0}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{notification_id}")
def delete_notification(
    notification_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除通知"""
    notification = db.query(Notification).filter(Notification.id == notification_id).first()
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    if notification.user_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    try:
        db.delete(notification)
        db.commit()
        return {"message": "Notification deleted successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/clear-all")
def clear_all_notifications(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """清空所有通知"""
    try:
        db.query(Notification).filter(Notification.user_id == user.id).delete()
        db.commit()
        return {"message": "All notifications cleared"}
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
