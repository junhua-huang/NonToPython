"""
通知服务 - FastAPI 重构版
"""
import asyncio
import logging
import re
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.models import Notification, User
from app.services.aliyun_push_service import AliyunPushService
from app.services.block_service import has_block_between
from app.services.notification_query_service import (
    is_notification_visible,
    visible_unread_count,
)

logger = logging.getLogger(__name__)


class NotificationService:

    SAFE_EXCEPTION_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

    @staticmethod
    def _safe_exception_type(exc: BaseException) -> str:
        name = exc.__class__.__name__
        return name if NotificationService.SAFE_EXCEPTION_TYPE.fullmatch(name) else "Exception"

    @staticmethod
    def _get_session() -> Session:
        return SessionLocal()

    @staticmethod
    def _push_new_notification(user_id: int, notification_dict: dict):
        """从 HTTP 请求线程安全地推送 WebSocket 通知。"""
        from app.ws_manager import ws_manager

        async def _push():
            db = SessionLocal()
            try:
                notification_id = notification_dict.get("id")
                if notification_id is None or not is_notification_visible(
                    db, user_id, notification_id
                ):
                    return

                sender_id = notification_dict.get("sender_id")

                # 查询发送者信息
                sender = None
                if sender_id:
                    s = db.query(User).filter(User.id == sender_id).first()
                    if s:
                        sender = {"username": s.username, "avatar_url": s.avatar_url}

                unread_count = visible_unread_count(db, user_id)
                notification_payload = dict(notification_dict)
                notification_payload["sender"] = sender

                await ws_manager.send_with_seq(user_id, "new_notification", {
                    "notification": notification_payload,
                    "unread_count": unread_count,
                })
            except Exception as exc:
                logger.warning(
                    "[NOTIFY WS] delivery failed uid=%s error_code=%s exception_type=%s",
                    user_id,
                    "WS_DELIVERY_ERROR",
                    NotificationService._safe_exception_type(exc),
                )
            finally:
                db.close()

        ws_manager.schedule_push(_push())

    @staticmethod
    def create_notification(user_id, notification_type, title, content,
                           sender_id=None, related_id=None, related_type=None, db=None):
        own_db = db is None
        if own_db:
            db = NotificationService._get_session()
        try:
            if sender_id is not None and has_block_between(db, user_id, sender_id):
                # Match this method's existing transaction boundary without
                # persisting or scheduling sender-backed hidden notifications.
                db.commit()
                return None
            try:
                notification = Notification(
                    user_id=user_id,
                    sender_id=sender_id,
                    notification_type=notification_type,
                    title=title,
                    content=content,
                    related_id=related_id,
                    related_type=related_type
                )
                db.add(notification)
                db.flush()
                notification_dict = notification.to_dict()
                db.commit()
            except Exception as persistence_error:
                db.rollback()
                logger.error(
                    "[NOTIFY DB] notification persistence failed error_code=%s exception_type=%s",
                    "DB_PERSISTENCE_ERROR",
                    NotificationService._safe_exception_type(persistence_error),
                )
                return None

            # --- WebSocket push (best effort) ---
            try:
                NotificationService._push_new_notification(user_id, notification_dict)
            except Exception as ws_error:
                logger.warning(
                    "[NOTIFY WS] scheduling failed uid=%s error_code=%s exception_type=%s",
                    user_id,
                    "WS_SCHEDULING_ERROR",
                    NotificationService._safe_exception_type(ws_error),
                )

            # --- Mobile push (best effort; respects notify_push/block checks internally) ---
            try:
                AliyunPushService.schedule_notification_push(user_id, notification_dict)
            except Exception as push_error:
                logger.warning(
                    "[NOTIFY PUSH] scheduling failed uid=%s error_code=%s exception_type=%s",
                    user_id,
                    "PUSH_SCHEDULING_ERROR",
                    NotificationService._safe_exception_type(push_error),
                )

            return notification
        finally:
            if own_db:
                db.close()

    @staticmethod
    def notify_like(post_owner_id, liker_id, post_id, db=None):
        if post_owner_id == liker_id:
            return
        own_db = db is None
        if own_db:
            db = NotificationService._get_session()
        try:
            liker = db.query(User).filter(User.id == liker_id).first()
            if not liker:
                return
            title = f"{liker.username} 赞了你的帖子"
            content = f"{liker.username} 赞了你的帖子"
            return NotificationService.create_notification(
                user_id=post_owner_id,
                notification_type="like",
                title=title,
                content=content,
                sender_id=liker_id,
                related_id=post_id,
                related_type='post',
                db=db,
            )
        finally:
            if own_db:
                db.close()

    @staticmethod
    def notify_comment(post_owner_id, commenter_id, post_id, comment_content, db=None):
        if post_owner_id == commenter_id:
            return
        own_db = db is None
        if own_db:
            db = NotificationService._get_session()
        try:
            commenter = db.query(User).filter(User.id == commenter_id).first()
            if not commenter:
                return
            preview = comment_content[:50] + '...' if len(comment_content) > 50 else comment_content
            title = f"{commenter.username} 评论了你的帖子"
            content = f"{commenter.username}: {preview}"
            return NotificationService.create_notification(
                user_id=post_owner_id,
                notification_type="comment",
                title=title,
                content=content,
                sender_id=commenter_id,
                related_id=post_id,
                related_type='post',
                db=db,
            )
        finally:
            if own_db:
                db.close()

    @staticmethod
    def notify_friend_request(receiver_id, sender_id, db=None):
        own_db = db is None
        if own_db:
            db = NotificationService._get_session()
        try:
            sender = db.query(User).filter(User.id == sender_id).first()
            if not sender:
                return
            title = f"{sender.username} 想加你为好友"
            content = f"{sender.username} 发送了好友请求"
            return NotificationService.create_notification(
                user_id=receiver_id,
                notification_type="friend_request",
                title=title,
                content=content,
                sender_id=sender_id,
                related_type='friendship',
                db=db,
            )
        finally:
            if own_db:
                db.close()

    @staticmethod
    def notify_friend_accepted(sender_id, receiver_id, db=None):
        own_db = db is None
        if own_db:
            db = NotificationService._get_session()
        try:
            receiver = db.query(User).filter(User.id == receiver_id).first()
            if not receiver:
                return
            title = f"{receiver.username} 接受了你的好友请求"
            content = f"{receiver.username} 现在是你的好友了"
            return NotificationService.create_notification(
                user_id=sender_id,
                notification_type="friend_accept",
                title=title,
                content=content,
                sender_id=receiver_id,
                related_id=receiver_id,  # 对方的 user_id，前端可据此跳转聊天
                related_type='friendship',
                db=db,
            )
        finally:
            if own_db:
                db.close()

    @staticmethod
    def notify_message(receiver_id, sender_id, message_content, conversation_id, db=None):
        if receiver_id == sender_id:
            return
        own_db = db is None
        if own_db:
            db = NotificationService._get_session()
        try:
            sender = db.query(User).filter(User.id == sender_id).first()
            if not sender:
                return
            preview = message_content[:50] + '...' if len(message_content) > 50 else message_content
            title = f"来自 {sender.username} 的新消息"
            content = preview
            return NotificationService.create_notification(
                user_id=receiver_id,
                notification_type="message",
                title=title,
                content=content,
                sender_id=sender_id,
                related_id=conversation_id,
                related_type='conversation',
                db=db,
            )
        finally:
            if own_db:
                db.close()

    @staticmethod
    def notify_mention(mentioned_user_id, mentioner_id, post_id, context, db=None):
        if mentioned_user_id == mentioner_id:
            return
        own_db = db is None
        if own_db:
            db = NotificationService._get_session()
        try:
            mentioner = db.query(User).filter(User.id == mentioner_id).first()
            if not mentioner:
                return
            title = f"{mentioner.username} 在帖子中提到了你"
            content = context[:100] if context else f"{mentioner.username} 提到了你"
            return NotificationService.create_notification(
                user_id=mentioned_user_id,
                notification_type="mention",
                title=title,
                content=content,
                sender_id=mentioner_id,
                related_id=post_id,
                related_type='post',
                db=db,
            )
        finally:
            if own_db:
                db.close()
