"""Shared visibility queries for recipient notifications."""

from sqlalchemy import and_, exists, or_
from sqlalchemy.orm import Query, Session

from app.models.models import Block, Notification


def visible_notification_query(
    db: Session,
    user_id: int,
    *,
    include_message_notifications: bool = False,
) -> Query:
    """Build a recipient query for interaction/system notifications.

    Chat messages are delivered through the chat stream and optional mobile push,
    so legacy ``notification_type=message`` rows are excluded by default.
    """
    blocked_sender_exists = exists().where(or_(
        and_(Block.blocker_id == user_id, Block.blocked_id == Notification.sender_id),
        and_(Block.blocker_id == Notification.sender_id, Block.blocked_id == user_id),
    ))
    filters = [
        Notification.user_id == user_id,
        ~blocked_sender_exists,
    ]
    if not include_message_notifications:
        filters.append(Notification.notification_type != "message")
    return db.query(Notification).filter(*filters)


def visible_notification_ids(
    db: Session,
    user_id: int,
    notification_ids: set[int],
    *,
    include_message_notifications: bool = False,
) -> set[int]:
    """Return the currently visible subset of recipient notification IDs."""
    if not notification_ids:
        return set()
    return {
        notification_id
        for (notification_id,) in visible_notification_query(
            db,
            user_id,
            include_message_notifications=include_message_notifications,
        )
        .with_entities(Notification.id)
        .filter(Notification.id.in_(notification_ids))
        .all()
    }


def is_notification_visible(
    db: Session,
    user_id: int,
    notification_id: int,
    *,
    include_message_notifications: bool = False,
) -> bool:
    """Return whether a notification is currently visible to its recipient."""
    return notification_id in visible_notification_ids(
        db,
        user_id,
        {notification_id},
        include_message_notifications=include_message_notifications,
    )


def visible_unread_count(
    db: Session,
    user_id: int,
    *,
    include_message_notifications: bool = False,
) -> int:
    """Count unread notifications visible to the recipient."""
    return visible_notification_query(
        db,
        user_id,
        include_message_notifications=include_message_notifications,
    ).filter(Notification.is_read == False).count()
