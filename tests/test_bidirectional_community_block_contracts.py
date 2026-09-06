import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.community import Community, CommunityMember
from app.models.models import Block, Conversation, ConversationParticipant, Friendship, Message, Notification, User
from app.routers import chat as chat_router
from app.routers import communities as communities_router
from app.routers import ws as ws_router
from app.services.chat_read_state_service import get_community_unread_counts


@pytest.fixture(autouse=True)
def approve_route_moderation(monkeypatch):
    monkeypatch.setattr(chat_router, "moderate_route_fields", lambda *args, **kwargs: None)
    monkeypatch.setattr(communities_router, "moderate_route_fields", lambda *args, **kwargs: None)
    monkeypatch.setattr(ws_router, "moderate_route_fields", lambda *args, **kwargs: None)


@pytest.fixture()
def community_block_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False)()
    try:
        db.add_all([
            User(id=1, username="alice", email="alice@example.com", password_hash="x"),
            User(id=2, username="bob", email="bob@example.com", password_hash="x"),
            User(id=3, username="carol", email="carol@example.com", password_hash="x"),
            Community(id=1, name="shared", slug="shared", owner_id=3, status="active"),
            CommunityMember(community_id=1, user_id=1, role="member", status="active"),
            CommunityMember(community_id=1, user_id=2, role="member", status="active"),
            CommunityMember(community_id=1, user_id=3, role="owner", status="active"),
            Conversation(id=1, type="community", community_id=1),
            ConversationParticipant(conversation_id=1, user_id=1),
            ConversationParticipant(conversation_id=1, user_id=2),
            ConversationParticipant(conversation_id=1, user_id=3),
            Message(id=1, conversation_id=1, sender_id=2, content="hidden"),
            Message(id=2, conversation_id=1, sender_id=3, content="visible"),
            Block(blocker_id=2, blocked_id=1),
        ])
        db.commit()
        yield db
    finally:
        db.close()
        engine.dispose()


def test_community_member_and_message_discovery_is_pairwise(community_block_db):
    alice = community_block_db.get(User, 1)

    members = communities_router.list_members(
        community_id=1, limit=50, offset=0, user=alice, db=community_block_db,
    )
    chat = communities_router.get_community_chat(
        community_id=1, limit=50, before_id=None, user=alice, db=community_block_db,
    )

    assert {item["user_id"] for item in members["members"]} == {1, 3}
    assert [item["sender_id"] for item in chat["messages"]] == [3]
    assert get_community_unread_counts(community_block_db, 1, [1]) == {1: 1}


def test_generic_chat_history_and_session_last_message_hide_blocked_community_sender(community_block_db):
    alice = community_block_db.get(User, 1)
    community_block_db.get(Message, 2).quote_message_id = 1
    community_block_db.commit()

    history = chat_router.get_messages(
        conversation_id=1, page=1, per_page=50, user=alice, db=community_block_db,
    )
    sessions = chat_router.get_sessions(
        page=1, per_page=30, user=alice, db=community_block_db,
    )
    ws_sessions = ws_router._build_session_list(community_block_db, 1)

    assert [item["sender_id"] for item in history["messages"]] == [3]
    assert history["messages"][0]["quote_preview"] == "消息已撤回"
    assert history["total"] == 1
    assert sessions["sessions"][0]["last_message"]["sender_id"] == 3
    assert sessions["sessions"][0]["last_message"]["quote_preview"] == "消息已撤回"
    assert ws_sessions[0]["last_message"]["sender_id"] == 3
    assert ws_sessions[0]["last_message"]["quote_preview"] == "消息已撤回"


def test_blocked_community_message_target_is_hidden_from_around(community_block_db):
    alice = community_block_db.get(User, 1)

    calls = [
        lambda: communities_router.get_community_message_around(
            community_id=1,
            target_id=1,
            before=5,
            after=5,
            user=alice,
            db=community_block_db,
        ),
        lambda: chat_router.get_messages_around(
            conversation_id=1,
            target_id=1,
            before=5,
            after=5,
            user=alice,
            db=community_block_db,
        ),
    ]
    for call in calls:
        with pytest.raises(HTTPException) as exc_info:
            call()
        assert exc_info.value.status_code == 404


def test_generic_around_window_hides_blocked_neighbor_messages(community_block_db):
    alice = community_block_db.get(User, 1)

    result = chat_router.get_messages_around(
        conversation_id=1,
        target_id=2,
        before=5,
        after=5,
        user=alice,
        db=community_block_db,
    )

    assert [item["sender_id"] for item in result["messages"]] == [3]


def test_batch_history_applies_limit_after_block_filter(community_block_db):
    alice = community_block_db.get(User, 1)
    now = datetime.utcnow()
    community_block_db.get(Message, 2).created_at = now - timedelta(minutes=2)
    community_block_db.get(Message, 1).created_at = now - timedelta(minutes=1)
    community_block_db.commit()

    result = chat_router.get_messages_batch(
        conv_ids="1",
        per_page=1,
        user=alice,
        db=community_block_db,
    )

    messages = result["data"]["conversations"][0]["messages"]
    assert [item["sender_id"] for item in messages] == [3]


def test_ws_sync_hides_blocked_message_and_presence_events_created_before_block(community_block_db, monkeypatch):
    from app import ws_manager as ws_manager_module

    Session = sessionmaker(bind=community_block_db.get_bind(), autoflush=False)
    monkeypatch.setattr(ws_manager_module, "SessionLocal", Session)
    manager = ws_manager_module.WSManager()
    manager._next_seq_sync(1, {"event": "new_message", "data": {"sender_id": 2, "content": "hidden"}})
    manager._next_seq_sync(1, {"event": "friend_online", "data": {"user_id": 2}})
    manager._next_seq_sync(1, {"event": "community_member_presence", "data": {"user_id": 2}})
    manager._next_seq_sync(1, {"event": "online_friends", "data": {"user_ids": [2, 3]}})
    manager._next_seq_sync(1, {"event": "message_recalled", "data": {"sender_id": 2, "message_id": 1}})
    manager._next_seq_sync(1, {"event": "conversation_read", "data": {"read_by": 2, "conversation_id": 1}})
    manager._next_seq_sync(1, {"event": "new_message", "data": {"sender_id": 3, "content": "visible"}})

    messages = asyncio.run(manager.get_messages_after_seq(1, 0))

    assert messages == [
        {"seq": 4, "payload": {"event": "online_friends", "data": {"user_ids": [3]}}},
        {"seq": 7, "payload": {"event": "new_message", "data": {"sender_id": 3, "content": "visible"}}},
    ]


def test_ws_recall_and_read_fanout_exclude_blocked_community_member(community_block_db, monkeypatch):
    community_block_db.add(Message(id=3, conversation_id=1, sender_id=1, content="alice recent"))
    community_block_db.commit()
    Session = sessionmaker(bind=community_block_db.get_bind(), autoflush=False)
    sent_raw = AsyncMock()
    sent_event = AsyncMock()
    monkeypatch.setattr(ws_router, "_get_db_session", Session)
    monkeypatch.setattr(ws_router.ws_manager, "send_raw", sent_raw)
    monkeypatch.setattr(ws_router.ws_manager, "send_with_seq", sent_event)

    asyncio.run(ws_router._handle_recall_message(
        websocket=None,
        user_id=1,
        data={"request_id": "recall", "payload": {"message_id": 3}},
    ))
    recall_targets = {
        call.args[0]
        for call in sent_event.await_args_list
        if call.args[1] == "message_recalled"
    }

    sent_event.reset_mock()
    asyncio.run(ws_router._handle_conversation_read(
        websocket=None,
        user_id=1,
        payload={"conversation_id": 1, "request_id": "read"},
    ))
    read_targets = {
        call.args[0]
        for call in sent_event.await_args_list
        if call.args[1] == "conversation_read"
    }

    assert recall_targets == {1, 3}
    assert read_targets == {3}


def test_ws_session_limit_is_applied_after_direct_block_filter(community_block_db):
    now = datetime.utcnow()
    users = [
        User(id=user_id, username=f"user{user_id}", email=f"user{user_id}@example.com", password_hash="x")
        for user_id in range(10, 61)
    ]
    conversations = []
    participants = []
    blocks = []
    for user_id in range(10, 61):
        conversation_id = user_id
        conversations.append(Conversation(
            id=conversation_id,
            user1_id=1,
            user2_id=user_id,
            type="direct",
            last_message_at=now if user_id < 60 else now - timedelta(days=1),
        ))
        participants.append(ConversationParticipant(conversation_id=conversation_id, user_id=1))
        participants.append(ConversationParticipant(conversation_id=conversation_id, user_id=user_id))
        if user_id < 60:
            blocks.append(Block(blocker_id=1, blocked_id=user_id))
    community_block_db.add_all(users + conversations + participants + blocks)
    community_block_db.commit()

    sessions = ws_router._build_session_list(community_block_db, 1)

    assert 60 in {item.get("partner_id") for item in sessions}


def test_presence_targets_exclude_blocked_friends_and_community_members(community_block_db, monkeypatch):
    from app import ws_manager as ws_manager_module

    community_block_db.add_all([
        Friendship(sender_id=1, receiver_id=2, status="accepted"),
        Friendship(sender_id=1, receiver_id=3, status="accepted"),
    ])
    community_block_db.commit()
    Session = community_block_db.info.get("session_factory")
    if Session is None:
        Session = sessionmaker(bind=community_block_db.get_bind(), autoflush=False)
    monkeypatch.setattr(ws_manager_module, "SessionLocal", Session)

    manager = ws_manager_module.WSManager()
    assert manager._get_friend_ids_sync(1) == [3]
    targets = manager._get_community_presence_targets_sync(1, [2, 3])
    assert targets == [{"community_id": 1, "conversation_id": 1, "recipient_ids": [3]}]


def test_generic_http_community_send_excludes_blocked_recipient(community_block_db, monkeypatch):
    alice = community_block_db.get(User, 1)
    sent = AsyncMock()
    monkeypatch.setattr(chat_router.ws_manager, "send_with_seq", sent)
    monkeypatch.setattr(chat_router.ws_manager, "invalidate_participant_caches", lambda ids: None)

    with patch("app.services.notification_service.NotificationService.push_message") as notify:
        asyncio.run(chat_router.send_message(
            conversation_id=1,
            payload={"content": "http hello"},
            user=alice,
            db=community_block_db,
        ))

    assert {call.args[0] for call in sent.await_args_list} == {1, 3}
    assert {call.args[0] for call in notify.call_args_list} == {3}


def test_community_typing_broadcast_excludes_blocked_room_member(community_block_db, monkeypatch):
    from app import ws_manager as ws_manager_module

    manager = ws_router.ws_manager
    delivered = AsyncMock()
    Session = sessionmaker(bind=community_block_db.get_bind(), autoflush=False)
    monkeypatch.setattr(ws_manager_module, "SessionLocal", Session)
    monkeypatch.setattr(
        ws_router,
        "_get_db_session",
        sessionmaker(bind=community_block_db.get_bind(), autoflush=False),
    )
    monkeypatch.setattr(manager, "_conversation_users", {1: {1, 2, 3}})
    monkeypatch.setattr(manager, "_raw_send_to_connections", delivered)

    asyncio.run(ws_router._handle_typing(
        websocket=None,
        user_id=1,
        data={"payload": {"conversation_id": 1}},
        is_typing=True,
    ))

    assert {call.args[0] for call in delivered.await_args_list} == {3}


def test_generic_http_community_quote_preview_is_pairwise_per_recipient(community_block_db, monkeypatch):
    carol = community_block_db.get(User, 3)
    sent = AsyncMock()
    monkeypatch.setattr(chat_router.ws_manager, "send_with_seq", sent)
    monkeypatch.setattr(chat_router.ws_manager, "invalidate_participant_caches", lambda ids: None)

    with patch("app.services.notification_service.NotificationService.push_message"):
        result = asyncio.run(chat_router.send_message(
            conversation_id=1,
            payload={"content": "reply", "quote_message_id": 1},
            user=carol,
            db=community_block_db,
        ))

    delivered = {call.args[0]: call.args[2]["data"] for call in sent.await_args_list}
    assert result["data"]["quote_preview"] == "hidden"
    assert delivered[1]["quote_preview"] == "消息已撤回"
    assert delivered[2]["quote_preview"] == "hidden"


def test_generic_ws_community_send_excludes_blocked_recipient(community_block_db, monkeypatch):
    enqueue = patch.object(ws_router.ws_manager, "enqueue_push_batch")
    monkeypatch.setattr(ws_router, "_get_db_session", sessionmaker(bind=community_block_db.get_bind(), autoflush=False))
    monkeypatch.setattr(ws_router.ws_manager, "check_and_record_dedup", AsyncMock(return_value=False))
    monkeypatch.setattr(ws_router.ws_manager, "get_current_seq", AsyncMock(return_value=0))
    monkeypatch.setattr(ws_router.ws_manager, "send_raw", AsyncMock())
    monkeypatch.setattr(ws_router.ws_manager, "send_with_seq", AsyncMock())
    monkeypatch.setattr(ws_router.ws_manager, "invalidate_participant_caches", lambda ids: None)

    with enqueue as queued, patch("app.services.notification_service.NotificationService.push_message") as notify:
        asyncio.run(ws_router._handle_send_message(
            websocket=None,
            user_id=1,
            data={"payload": {"conversation_id": 1, "content": "generic hello"}},
        ))

    queued_ids = {item[0] for item in queued.call_args.args[0]}
    assert queued_ids == {3}
    assert {call.args[0] for call in notify.call_args_list} == {3}


def test_generic_ws_community_quote_preview_is_pairwise_per_recipient(community_block_db, monkeypatch):
    enqueue = patch.object(ws_router.ws_manager, "enqueue_push_batch")
    monkeypatch.setattr(
        ws_router,
        "_get_db_session",
        sessionmaker(bind=community_block_db.get_bind(), autoflush=False),
    )
    monkeypatch.setattr(ws_router.ws_manager, "check_and_record_dedup", AsyncMock(return_value=False))
    monkeypatch.setattr(ws_router.ws_manager, "get_current_seq", AsyncMock(return_value=0))
    monkeypatch.setattr(ws_router.ws_manager, "send_raw", AsyncMock())
    monkeypatch.setattr(ws_router.ws_manager, "send_with_seq", AsyncMock())
    monkeypatch.setattr(ws_router.ws_manager, "invalidate_participant_caches", lambda ids: None)

    with enqueue as queued, patch("app.services.notification_service.NotificationService.push_message"):
        asyncio.run(ws_router._handle_send_message(
            websocket=None,
            user_id=3,
            data={"payload": {"conversation_id": 1, "content": "reply", "quote_message_id": 1}},
        ))

    delivered = {uid: payload for uid, _event, payload in queued.call_args.args[0]}
    assert delivered[1]["quote_preview"] == "消息已撤回"
    assert delivered[2]["quote_preview"] == "hidden"


def test_community_http_quote_preview_is_pairwise_per_recipient(community_block_db, monkeypatch):
    carol = community_block_db.get(User, 3)
    sent = AsyncMock()
    monkeypatch.setattr(communities_router.ws_manager, "is_connected", lambda user_id: True)
    monkeypatch.setattr(communities_router.ws_manager, "send_with_seq", sent)

    result = asyncio.run(communities_router.send_community_message(
        community_id=1,
        payload={"content": "reply", "quote_message_id": 1},
        user=carol,
        db=community_block_db,
    ))

    delivered = {call.args[0]: call.args[2]["message"] for call in sent.await_args_list}
    assert result["message"]["quote_preview"] == "hidden"
    assert delivered[1]["quote_preview"] == "消息已撤回"
    assert delivered[2]["quote_preview"] == "hidden"


def test_community_send_excludes_blocked_delivery_and_mentions(community_block_db, monkeypatch):
    alice = community_block_db.get(User, 1)
    sent = AsyncMock()
    monkeypatch.setattr(communities_router.ws_manager, "is_connected", lambda user_id: True)
    monkeypatch.setattr(communities_router.ws_manager, "send_with_seq", sent)

    with patch("app.services.notification_service.NotificationService.create_notification") as notify:
        result = asyncio.run(communities_router.send_community_message(
            community_id=1,
            payload={"content": "hello", "mention_user_ids": [2, 3]},
            user=alice,
            db=community_block_db,
        ))

    delivered_ids = {call.args[0] for call in sent.await_args_list}
    assert delivered_ids == {1, 3}
    assert {call.kwargs["user_id"] for call in notify.call_args_list} == {3}
    assert result["message"]["sender_id"] == 1
