"""Database-authoritative bidirectional block isolation helpers."""

from sqlalchemy import and_, exists, or_
from sqlalchemy.orm import Session

from app.models.models import Block


def block_between_predicate(user_a_id, user_b_id):
    """Return a correlated EXISTS expression for a block in either direction."""
    return exists().where(or_(
        and_(Block.blocker_id == user_a_id, Block.blocked_id == user_b_id),
        and_(Block.blocker_id == user_b_id, Block.blocked_id == user_a_id),
    ))


def no_block_between_predicate(user_a_id, user_b_id):
    """Return a SQL predicate requiring no block in either direction."""
    return ~block_between_predicate(user_a_id, user_b_id)


def has_block_between(db: Session, user_a_id: int, user_b_id: int) -> bool:
    """Read the authoritative database relation for either block direction."""
    if user_a_id == user_b_id:
        return False
    return db.query(Block.id).filter(or_(
        and_(Block.blocker_id == user_a_id, Block.blocked_id == user_b_id),
        and_(Block.blocker_id == user_b_id, Block.blocked_id == user_a_id),
    )).first() is not None


def excluded_user_ids(db: Session, user_id: int) -> set[int]:
    """Load all users isolated from ``user_id`` in one database query."""
    rows = db.query(Block.blocker_id, Block.blocked_id).filter(or_(
        Block.blocker_id == user_id,
        Block.blocked_id == user_id,
    )).all()
    return {
        blocked_id if blocker_id == user_id else blocker_id
        for blocker_id, blocked_id in rows
    }


def visible_user_predicate(viewer_user_id: int, candidate_user_id):
    """Return a correlated predicate hiding candidate users blocked either way."""
    return no_block_between_predicate(viewer_user_id, candidate_user_id)
