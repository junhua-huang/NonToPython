import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import communities, ws


class FakeQuery:
    def __init__(self, row):
        self.row = row

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.row

    def count(self):
        return 0

    def all(self):
        return []

    def delete(self, *args, **kwargs):
        return 0


class FakeDB:
    def __init__(self, row, *, dedup_row=None, dedup_rows=None, flush_error=None):
        self.row = row
        self.dedup_row = dedup_row
        self.dedup_rows = list(dedup_rows or [])
        self.flush_error = flush_error
        self.closed = False
        self.added = []
        self.committed = False
        self.rolled_back = False

    def query(self, *args, **kwargs):
        model = args[0] if args else None
        if getattr(model, "__name__", "") == "WSAckDedup":
            if self.dedup_rows:
                return FakeQuery(self.dedup_rows.pop(0))
            return FakeQuery(self.dedup_row)
        return FakeQuery(self.row)

    def add(self, item):
        self.added.append(item)

    def flush(self):
        if self.flush_error:
            raise self.flush_error
        for item in self.added:
            if getattr(item.__class__, "__name__", "") == "Message":
                item.id = 77

    def commit(self):
        self.committed = True

    def refresh(self, item):
        if getattr(item, "id", None) is None:
            item.id = 77

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


class FakeWSManager:
    def __init__(self):
        self.raw = []
        self.side_effects = []

    async def send_raw(self, user_id, payload):
        self.raw.append((user_id, payload))

    async def check_and_record_dedup(self, *args, **kwargs):
        self.side_effects.append(("check_and_record_dedup", args))
        return False

    async def get_dedup_message_id(self, *args, **kwargs):
        self.side_effects.append(("get_dedup_message_id", args))
        return None

    async def get_current_seq(self, *args, **kwargs):
        self.side_effects.append(("get_current_seq", args))
        return 0

    async def update_dedup_message_id(self, *args, **kwargs):
        self.side_effects.append(("update_dedup_message_id", args))

    async def send_with_seq(self, *args, **kwargs):
        self.side_effects.append(("send_with_seq", args))

    def enqueue_push_batch(self, *args, **kwargs):
        self.side_effects.append(("enqueue_push_batch", args))

    def invalidate_participant_caches(self, *args, **kwargs):
        self.side_effects.append(("invalidate_participant_caches", args))


def test_http_community_chat_rejection_has_no_persistence_or_fanout(monkeypatch):
    events = []
    community = SimpleNamespace(id=3, status="active", name="circle")
    member = SimpleNamespace(user_id=1, status="active")
    conversation = SimpleNamespace(id=7, type="community", community_id=3, last_message_at=None)

    class Query:
        def __init__(self, row):
            self.row = row

        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return self.row

        def all(self):
            return []

    class DB:
        def query(self, model):
            if model is communities.Community:
                return Query(community)
            if model is communities.CommunityMember:
                return Query(member)
            if model is communities.Conversation:
                return Query(conversation)
            return Query(None)

        def add(self, item):
            events.append(("db.add", type(item).__name__))

        def commit(self):
            events.append(("db.commit",))

        def refresh(self, item):
            events.append(("db.refresh",))

    def reject(*args, **kwargs):
        events.append(("moderate", args[1], kwargs["actor_user_id"], kwargs["is_public"]))
        raise HTTPException(
            status_code=422,
            detail={
                "code": "CONTENT_REJECTED",
                "message": "内容未通过审核",
                "retryable": False,
            },
        )

    monkeypatch.setattr(
        communities,
        "_normalize_community_message_payload",
        lambda payload, *args, **kwargs: (payload["content"], "text", None, None, []),
    )
    monkeypatch.setattr(communities, "moderate_route_fields", reject)

    try:
        asyncio.run(
            communities.send_community_message(
                3,
                {"content": "blocked", "message_type": "text"},
                user=SimpleNamespace(id=1),
                db=DB(),
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 422
    else:
        raise AssertionError("community chat moderation rejection should abort")

    assert events == [
        (
            "moderate",
            "POST /api/communities/{community_id}/chat/messages",
            1,
            False,
        )
    ]


@pytest.mark.parametrize("conversation_type", ["single", "community"])
def test_ws_rejection_is_failed_ack_and_has_no_dedup_side_effect(monkeypatch, conversation_type):
    conv = SimpleNamespace(id=7, type=conversation_type, community_id=3 if conversation_type == "community" else None)
    manager = FakeWSManager()
    opened_sessions = []

    def reject(*args, **kwargs):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "CONTENT_REJECTED",
                "message": "内容未通过审核",
                "retryable": False,
            },
        )

    def session_factory():
        session = FakeDB(conv)
        opened_sessions.append(session)
        return session

    monkeypatch.setattr(ws, "ws_manager", manager)
    monkeypatch.setattr(ws, "_get_db_session", session_factory)
    monkeypatch.setattr(ws, "_get_dedup_state", lambda *args: None)
    monkeypatch.setattr(ws, "can_access_conversation", lambda *args, **kwargs: True)
    monkeypatch.setattr(ws, "get_active_conversation_participant_ids", lambda *args, **kwargs: [1, 2])
    monkeypatch.setattr(ws, "_can_send_to_participants", lambda *args, **kwargs: True)
    monkeypatch.setattr(ws, "moderate_route_fields", reject)

    asyncio.run(
        ws._handle_send_message(
            None,
            1,
            {
                "type": "send_message",
                "request_id": "req-7",
                "payload": {
                    "client_msg_id": "client-7",
                    "conversation_id": 7,
                    "content": "blocked",
                    "message_type": "text",
                },
            },
        )
    )

    assert manager.raw == [
        (
            1,
            {
                "type": "ack",
                "request_id": "req-7",
                "client_msg_id": "client-7",
                "clientMsgId": "client-7",
                "status": 422,
                "code": "CONTENT_REJECTED",
                "retryable": False,
                "msg": "内容未通过审核",
                "message": "内容未通过审核",
            },
        )
    ]
    assert manager.side_effects == []
    assert len(opened_sessions) == 1
    assert opened_sessions[0].closed is True
    assert "message_id" not in manager.raw[0][1]


def test_ws_same_user_duplicate_ack_bypasses_moderation_and_authorization(monkeypatch):
    manager = FakeWSManager()
    moderation_events = []

    def fail_if_moderated(*args, **kwargs):
        moderation_events.append(args)
        raise AssertionError("duplicate sends must not be moderated again")

    def fail_if_session_opened():
        raise AssertionError("duplicate sends must not open an authorization or persistence session")

    monkeypatch.setattr(ws, "ws_manager", manager)
    monkeypatch.setattr(ws, "_get_dedup_state", lambda *args: {"user_id": 1, "message_id": 99})
    monkeypatch.setattr(ws, "_get_db_session", fail_if_session_opened)
    monkeypatch.setattr(ws, "moderate_route_fields", fail_if_moderated)

    asyncio.run(
        ws._handle_send_message(
            None,
            1,
            {
                "type": "send_message",
                "request_id": "req-dup",
                "payload": {
                    "client_msg_id": "client-dup",
                    "conversation_id": 7,
                    "content": "blocked",
                    "message_type": "text",
                },
            },
        )
    )

    assert moderation_events == []
    assert manager.side_effects == []
    assert manager.raw[0][1]["payload"] == {
        "client_msg_id": "client-dup",
        "server_seq": 0,
        "message_id": 99,
        "status": 200,
        "msg": "duplicate",
    }
    assert manager.raw[1][1] == {
        "type": "ack",
        "clientMsgId": "client-dup",
        "client_msg_id": "client-dup",
        "message_id": 99,
        "server_seq": 0,
    }



def test_ws_incomplete_duplicate_state_is_retryable_failed_ack_without_moderation(monkeypatch):
    manager = FakeWSManager()
    moderation_events = []

    def fail_if_moderated(*args, **kwargs):
        moderation_events.append(args)
        raise AssertionError("incomplete duplicate state must not be moderated again")

    def fail_if_session_opened():
        raise AssertionError("incomplete duplicate state must not open persistence session")

    monkeypatch.setattr(ws, "ws_manager", manager)
    monkeypatch.setattr(ws, "_get_dedup_state", lambda *args: {"user_id": 1, "message_id": None})
    monkeypatch.setattr(ws, "_get_db_session", fail_if_session_opened)
    monkeypatch.setattr(ws, "moderate_route_fields", fail_if_moderated)

    asyncio.run(
        ws._handle_send_message(
            None,
            1,
            {
                "type": "send_message",
                "request_id": "req-pending",
                "payload": {
                    "client_msg_id": "client-pending",
                    "conversation_id": 7,
                    "content": "blocked",
                    "message_type": "text",
                },
            },
        )
    )

    assert moderation_events == []
    assert manager.side_effects == []
    assert manager.raw == [
        (
            1,
            {
                "type": "ack",
                "request_id": "req-pending",
                "client_msg_id": "client-pending",
                "clientMsgId": "client-pending",
                "status": 503,
                "code": "MODERATION_UNAVAILABLE",
                "retryable": True,
                "msg": "内容审核服务暂不可用，请稍后重试",
                "message": "内容审核服务暂不可用，请稍后重试",
            },
        )
    ]



def test_ws_get_dedup_state_is_user_scoped_with_real_sqlite(monkeypatch):
    from datetime import datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import Base
    from app.models.models import User, WSAckDedup

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        db.add_all([
            User(id=1, username="alice", email="alice@example.com", password_hash="x"),
            User(id=2, username="bob", email="bob@example.com", password_hash="x"),
            WSAckDedup(user_id=1, client_msg_id="same-client", message_id=91, processed_at=datetime.utcnow()),
            WSAckDedup(user_id=2, client_msg_id="same-client", message_id=92, processed_at=datetime.utcnow()),
        ])
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(ws, "_get_db_session", Session)

    assert ws._get_dedup_state(1, "same-client") == {"user_id": 1, "message_id": 91}
    assert ws._get_dedup_state(2, "same-client") == {"user_id": 2, "message_id": 92}
    engine.dispose()



def test_ws_expired_dedup_state_is_ignored(monkeypatch):
    from datetime import datetime, timedelta

    row = SimpleNamespace(
        user_id=1,
        client_msg_id="expired-client",
        message_id=99,
        processed_at=datetime.utcnow() - timedelta(hours=25),
    )
    session = FakeDB(row)

    monkeypatch.setattr(ws, "_get_db_session", lambda: session)

    assert ws._get_dedup_state(1, "expired-client") is None
    assert session.closed is True



def test_ws_dedup_lookup_failure_is_retryable_failed_ack(monkeypatch):
    manager = FakeWSManager()

    def fail_dedup_lookup(*args):
        raise RuntimeError("dedup lookup failed")

    def fail_if_session_opened():
        raise AssertionError("dedup lookup failure must fail before authorization or persistence")

    monkeypatch.setattr(ws, "ws_manager", manager)
    monkeypatch.setattr(ws, "_get_dedup_state", fail_dedup_lookup)
    monkeypatch.setattr(ws, "_get_db_session", fail_if_session_opened)

    asyncio.run(
        ws._handle_send_message(
            None,
            1,
            {
                "type": "send_message",
                "request_id": "req-dedup-fail",
                "payload": {
                    "client_msg_id": "client-dedup-fail",
                    "conversation_id": 7,
                    "content": "hello",
                    "message_type": "text",
                },
            },
        )
    )

    ack = manager.raw[-1][1]
    assert ack["type"] == "ack"
    assert ack["status"] == 503
    assert ack["code"] == "MODERATION_UNAVAILABLE"
    assert ack["retryable"] is True
    assert "dedup lookup failed" not in str(ack)
    assert "message_id" not in ack



def test_ws_late_pending_duplicate_is_retryable_failed_ack_without_persistence(monkeypatch):
    pending_row = SimpleNamespace(user_id=1, client_msg_id="client-late-pending", message_id=None)
    conv = SimpleNamespace(id=7, type="single", community_id=None)
    manager = FakeWSManager()
    opened_sessions = []
    moderation_events = []

    def session_factory():
        session = FakeDB(conv, dedup_row=pending_row)
        opened_sessions.append(session)
        return session

    def moderate(*args, **kwargs):
        moderation_events.append(args)

    monkeypatch.setattr(ws, "ws_manager", manager)
    monkeypatch.setattr(ws, "_get_db_session", session_factory)
    monkeypatch.setattr(ws, "_get_dedup_state", lambda *args: None)
    monkeypatch.setattr(ws, "can_access_conversation", lambda *args, **kwargs: True)
    monkeypatch.setattr(ws, "get_active_conversation_participant_ids", lambda *args, **kwargs: [1, 2])
    monkeypatch.setattr(ws, "_can_send_to_participants", lambda *args, **kwargs: True)
    monkeypatch.setattr(ws, "moderate_route_fields", moderate)

    asyncio.run(
        ws._handle_send_message(
            None,
            1,
            {
                "type": "send_message",
                "request_id": "req-late-pending",
                "payload": {
                    "client_msg_id": "client-late-pending",
                    "conversation_id": 7,
                    "content": "hello",
                    "message_type": "text",
                },
            },
        )
    )

    assert len(moderation_events) == 1
    assert manager.side_effects == []
    assert len(opened_sessions) == 2
    assert all(session.closed for session in opened_sessions)
    assert opened_sessions[-1].added == []
    assert manager.raw == [
        (
            1,
            {
                "type": "ack",
                "request_id": "req-late-pending",
                "client_msg_id": "client-late-pending",
                "clientMsgId": "client-late-pending",
                "status": 503,
                "code": "MODERATION_UNAVAILABLE",
                "retryable": True,
                "msg": "内容审核服务暂不可用，请稍后重试",
                "message": "内容审核服务暂不可用，请稍后重试",
            },
        )
    ]



def test_ws_dedup_reservation_failure_is_retryable_failed_ack_without_persistence(monkeypatch):
    conv = SimpleNamespace(id=7, type="single", community_id=None)
    manager = FakeWSManager()
    opened_sessions = []

    def session_factory():
        session = FakeDB(conv, flush_error=ws.SQLAlchemyError("dedup unavailable"))
        opened_sessions.append(session)
        return session

    monkeypatch.setattr(ws, "ws_manager", manager)
    monkeypatch.setattr(ws, "_get_db_session", session_factory)
    monkeypatch.setattr(ws, "_get_dedup_state", lambda *args: None)
    monkeypatch.setattr(ws, "can_access_conversation", lambda *args, **kwargs: True)
    monkeypatch.setattr(ws, "get_active_conversation_participant_ids", lambda *args, **kwargs: [1, 2])
    monkeypatch.setattr(ws, "_can_send_to_participants", lambda *args, **kwargs: True)
    monkeypatch.setattr(ws, "moderate_route_fields", lambda *args, **kwargs: None)

    asyncio.run(
        ws._handle_send_message(
            None,
            1,
            {
                "type": "send_message",
                "request_id": "req-reserve-fail",
                "payload": {
                    "client_msg_id": "client-reserve-fail",
                    "conversation_id": 7,
                    "content": "hello",
                    "message_type": "text",
                },
            },
        )
    )

    assert manager.side_effects == []
    assert len(opened_sessions) == 2
    assert all(session.closed for session in opened_sessions)
    assert opened_sessions[-1].rolled_back is True
    assert manager.raw[-1][1]["status"] == 503
    assert manager.raw[-1][1]["code"] == "MODERATION_UNAVAILABLE"
    assert "message_id" not in manager.raw[-1][1]



def test_ws_same_user_integrity_race_returns_duplicate_success_ack(monkeypatch):
    conv = SimpleNamespace(id=7, type="single", community_id=None)
    manager = FakeWSManager()
    winner_row = SimpleNamespace(
        user_id=1,
        client_msg_id="client-race-same-user",
        message_id=88,
        processed_at=None,
    )
    sessions = iter([
        FakeDB(None),
        FakeDB(conv),
        FakeDB(conv, dedup_rows=[None], flush_error=ws.IntegrityError("INSERT", {}, Exception("race"))),
        FakeDB(None, dedup_row=winner_row),
    ])

    monkeypatch.setattr(ws, "ws_manager", manager)
    monkeypatch.setattr(ws, "_get_db_session", lambda: next(sessions))
    monkeypatch.setattr(ws, "_get_dedup_state", ws._get_dedup_state)
    monkeypatch.setattr(ws, "can_access_conversation", lambda *args, **kwargs: True)
    monkeypatch.setattr(ws, "get_active_conversation_participant_ids", lambda *args, **kwargs: [1, 2])
    monkeypatch.setattr(ws, "_can_send_to_participants", lambda *args, **kwargs: True)
    monkeypatch.setattr(ws, "moderate_route_fields", lambda *args, **kwargs: None)

    asyncio.run(
        ws._handle_send_message(
            None,
            1,
            {
                "type": "send_message",
                "request_id": "req-same-user-race",
                "payload": {
                    "client_msg_id": "client-race-same-user",
                    "conversation_id": 7,
                    "content": "hello",
                    "message_type": "text",
                },
            },
        )
    )

    assert manager.raw[0][1]["payload"] == {
        "client_msg_id": "client-race-same-user",
        "server_seq": 0,
        "message_id": 88,
        "status": 200,
        "msg": "duplicate",
    }
    assert manager.raw[1][1] == {
        "type": "ack",
        "clientMsgId": "client-race-same-user",
        "client_msg_id": "client-race-same-user",
        "message_id": 88,
        "server_seq": 0,
    }



def test_ws_cross_user_late_duplicate_is_persisted_without_id_leak(monkeypatch):
    conv = SimpleNamespace(id=7, type="single", community_id=None)
    manager = FakeWSManager()
    opened_sessions = []

    def session_factory():
        session = FakeDB(conv, dedup_row=None)
        opened_sessions.append(session)
        return session

    monkeypatch.setattr(ws, "ws_manager", manager)
    monkeypatch.setattr(ws, "_get_db_session", session_factory)
    monkeypatch.setattr(ws, "_get_dedup_state", lambda *args: None)
    monkeypatch.setattr(ws, "can_access_conversation", lambda *args, **kwargs: True)
    monkeypatch.setattr(ws, "get_active_conversation_participant_ids", lambda *args, **kwargs: [2])
    monkeypatch.setattr(ws, "_can_send_to_participants", lambda *args, **kwargs: True)
    monkeypatch.setattr(ws, "moderate_route_fields", lambda *args, **kwargs: None)
    monkeypatch.setattr(ws, "validate_quote", lambda *args, **kwargs: None)
    monkeypatch.setattr(ws, "inject_quote_preview", lambda db, data, **kwargs: data)
    monkeypatch.setattr(ws, "redact_unavailable_post_card", lambda *args, **kwargs: None)

    asyncio.run(
        ws._handle_send_message(
            None,
            2,
            {
                "type": "send_message",
                "request_id": "req-race",
                "payload": {
                    "client_msg_id": "client-race",
                    "conversation_id": 7,
                    "content": "hello",
                    "message_type": "text",
                },
            },
        )
    )

    assert len(opened_sessions) == 3
    assert all(session.closed for session in opened_sessions)
    assert any(session.committed for session in opened_sessions)
    assert manager.raw[0][1]["payload"] == {
        "client_msg_id": "client-race",
        "server_seq": 0,
        "message_id": 77,
        "status": 200,
        "msg": "success",
    }
    assert manager.raw[1][1]["message_id"] == 77
    assert "99" not in str(manager.raw)


def test_ws_moderation_unavailable_failed_ack_is_retryable(monkeypatch):
    conv = SimpleNamespace(id=7, type="single", community_id=None)
    manager = FakeWSManager()

    def unavailable(*args, **kwargs):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "MODERATION_UNAVAILABLE",
                "message": "内容审核服务暂不可用，请稍后重试",
                "retryable": True,
            },
        )

    monkeypatch.setattr(ws, "ws_manager", manager)
    monkeypatch.setattr(ws, "_get_db_session", lambda: FakeDB(conv))
    monkeypatch.setattr(ws, "_get_dedup_state", lambda *args: None)
    monkeypatch.setattr(ws, "can_access_conversation", lambda *args, **kwargs: True)
    monkeypatch.setattr(ws, "get_active_conversation_participant_ids", lambda *args, **kwargs: [1, 2])
    monkeypatch.setattr(ws, "_can_send_to_participants", lambda *args, **kwargs: True)
    monkeypatch.setattr(ws, "moderate_route_fields", unavailable)

    asyncio.run(
        ws._handle_send_message(
            None,
            1,
            {
                "type": "send_message",
                "request_id": "req-8",
                "payload": {
                    "client_msg_id": "client-8",
                    "conversation_id": 7,
                    "content": "hello",
                    "message_type": "text",
                },
            },
        )
    )

    ack = manager.raw[-1][1]
    assert ack["type"] == "ack"
    assert ack["status"] == 503
    assert ack["code"] == "MODERATION_UNAVAILABLE"
    assert ack["retryable"] is True
    assert "message_id" not in ack
