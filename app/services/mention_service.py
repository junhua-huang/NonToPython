"""Mention delivery uses an isolated session after the content transaction commits."""
import logging
import re

from app.database import SessionLocal
from app.models.models import Notification, User
from app.services.block_service import has_block_between
from app.services.notification_service import NotificationService
from app.services.post_visibility_service import can_view_post

logger = logging.getLogger(__name__)


class MentionService:
    @staticmethod
    def extract_mentions(content):
        # Keep first appearance order; no username, content or tokens in logs.
        return list(dict.fromkeys(re.findall(r'@([\w\u4e00-\u9fa5-]+)', content or "")))

    @staticmethod
    def find_users_by_usernames(usernames, db):
        if not usernames:
            return []
        return db.query(User).filter(User.username.in_(usernames), User.is_active.is_(True)).all()

    @staticmethod
    def process_mentions(content, author_id, post_id=None, comment_id=None):
        """Best-effort side effect; never turn committed content into an HTTP failure."""
        usernames = MentionService.extract_mentions(content)
        if not usernames or not post_id:
            return []
        from app.models.models import Post
        with SessionLocal() as db:
            try:
                post = db.query(Post).filter(Post.id == post_id).first()
                author = db.query(User).filter(User.id == author_id).first()
                if post is None or author is None:
                    return []
                kind, target = ("comment", comment_id) if comment_id else ("post", post_id)
                users, payloads = [], []
                for user in MentionService.find_users_by_usernames(usernames, db):
                    if user.id == author_id or has_block_between(db, user.id, author_id) or not can_view_post(db, post, user.id):
                        continue
                    existing = db.query(Notification.id).filter(
                        Notification.user_id == user.id, Notification.sender_id == author_id,
                        Notification.notification_type == "mention", Notification.related_type == kind,
                        Notification.related_id == target,
                    ).first()
                    if existing:
                        continue
                    notice = Notification(
                        user_id=user.id, sender_id=author_id, notification_type="mention",
                        title=f"{author.username} 在{'评论' if comment_id else '动态'}中提到了你",
                        content=(content[:100] + ('…' if len(content) > 100 else '')),
                        related_id=target, related_type=kind,
                    )
                    db.add(notice)
                    db.flush()
                    payloads.append((user.id, notice.to_dict()))
                    users.append(user)
                db.commit()
            except Exception as error:
                db.rollback()
                logger.warning("mention_processing_failed post_id=%s error_type=%s", post_id,
                               NotificationService._safe_exception_type(error))
                return []
            for user_id, payload in payloads:
                NotificationService.dispatch_notification(user_id, payload)
            return users
