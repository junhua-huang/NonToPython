"""
通知服务 - FastAPI 重构版
"""
import asyncio
import logging
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.models import Notification, User

logger = logging.getLogger(__name__)


class NotificationService:

    @staticmethod
    def _get_session() -> Session:
        return SessionLocal()

    @staticmethod
    def _push_new_notification(user_id: int, notification: Notification):
        """从 HTTP 请求线程安全地推送 WebSocket 通知"""
        from app.ws_manager import ws_manager

        async def _push():
            db = SessionLocal()
            try:
                sender_id = notification.sender_id if hasattr(notification, "sender_id") else None

                # 屏蔽检查：接收者屏蔽了发送者，则不推送
                if sender_id and user_id != sender_id:
                    if sender_id in ws_manager.get_blocked_user_ids(user_id):
                        return

                # 查询发送者信息
                sender = None
                if sender_id:
                    s = db.query(User).filter(User.id == sender_id).first()
                    if s:
                        sender = {"username": s.username, "avatar_url": s.avatar_url}

                unread_count = (
                    db.query(Notification)
                    .filter(
                        Notification.user_id == user_id,
                        Notification.is_read == False,
                    )
                    .count()
                )
                notification_dict = notification.to_dict() if hasattr(notification, "to_dict") else {}
                notification_dict["sender"] = sender

                await ws_manager.send_with_seq(user_id, "new_notification", {
                    "notification": notification_dict,
                    "unread_count": unread_count,
                })
            except Exception as e:
                logger.warning(f"[NOTIFY PUSH] failed uid={user_id}: {e}", exc_info=True)
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
            db.commit()
            db.refresh(notification)

            # --- WebSocket 推送 ---
            NotificationService._push_new_notification(user_id, notification)

            return notification
        except Exception as e:
            db.rollback()
            print(f"Error creating notification: {e}")
            return None
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
        finally:
            if own_db:
                db.close()

        NotificationService.create_notification(
            user_id=post_owner_id,
            notification_type="like",
            title=title,
            content=content,
            sender_id=liker_id,
            related_id=post_id,
            related_type='post',
            db=db,
        )

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
        finally:
            if own_db:
                db.close()

        NotificationService.create_notification(
            user_id=post_owner_id,
            notification_type="comment",
            title=title,
            content=content,
            sender_id=commenter_id,
            related_id=post_id,
            related_type='post',
            db=db,
        )

    @staticmethod
    def notify_friend_request(receiver_id, sender_id):
        db = NotificationService._get_session()
        try:
            sender = db.query(User).filter(User.id == sender_id).first()
            if not sender:
                return
            title = f"{sender.username} 想加你为好友"
            content = f"{sender.username} 发送了好友请求"
        finally:
            db.close()

        NotificationService.create_notification(
            user_id=receiver_id,
            notification_type="friend_request",
            title=title,
            content=content,
            sender_id=sender_id,
            related_type='friendship'
        )

    @staticmethod
    def notify_friend_accepted(sender_id, receiver_id):
        db = NotificationService._get_session()
        try:
            receiver = db.query(User).filter(User.id == receiver_id).first()
            if not receiver:
                return
            title = f"{receiver.username} 接受了你的好友请求"
            content = f"{receiver.username} 现在是你的好友了"
        finally:
            db.close()

        NotificationService.create_notification(
            user_id=sender_id,
            notification_type="friend_accept",
            title=title,
            content=content,
            sender_id=receiver_id,
            related_id=receiver_id,  # 对方的 user_id，前端可据此跳转聊天
            related_type='friendship'
        )

    @staticmethod
    def notify_message(receiver_id, sender_id, message_content, conversation_id):
        if receiver_id == sender_id:
            return
        db = NotificationService._get_session()
        try:
            sender = db.query(User).filter(User.id == sender_id).first()
            if not sender:
                return
            preview = message_content[:50] + '...' if len(message_content) > 50 else message_content
            title = f"来自 {sender.username} 的新消息"
            content = preview
        finally:
            db.close()

        NotificationService.create_notification(
            user_id=receiver_id,
            notification_type="message",
            title=title,
            content=content,
            sender_id=sender_id,
            related_id=conversation_id,
            related_type='conversation'
        )

    @staticmethod
    def notify_mention(mentioned_user_id, mentioner_id, post_id, context):
        if mentioned_user_id == mentioner_id:
            return
        db = NotificationService._get_session()
        try:
            mentioner = db.query(User).filter(User.id == mentioner_id).first()
            if not mentioner:
                return
            title = f"{mentioner.username} 在帖子中提到了你"
            content = context[:100] if context else f"{mentioner.username} 提到了你"
        finally:
            db.close()

        NotificationService.create_notification(
            user_id=mentioned_user_id,
            notification_type="mention",
            title=title,
            content=content,
            sender_id=mentioner_id,
            related_id=post_id,
            related_type='post'
        )