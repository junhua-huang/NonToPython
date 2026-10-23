"""Transactional hard deletion. Cloud operations run only after the DB commit.

Uses Core statements so deletion does not depend on incomplete ORM cascades.
Unknown user foreign keys fail closed before any mutation.
"""
from datetime import datetime, timedelta
import json
import re
import uuid

from sqlalchemy import and_, delete, func, or_, select, update

from app.database import Base
from app.models import models as m
from app.models import community as c
from app.models import deployment as d
from app.models.privacy import AccountErasure

UPLOAD_FOLDERS = ("avatars", "covers", "posts", "comic", "chat", "general")
OWNED_REFERENCES = {
    "post_visibility": ("user_id",), "topic_followers": ("user_id",),
    "post_feed_seen": ("user_id",), "likes": ("user_id",),
    "friendships": ("sender_id", "receiver_id"),
    "conversation_participants": ("user_id",), "push_devices": ("user_id",),
    "blocks": ("blocker_id", "blocked_id"), "post_views": ("user_id",),
    "search_history": ("user_id",), "comic_event_follows": ("user_id",),
    "comic_likes": ("user_id",), "comic_comment_likes": ("user_id",),
    "ws_message_log": ("user_id",), "ws_user_seq": ("user_id",),
    "ws_ack_dedup": ("user_id",), "user_roles": ("user_id",),
    "coser_profiles": ("user_id",), "photographer_profiles": ("user_id",),
    "service_profiles": ("user_id",), "role_applications": ("user_id",),
    "community_members": ("user_id",), "community_join_requests": ("user_id",),
    "community_announcements": ("author_id",), "community_bans": ("user_id",),
}
NULL_REFERENCES = {
    "posts": ("hidden_by",), "comments": ("reply_to_user_id", "hidden_by"),
    "conversations": ("user1_id", "user2_id"), "reports": ("resolved_by",),
    "sensitive_words": ("created_by",), "comic_events": ("creator_id",),
    "comic_comments": ("reply_to_user_id",), "role_applications": ("reviewer_id",),
    "moderation_events": ("actor_user_id",), "admin_settings": ("updated_by",),
    "admin_audit_logs": ("admin_user_id",), "communities": ("owner_id",),
    "community_join_requests": ("reviewed_by",), "community_bans": ("banned_by",),
    "deployment_artifacts": ("uploaded_by",), "deployment_jobs": ("created_by", "approved_by"),
    "deployment_settings": ("updated_by",),
}
SPECIAL_REFERENCES = {
    ("posts", "user_id"), ("comments", "user_id"), ("messages", "sender_id"),
    ("comic_comments", "user_id"), ("notifications", "user_id"),
    ("notifications", "sender_id"), ("notification_deliveries", "user_id"),
    ("reports", "reporter_id"),
}


def assert_user_reference_coverage():
    covered = SPECIAL_REFERENCES | {
        (table, column) for policy in (OWNED_REFERENCES, NULL_REFERENCES)
        for table, columns in policy.items() for column in columns
    }
    for table in Base.metadata.tables.values():
        for fk in table.foreign_keys:
            if fk.target_fullname == "users.id" and (table.name, fk.parent.name) not in covered:
                raise RuntimeError("Unreviewed user reference prevents erasure")


def _chunks(values, size=400):
    values = list(values)
    for offset in range(0, len(values), size):
        yield values[offset:offset + size]


def _ids(db, model, condition):
    return list(db.execute(select(model.id).where(condition)).scalars())


def _remove_ids(db, table, column, ids):
    for chunk in _chunks(ids):
        db.execute(delete(table).where(column.in_(chunk)))


def _update_ids(db, table, column, ids, **values):
    for chunk in _chunks(ids):
        db.execute(update(table).where(column.in_(chunk)).values(**values))


def _scoped_target(table, targets):
    return or_(*(and_(table.c.target_type == kind, table.c.target_id.in_(ids))
                 for kind, ids in targets.items() if ids))


def replay_contains_erased_data(payload, user_id, message_ids, notification_ids, post_ids):
    """Remove copied identity, quote previews and old media from other users' replays."""
    if isinstance(payload, dict):
        user_keys = {"user_id", "sender_id", "author_id", "actor_user_id", "owner_id",
                     "user1_id", "user2_id", "receiver_id", "blocked_id", "reply_to_user_id"}
        for key, value in payload.items():
            if key in user_keys and value == user_id:
                return True
            if key in {"message_id", "quote_message_id"} and isinstance(value, int) and value in message_ids:
                return True
            if key in {"post_id", "quoted_post_id"} and isinstance(value, int) and value in post_ids:
                return True
            if key == "notification_id" and isinstance(value, int) and value in notification_ids:
                return True
            if key in {"user_ids", "online_user_ids", "online_friends"} and isinstance(value, list) and user_id in value:
                return True
        if payload.get("id") == user_id and ("username" in payload or "avatar_url" in payload):
            return True
        return any(replay_contains_erased_data(v, user_id, message_ids, notification_ids, post_ids)
                   for v in payload.values())
    if isinstance(payload, list):
        return any(replay_contains_erased_data(v, user_id, message_ids, notification_ids, post_ids) for v in payload)
    if isinstance(payload, str):
        return any(f"/{folder}/{user_id}/" in payload for folder in UPLOAD_FOLDERS)
    return False


def _legacy_media_keys(db, user_id, post_ids, comment_ids, message_ids):
    from app.utils import FileUploader
    # A legacy URL is not ownership proof. Keep only COS keys for restricted review.
    queries = [select(m.User.avatar_url, m.User.cover_photo_url).where(m.User.id == user_id)]
    queries += [select(model.portfolio_images).where(model.user_id == user_id)
                for model in (m.CoserProfile, m.PhotographerProfile, m.ServiceProfile)]
    queries += [select(m.RoleApplication.proof_images).where(m.RoleApplication.user_id == user_id)]
    for ids, model, columns in (
        (post_ids, m.Post, (m.Post.images, m.Post.video_url, m.Post.content)),
        (comment_ids, m.Comment, (m.Comment.content,)),
        (message_ids, m.Message, (m.Message.media_url, m.Message.content)),
    ):
        queries += [select(*columns).where(model.id.in_(chunk)) for chunk in _chunks(ids)]
    keys = set()
    for query in queries:
        for row in db.execute(query):
            for value in row:
                if not isinstance(value, str):
                    continue
                for url in re.findall(r'https://[^\s"<>\\]+', value):
                    key = FileUploader.cos_key_from_url(url.rstrip("',]}"))
                    if not key:
                        continue
                    if not any(key.startswith(f"{folder}/{user_id}/") for folder in UPLOAD_FOLDERS):
                        # Never enqueue another account's namespace as a legacy object.
                        parts = key.split("/")
                        if len(parts) > 2 and parts[0] in UPLOAD_FOLDERS and parts[1].isdigit():
                            continue
                        keys.add(key)
    return sorted(keys)


def erase_account(db, user_id, *, now=None):
    """Stage all changes in caller transaction; caller commits or rolls everything back."""
    if type(user_id) is not int or user_id <= 0:
        raise ValueError("Positive account ID required")
    assert_user_reference_coverage()
    now = now or datetime.utcnow()
    users = m.User.__table__
    account = db.execute(select(users).where(users.c.id == user_id).with_for_update()).mappings().first()
    ledger = AccountErasure.__table__
    existing = db.execute(select(ledger.c.id).where(ledger.c.user_id == user_id)).scalar_one_or_none()
    if account is None:
        if existing:
            return existing
        raise LookupError("Account not found")

    post_ids = _ids(db, m.Post, m.Post.user_id == user_id)
    comment_ids = set(_ids(db, m.Comment, m.Comment.user_id == user_id))
    for chunk in _chunks(post_ids):
        comment_ids.update(_ids(db, m.Comment, m.Comment.post_id.in_(chunk)))
    comic_ids = _ids(db, m.ComicComment, m.ComicComment.user_id == user_id)
    message_ids = _ids(db, m.Message, m.Message.sender_id == user_id)
    legacy_keys = _legacy_media_keys(db, user_id, post_ids, comment_ids, message_ids)
    erasure_id = existing or str(uuid.uuid4())
    if not existing:
        db.execute(ledger.insert().values(id=erasure_id, user_id=user_id, media_status="pending",
            legacy_keys_json=json.dumps(legacy_keys), created_at=now, final_sweep_after=now + timedelta(minutes=65),
            next_attempt_at=now, attempts=0, backup_status="unverified"))
    else:
        db.execute(update(ledger).where(ledger.c.id == erasure_id).values(media_status="pending",
            next_attempt_at=now, media_completed_at=None))

    affected_communities = set(db.execute(select(c.CommunityMember.community_id).where(c.CommunityMember.user_id == user_id)).scalars())
    affected_communities.update(db.execute(select(m.Post.community_id).where(m.Post.user_id == user_id, m.Post.community_id.is_not(None))).scalars())
    event_ids = set(db.execute(select(m.ComicComment.event_id).where(m.ComicComment.user_id == user_id)).scalars())
    event_ids.update(db.execute(select(m.ComicLike.event_id).where(m.ComicLike.user_id == user_id)).scalars())

    targets = {"user": [user_id], "post": post_ids, "comment": list(comment_ids), "message": message_ids,
               "comic_comment": comic_ids}
    notification_conditions = [m.Notification.user_id == user_id, m.Notification.sender_id == user_id]
    # related_id has no FK and therefore must be included explicitly.
    for kind, ids in targets.items():
        for chunk in _chunks(ids):
            notification_conditions.append(and_(m.Notification.related_type == kind, m.Notification.related_id.in_(chunk)))
    notification_ids = _ids(db, m.Notification, or_(*notification_conditions))
    for table in (m.NotificationDelivery.__table__, m.PushLog.__table__):
        db.execute(delete(table).where(table.c.user_id == user_id))
        _remove_ids(db, table, table.c.notification_id, notification_ids)
    _remove_ids(db, m.Notification.__table__, m.Notification.id, notification_ids)

    # Replays are serialized copies, not live FK relations. Parse in bounded batches.
    cursor = 0
    while True:
        rows = db.execute(select(m.WSMessageLog.id, m.WSMessageLog.payload).where(m.WSMessageLog.id > cursor)
                          .order_by(m.WSMessageLog.id).limit(400)).all()
        if not rows:
            break
        cursor = rows[-1].id
        stale = []
        for row in rows:
            try:
                payload = json.loads(row.payload)
            except (ValueError, TypeError):
                stale.append(row.id)
                continue
            if replay_contains_erased_data(payload, user_id, set(message_ids), set(notification_ids), set(post_ids)):
                stale.append(row.id)
        _remove_ids(db, m.WSMessageLog.__table__, m.WSMessageLog.id, stale)

    _update_ids(db, m.Message.__table__, m.Message.quote_message_id, message_ids,
                quote_message_id=None, quote_preview=None)
    for chunk in _chunks(post_ids):
        db.execute(update(m.Message.__table__).where(m.Message.message_type == "post", m.Message.related_id.in_(chunk))
                   .values(related_id=None, content="原内容已删除", media_url=None))
    _remove_ids(db, m.WSAckDedup.__table__, m.WSAckDedup.message_id, message_ids)
    _remove_ids(db, m.Message.__table__, m.Message.id, message_ids)

    _update_ids(db, m.Post.__table__, m.Post.quoted_post_id, post_ids, quoted_post_id=None)
    _update_ids(db, m.Comment.__table__, m.Comment.parent_id, comment_ids, parent_id=None)
    _remove_ids(db, m.Like.__table__, m.Like.comment_id, comment_ids)
    _remove_ids(db, m.Comment.__table__, m.Comment.id, comment_ids)
    for table, column in ((m.Like.__table__, m.Like.post_id), (m.PostView.__table__, m.PostView.post_id),
                          (m.PostFeedSeen.__table__, m.PostFeedSeen.post_id), (m.post_topics, m.post_topics.c.post_id),
                          (m.post_visibility, m.post_visibility.c.post_id)):
        _remove_ids(db, table, column, post_ids)
    _remove_ids(db, m.Post.__table__, m.Post.id, post_ids)
    _update_ids(db, m.ComicComment.__table__, m.ComicComment.parent_id, comic_ids, parent_id=None)
    _remove_ids(db, m.ComicCommentLike.__table__, m.ComicCommentLike.comment_id, comic_ids)
    _remove_ids(db, m.ComicComment.__table__, m.ComicComment.id, comic_ids)

    db.execute(delete(m.Report.__table__).where(or_(m.Report.reporter_id == user_id, _scoped_target(m.Report.__table__, targets))))
    db.execute(delete(m.EmailOtp.__table__).where(func.lower(m.EmailOtp.email) == account["email"].lower()))
    db.execute(delete(m.LoginAttempt.__table__).where(func.lower(m.LoginAttempt.identifier).in_(
        [account["email"].lower(), account["username"].lower()])))
    # Shared content survives with no link to a fabricated replacement identity.
    db.execute(update(c.Community.__table__).where(c.Community.owner_id == user_id).values(owner_id=None, status="archived"))
    db.execute(update(m.RoleApplication.__table__).where(m.RoleApplication.reviewer_id == user_id)
               .values(review_comment=None))
    db.execute(update(m.AdminAuditLog.__table__).where(or_(m.AdminAuditLog.admin_user_id == user_id,
        _scoped_target(m.AdminAuditLog.__table__, {k: [str(i) for i in v] for k, v in targets.items()})))
        .values(reason=None, metadata_json=None, ip_address=None, user_agent=None))
    db.execute(update(m.AdminAuditLog.__table__).where(_scoped_target(m.AdminAuditLog.__table__,
        {k: [str(i) for i in v] for k, v in targets.items()})).values(target_id=None))
    for setting in db.execute(select(d.DeploymentSettings.id, d.DeploymentSettings.operator_ids_json)).all():
        operators = json.loads(setting.operator_ids_json)
        db.execute(update(d.DeploymentSettings.__table__).where(d.DeploymentSettings.id == setting.id)
                   .values(operator_ids_json=json.dumps([x for x in operators if str(x) != str(user_id)])))
    for table_name, columns in OWNED_REFERENCES.items():
        table = Base.metadata.tables[table_name]
        db.execute(delete(table).where(or_(*(table.c[col] == user_id for col in columns))))
    for table_name, columns in NULL_REFERENCES.items():
        table = Base.metadata.tables[table_name]
        for col in columns:
            db.execute(update(table).where(table.c[col] == user_id).values({col: None}))
    db.execute(delete(users).where(users.c.id == user_id))

    for community_id in affected_communities:
        members = db.execute(select(func.count()).select_from(c.CommunityMember).where(
            c.CommunityMember.community_id == community_id, c.CommunityMember.status == "active")).scalar_one()
        posts = db.execute(select(func.count()).select_from(m.Post).where(m.Post.community_id == community_id)).scalar_one()
        db.execute(update(c.Community.__table__).where(c.Community.id == community_id).values(member_count=members, post_count=posts))
    for event_id in event_ids:
        likes = db.execute(select(func.count()).select_from(m.ComicLike).where(m.ComicLike.event_id == event_id)).scalar_one()
        comments = db.execute(select(func.count()).select_from(m.ComicComment).where(m.ComicComment.event_id == event_id)).scalar_one()
        db.execute(update(m.ComicEvent.__table__).where(m.ComicEvent.id == event_id).values(like_count=likes, comment_count=comments))
    return erasure_id
