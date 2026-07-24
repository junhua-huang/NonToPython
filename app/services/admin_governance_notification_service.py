from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from html import escape
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.models import Notification, NotificationDelivery, User
from app.services.aliyun_push_service import AliyunPushService
from app.services.email_service import EmailService
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

_ALLOWED_CHANNELS = {"in_app", "push", "email"}
_REQUIRED_EMAIL_EVENTS = {
    "account_deactivated",
    "account_reactivated",
    "identity_verified",
    "identity_rejected",
    "identity_suspended",
}
_FORBIDDEN_NOTICE_FRAGMENTS = {
    "secret",
    "token",
    "signed_url",
    "cos_key",
    "raw_response",
    "stacktrace",
    "traceback",
    "exception",
    "secretid",
    "secretkey",
}
_DEFAULT_TITLES = {
    "account_deactivated": "账号已被停用",
    "account_reactivated": "账号已恢复",
    "post_hidden": "帖子已被隐藏",
    "post_restored": "帖子已恢复展示",
    "comment_hidden": "评论已被隐藏",
    "comment_restored": "评论已恢复展示",
    "report_resolved": "举报已处理",
    "report_rejected": "举报已驳回",
    "identity_verified": "身份认证已通过",
    "identity_rejected": "身份认证未通过",
    "identity_suspended": "身份认证已被暂停",
}
_DEFAULT_NOTICES = {
    "account_deactivated": "你的账号因违反社区规则已被停用。",
    "account_reactivated": "你的账号已恢复正常使用。",
    "post_hidden": "你的帖子因违反社区规则已被管理员隐藏。",
    "post_restored": "你的帖子已恢复展示。",
    "comment_hidden": "你的评论因违反社区规则已被管理员隐藏。",
    "comment_restored": "你的评论已恢复展示。",
    "report_resolved": "你提交的举报已处理，感谢你的反馈。",
    "report_rejected": "你提交的举报未被采纳。",
    "identity_verified": "你的身份认证已通过。",
    "identity_rejected": "你的身份认证未通过，请根据提示补充资料后重新提交。",
    "identity_suspended": "你的身份认证已被暂停。",
}
_EMAIL_SUBJECTS = {
    "account_deactivated": "【NonTo】你的账号已被停用",
    "account_reactivated": "【NonTo】你的账号已恢复",
    "identity_verified": "【NonTo】你的身份认证已通过",
    "identity_rejected": "【NonTo】你的身份认证未通过",
    "identity_suspended": "【NonTo】你的身份认证已被暂停",
}


def sanitize_user_notice(value: str | None, event_type: str) -> str:
    text = value.strip() if isinstance(value, str) and value.strip() else _DEFAULT_NOTICES.get(event_type, "你有一条平台治理通知。")
    text = text[:500]
    lowered = text.lower()
    if any(fragment in lowered for fragment in _FORBIDDEN_NOTICE_FRAGMENTS):
        return _DEFAULT_NOTICES.get(event_type, "你有一条平台治理通知。")
    return text


def normalize_notify_channels(channels: Iterable[str] | None, *, event_type: str) -> set[str]:
    if channels is None:
        normalized = {"in_app", "push"}
    else:
        normalized = {str(channel).strip().lower() for channel in channels if str(channel).strip().lower() in _ALLOWED_CHANNELS}
    if not normalized:
        normalized = {"in_app"}
    if event_type in _REQUIRED_EMAIL_EVENTS:
        normalized.update({"in_app", "push", "email"})
    return normalized


def _email_body(title: str, notice: str) -> str:
    return f"""\
<div style=\"font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:520px;margin:0 auto;padding:24px;\">
  <h2 style=\"color:#1677ff;margin:0 0 16px;\">南图 NonTo</h2>
  <h3 style=\"color:#1f2329;margin:0 0 16px;\">{escape(title)}</h3>
  <p style=\"color:#333;font-size:15px;line-height:1.8;margin:0 0 20px;\">{escape(notice)}</p>
  <p style=\"color:#8899a6;font-size:12px;margin:24px 0 0;\">此邮件由系统自动发送，请勿回复。</p>
</div>"""


def _schedule_email_delivery(delivery_id: int, recipient_email: str, subject: str, body: str) -> None:
    async def _send() -> None:
        from app.database import SessionLocal

        db = SessionLocal()
        try:
            ok = await EmailService.send_email(recipient_email, subject, body)
            delivery = db.get(NotificationDelivery, delivery_id)
            if delivery:
                delivery.status = "sent" if ok else "failed"
                delivery.sent_at = datetime.utcnow() if ok else None
                delivery.last_error_code = None if ok else "EMAIL_SEND_FAILED"
                delivery.retry_count += 1
                db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning(
                "[ADMIN NOTICE EMAIL] delivery failed id=%s error_type=%s",
                delivery_id,
                exc.__class__.__name__,
            )
        finally:
            db.close()

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_send())
    except RuntimeError:
        try:
            asyncio.run(_send())
        except Exception as exc:
            logger.warning(
                "[ADMIN NOTICE EMAIL] delivery runner failed id=%s error_type=%s",
                delivery_id,
                exc.__class__.__name__,
            )


def create_governance_notification(
    db: Session,
    *,
    user: User | None,
    event_type: str,
    target_type: str | None = None,
    target_id: str | int | None = None,
    user_notice: str | None = None,
    notify_channels: Iterable[str] | None = None,
) -> tuple[list[NotificationDelivery], list[Notification]]:
    if user is None:
        return [], []

    channels = normalize_notify_channels(notify_channels, event_type=event_type)
    notice = sanitize_user_notice(user_notice, event_type)
    title = _DEFAULT_TITLES.get(event_type, "平台治理通知")
    notification = None
    deliveries: list[NotificationDelivery] = []
    notifications: list[Notification] = []

    if "in_app" in channels or "push" in channels:
        notification = Notification(
            user_id=user.id,
            notification_type=event_type,
            title=title,
            content=notice,
            related_id=int(target_id) if str(target_id or "").isdigit() else None,
            related_type=target_type,
        )
        db.add(notification)
        db.flush()
        notifications.append(notification)

    if "email" in channels:
        subject = _EMAIL_SUBJECTS.get(event_type, f"【NonTo】{title}")
        delivery = NotificationDelivery(
            notification_id=notification.id if notification else None,
            user_id=user.id,
            channel="email",
            event_type=event_type,
            target_type=target_type,
            target_id=str(target_id)[:80] if target_id is not None else None,
            recipient_email=user.email,
            subject=subject,
            body=_email_body(title, notice),
            status="pending",
            retry_count=0,
        )
        db.add(delivery)
        db.flush()
        deliveries.append(delivery)

    return deliveries, notifications


def dispatch_governance_notifications(deliveries: Iterable[NotificationDelivery]) -> None:
    for delivery in deliveries:
        if delivery.channel != "email" or not delivery.recipient_email or not delivery.subject or not delivery.body:
            continue
        _schedule_email_delivery(delivery.id, delivery.recipient_email, delivery.subject, delivery.body)


def dispatch_in_app_and_push(user_id: int, notification: Notification | None) -> None:
    if notification is None:
        return
    payload = notification.to_dict()
    try:
        NotificationService._push_new_notification(user_id, payload)
    except Exception as exc:
        logger.warning("[ADMIN NOTICE WS] scheduling failed uid=%s error_type=%s", user_id, exc.__class__.__name__)
    try:
        AliyunPushService.schedule_notification_push(user_id, payload)
    except Exception as exc:
        logger.warning("[ADMIN NOTICE PUSH] scheduling failed uid=%s error_type=%s", user_id, exc.__class__.__name__)
