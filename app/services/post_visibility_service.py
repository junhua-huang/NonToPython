"""Canonical post visibility writes and fail-closed legacy read policy."""

from typing import Any

from sqlalchemy import and_, exists, or_
from sqlalchemy.orm import Session

from app.models.community import CommunityMember
from app.models.models import Block, Friendship, Post


CANONICAL_POST_VISIBILITIES = frozenset({"public", "friends"})
LEGACY_FRIEND_VISIBILITIES = frozenset({"friends", "friends_only"})


def normalize_post_visibility(visibility: str | None) -> str:
    """Normalize supported writes, accepting only the legacy friends alias."""
    if visibility == "friends_only":
        return "friends"
    if visibility in CANONICAL_POST_VISIBILITIES:
        return visibility
    raise ValueError("visibility must be public or friends")


def validate_post_visibility_write(visibility: str | None, visible_user_ids: Any = None) -> str:
    """Validate a new visibility write and reject custom audience selectors."""
    if visible_user_ids not in (None, "", [], (), set()):
        raise ValueError("visible_user_ids custom audiences are no longer supported")
    return normalize_post_visibility(visibility)


def post_visibility_predicate(viewer_user_id: int | None):
    """Return the reusable SQL predicate for posts visible to a viewer."""
    not_admin_hidden = or_(Post.hidden_by_admin.is_(False), Post.hidden_by_admin.is_(None))
    not_community_only = or_(Post.community_only.is_(False), Post.community_only.is_(None))

    if viewer_user_id is None:
        return and_(
            not_admin_hidden,
            not_community_only,
            Post.visibility == "public",
        )

    blocked = exists().where(or_(
        and_(Block.blocker_id == viewer_user_id, Block.blocked_id == Post.user_id),
        and_(Block.blocker_id == Post.user_id, Block.blocked_id == viewer_user_id),
    ))
    accepted_friend = exists().where(and_(
        Friendship.status == "accepted",
        or_(
            and_(Friendship.sender_id == viewer_user_id, Friendship.receiver_id == Post.user_id),
            and_(Friendship.receiver_id == viewer_user_id, Friendship.sender_id == Post.user_id),
        ),
    ))
    active_community_member = exists().where(and_(
        CommunityMember.community_id == Post.community_id,
        CommunityMember.user_id == viewer_user_id,
        CommunityMember.status == "active",
    ))
    community_scope = or_(
        not_community_only,
        and_(
            Post.community_id.isnot(None),
            or_(Post.user_id == viewer_user_id, active_community_member),
        ),
    )
    audience_scope = or_(
        Post.user_id == viewer_user_id,
        and_(
            ~blocked,
            or_(
                Post.visibility == "public",
                and_(Post.visibility.in_(LEGACY_FRIEND_VISIBILITIES), accepted_friend),
            ),
        ),
    )

    return and_(not_admin_hidden, community_scope, audience_scope)


def load_visible_post(
    db: Session,
    post_id: int,
    viewer_user_id: int | None,
) -> Post | None:
    """Load a post through the canonical visibility predicate."""
    return db.query(Post).filter(
        Post.id == post_id,
        post_visibility_predicate(viewer_user_id),
    ).first()


def can_view_post(db: Session, post: Post, viewer_user_id: int | None) -> bool:
    """Apply the shared SQL policy to a loaded post for detail-style checks."""
    return load_visible_post(db, post.id, viewer_user_id) is not None
