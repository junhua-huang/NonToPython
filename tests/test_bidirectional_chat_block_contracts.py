import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.models import (
    Block,
    Conversation,
    ConversationParticipant,
    Friendship,
    Message,
    User,
)
from app.routers import chat as chat_router
from app.routers import ws as ws_router
from app.services.chat_read_state_service import can_access_conversation, get_direct_unread_count


@pytest.fixture()
def chat_block_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Session = sessionmaker(bind=engine, autoflush=False)
    Base.metadata.create_all(engine)
    db = Session()
    db.info["session_factory"] = Session
    try:
        db.add_all([
            User(id=1, username="alice", email="alice@example.com", password_hash="x"),
            User(id=2, username="bob", email="bob@example.com", password_hash="x"),
            Friendship(sender_id=1, receiver_id=2, status="accepted"),
            Conversation(id=1, user1_id=1, user2_id=2, type="direct"),
            ConversationParticipant(conversation_id=1, user_id=1),
            ConversationParticipant(conversation_id=1, user_id=2),
            Message(id=1, conversation_id=1, sender_id=2, content="old secret", is_read=False),
            Message(id=2, conversation_id=1, sender_id=1, content="alice secret", is_read=False),
            Block(blocker_id=2, blocked_id=1),
        ])
        db.commit()
        yield db
    finally:
        db.close()
        engine.dispose()


def test_direct_conversation_access_and_unread_are_denied_bidirectionally(chat_block_db):
    conversation = chat_block_db.get(Conversation, 1)

    assert not can_access_conversation(chat_block_db, conversation, 1)
    assert not can_access_conversation(chat_block_db, conversation, 2)
    assert get_direct_unread_count(chat_block_db, 1) == 0


def test_http_direct_lists_detail_history_send_read_and_presence_hide_reverse_block(chat_block_db):
    alice = chat_block_db.get(User, 1)

    with patch.object(chat_router.ws_manager, "get_blocked_user_ids", return_value=set()):
        assert chat_router.get_sessions(page=1, per_page=30, user=alice, db=chat_block_db)["sessions"] == []
        assert chat_router.get_conversations(user=alice, db=chat_block_db)["conversations"] == []

        denied_calls = [
            lambda: chat_router.get_conversation_with_user(user_id=2, user=alice, db=chat_block_db),
            lambda: chat_router.get_messages(conversation_id=1, page=1, per_page=20, user=alice, db=chat_block_db),
            lambda: chat_router.get_messages_v2(conversation_id=1, page=1, limit=20, user=alice, db=chat_block_db),
            lambda: chat_router.get_messages_batch(conv_ids="1", per_page=20, user=alice, db=chat_block_db),
            lambda: chat_router.get_messages_around(conversation_id=1, target_id=1, before=5, after=5, user=alice, db=chat_block_db),
            lambda: asyncio.run(chat_router.send_message(conversation_id=1, payload={"content": "no"}, user=alice, db=chat_block_db)),
            lambda: asyncio.run(chat_router.mark_conversation_as_read(conversation_id=1, user=alice, db=chat_block_db)),
            lambda: chat_router.recall_message(message_id=2, user=alice, db=chat_block_db),
            lambda: chat_router.get_user_status(user_id=2, user=alice, db=chat_block_db),
        ]
        for call in denied_calls:
            with pytest.raises(HTTPException) as exc_info:
                call()
            assert exc_info.value.status_code in {403, 404}

    assert chat_block_db.get(Message, 1).is_read is False


def test_ws_db_authority_denies_reverse_block_with_stale_empty_cache(chat_block_db):
    with patch.object(ws_router.ws_manager, "get_blocked_user_ids", return_value=set()):
        assert not ws_router._can_send_to_user(chat_block_db, 1, 2)
        assert not ws_router._can_send_to_participants(1, [1, 2], db=chat_block_db)
        assert ws_router._build_session_list(chat_block_db, 1) == []


def test_ws_typing_checks_conversation_access_before_broadcast(chat_block_db, monkeypatch):
    broadcast = AsyncMock()
    monkeypatch.setattr(ws_router, "_get_db_session", chat_block_db.info["session_factory"])
    monkeypatch.setattr(ws_router.ws_manager, "broadcast_to_conversation", broadcast)

    asyncio.run(ws_router._handle_typing(
        websocket=None,
        user_id=1,
        data={"payload": {"conversation_id": 1}},
        is_typing=True,
    ))

    broadcast.assert_not_awaited()


def test_ws_auth_revalidates_stale_sessions_rooms_and_online_friends(chat_block_db, monkeypatch):
    manager = ws_router.ws_manager
    sent = AsyncMock()
    joined = Mock()
    monkeypatch.setattr(ws_router, "_get_db_session", chat_block_db.info["session_factory"])
    monkeypatch.setattr(manager, "connect", AsyncMock(return_value="conn"))
    monkeypatch.setattr(manager, "is_connected", lambda user_id: False)
    monkeypatch.setattr(manager, "is_product_online_async", AsyncMock(side_effect=lambda uid: uid == 2))
    monkeypatch.setattr(manager, "send_with_seq", sent)
    monkeypatch.setattr(manager, "join_conversation", joined)
    monkeypatch.setattr(manager, "notify_community_presence", AsyncMock())
    monkeypatch.setattr(manager, "_notify_friends_online", AsyncMock())
    monkeypatch.setattr(manager, "get_cached_sessions", lambda user_id: [{"conversation_id": 1}])
    monkeypatch.setattr(manager, "get_cached_conv_ids", lambda user_id: [1])
    monkeypatch.setattr(manager, "set_cached_sessions", lambda user_id, sessions: None)
    monkeypatch.setattr(manager, "set_cached_conv_ids", lambda user_id, ids: None)

    asyncio.run(ws_router._do_auth_init(websocket=None, user_id=1))

    assert all(call.args[1] not in {"session_list", "online_friends"} for call in sent.await_args_list)
    joined.assert_not_called()


def test_ws_read_denies_blocked_direct_history_and_preserves_unread(chat_block_db, monkeypatch):
    sent_error = AsyncMock()
    monkeypatch.setattr(ws_router, "_get_db_session", chat_block_db.info["session_factory"])
    monkeypatch.setattr(ws_router, "_send_error", sent_error)

    asyncio.run(ws_router._handle_conversation_read(
        websocket=None,
        user_id=1,
        payload={"conversation_id": 1, "request_id": "r1"},
    ))

    chat_block_db.expire_all()
    assert chat_block_db.get(Message, 1).is_read is False
    sent_error.assert_awaited()


def test_ws_recall_denies_blocked_direct_conversation(chat_block_db, monkeypatch):
    sent_error = AsyncMock()
    sent_event = AsyncMock()
    monkeypatch.setattr(ws_router, "_get_db_session", chat_block_db.info["session_factory"])
    monkeypatch.setattr(ws_router, "_send_error", sent_error)
    monkeypatch.setattr(ws_router.ws_manager, "send_with_seq", sent_event)

    asyncio.run(ws_router._handle_recall_message(
        websocket=None,
        user_id=1,
        data={"request_id": "r2", "payload": {"message_id": 2}},
    ))

    chat_block_db.expire_all()
    assert chat_block_db.get(Message, 2).is_recalled is False
    sent_error.assert_awaited()
    sent_event.assert_not_awaited()
