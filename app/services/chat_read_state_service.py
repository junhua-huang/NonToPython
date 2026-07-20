"""Per-user chat read-state helpers.

Direct conversations still use the legacy ``Message.is_read`` flag in this
release. Community conversations use each participant's read cursor so one group
member opening chat does not clear unread counts for other members.
"""
from datetime import datetime
from typing import Iterable

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.models.community import CommunityMember
from app.models.models import Conversation, ConversationParticipant, Message
from app.services.block_service import has_block_between, no_block_between_predicate, visible_user_predicate


def get_community_participant(
    db: Session,
    conversation_id: int,
    user_id: int,
) -> ConversationParticipant | None:
    """Return active community participant row for a user and conversation."""
    return (
        db.query(ConversationParticipant)
        .join(Conversation, Conversation.id == ConversationParticipant.conversation_id)
        .join(
            CommunityMember,
            and_(
                CommunityMember.community_id == Conversation.community_id,
                CommunityMember.user_id == ConversationParticipant.user_id,
            ),
        )
        .filter(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.user_id == user_id,
            Conversation.type == 'community',
            CommunityMember.status == 'active',
        )
        .first()
    )


def can_access_conversation(db: Session, conversation: Conversation | None, user_id: int) -> bool:
    """Return whether a user may access a direct or active community chat."""
    if not conversation:
        return False
    if getattr(conversation, 'type', 'direct') == 'community':
        return get_community_participant(db, conversation.id, user_id) is not None
    if conversation.user1_id != user_id and conversation.user2_id != user_id:
        return False
    other_user_id = conversation.user2_id if conversation.user1_id == user_id else conversation.user1_id
    return other_user_id is not None and not has_block_between(db, user_id, other_user_id)


def get_active_conversation_participant_ids(db: Session, conversation: Conversation) -> list[int]:
    """Return deliverable participant IDs, filtering community chats by active membership."""
    if conversation.type == 'community':
        return [
            row.user_id
            for row in db.query(ConversationParticipant)
            .join(Conversation, Conversation.id == ConversationParticipant.conversation_id)
            .join(
                CommunityMember,
                and_(
                    CommunityMember.community_id == Conversation.community_id,
                    CommunityMember.user_id == ConversationParticipant.user_id,
                ),
            )
            .filter(
                ConversationParticipant.conversation_id == conversation.id,
                Conversation.type == 'community',
                CommunityMember.status == 'active',
            )
            .all()
        ]
    participant_ids = {
        row.user_id
        for row in db.query(ConversationParticipant.user_id)
        .filter(ConversationParticipant.conversation_id == conversation.id)
        .all()
    }
    participant_ids.update(
        user_id
        for user_id in (conversation.user1_id, conversation.user2_id)
        if user_id is not None
    )
    return sorted(participant_ids)


def mark_community_conversation_read(
    db: Session,
    conversation_id: int,
    user_id: int,
) -> int:
    """Advance only this participant's read cursor for a community chat."""
    participant = get_community_participant(db, conversation_id, user_id)
    if not participant:
        return 0

    latest = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.id.desc())
        .first()
    )
    participant.last_read_message_id = latest.id if latest else None
    participant.last_read_at = latest.created_at if latest else datetime.utcnow()
    return 1


def get_direct_unread_count(db: Session, user_id: int) -> int:
    """Legacy direct-chat unread count based on Message.is_read."""
    direct_conv_ids = [
        row.id
        for row in db.query(Conversation.id)
        .filter(
            Conversation.type != 'community',
            (Conversation.user1_id == user_id) | (Conversation.user2_id == user_id),
            no_block_between_predicate(user_id, func.coalesce(
                func.nullif(Conversation.user1_id, user_id),
                Conversation.user2_id,
            )),
        )
        .all()
    ]
    if not direct_conv_ids:
        return 0
    return (
        db.query(func.count(Message.id))
        .filter(
            Message.conversation_id.in_(direct_conv_ids),
            Message.is_read == False,
            Message.sender_id != user_id,
        )
        .scalar()
        or 0
    )


def get_community_unread_counts(
    db: Session,
    user_id: int,
    conversation_ids: Iterable[int] | None = None,
) -> dict[int, int]:
    """Return per-conversation unread counts using participant read cursors."""
    query = (
        db.query(Conversation.id, func.count(Message.id))
        .join(ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id)
        .join(
            CommunityMember,
            and_(
                CommunityMember.community_id == Conversation.community_id,
                CommunityMember.user_id == ConversationParticipant.user_id,
            ),
        )
        .join(Message, Message.conversation_id == Conversation.id)
        .filter(
            Conversation.type == 'community',
            ConversationParticipant.user_id == user_id,
            CommunityMember.status == 'active',
            Message.sender_id != user_id,
            visible_user_predicate(user_id, Message.sender_id),
            or_(
                ConversationParticipant.last_read_message_id.is_(None),
                Message.id > ConversationParticipant.last_read_message_id,
            ),
        )
    )
    ids = list(conversation_ids or [])
    if ids:
        query = query.filter(Conversation.id.in_(ids))
    return {row[0]: row[1] for row in query.group_by(Conversation.id).all()}


def get_total_unread_count(db: Session, user_id: int) -> int:
    """Combine legacy direct unread and cursor-based community unread."""
    direct_unread = get_direct_unread_count(db, user_id)
    community_unread = sum(get_community_unread_counts(db, user_id).values())
    return direct_unread + community_unread
