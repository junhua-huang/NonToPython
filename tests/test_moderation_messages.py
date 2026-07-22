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


class FakeDB:
    def __init__(self, row):
        self.row = row
        self.closed = False

    def query(self, *args, **kwargs):
        return FakeQuery(self.row)

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
    monkeypatch.setattr(ws, "_get_dedup_state", lambda client_msg_id: None)
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
    monkeypatch.setattr(ws, "_get_dedup_state", lambda client_msg_id: {"user_id": 1, "message_id": 99})
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
    monkeypatch.setattr(ws, "_get_dedup_state", lambda client_msg_id: {"user_id": 1, "message_id": None})
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



def test_ws_dedup_lookup_failure_is_retryable_failed_ack(monkeypatch):
    manager = FakeWSManager()

    def fail_dedup_lookup(client_msg_id):
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
    conv = SimpleNamespace(id=7, type="single", community_id=None)
    manager = FakeWSManager()
    opened_sessions = []
    moderation_events = []

    async def pending_duplicate(*args, **kwargs):
        manager.side_effects.append(("check_and_record_dedup", args))
        return True

    async def pending_message_id(*args, **kwargs):
        manager.side_effects.append(("get_dedup_message_id", args))
        return None

    def session_factory():
        session = FakeDB(conv)
        opened_sessions.append(session)
        return session

    def moderate(*args, **kwargs):
        moderation_events.append(args)

    monkeypatch.setattr(ws, "ws_manager", manager)
    monkeypatch.setattr(ws, "_get_db_session", session_factory)
    monkeypatch.setattr(ws, "_get_dedup_state", lambda client_msg_id: None)
    monkeypatch.setattr(ws, "can_access_conversation", lambda *args, **kwargs: True)
    monkeypatch.setattr(ws, "get_active_conversation_participant_ids", lambda *args, **kwargs: [1, 2])
    monkeypatch.setattr(ws, "_can_send_to_participants", lambda *args, **kwargs: True)
    monkeypatch.setattr(ws, "moderate_route_fields", moderate)
    monkeypatch.setattr(manager, "check_and_record_dedup", pending_duplicate, raising=False)
    monkeypatch.setattr(manager, "get_dedup_message_id", pending_message_id, raising=False)

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
    assert manager.side_effects == [
        ("check_and_record_dedup", (1, "client-late-pending")),
        ("get_dedup_message_id", ("client-late-pending",)),
    ]
    assert len(opened_sessions) == 1
    assert opened_sessions[0].closed is True
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

    async def reservation_unavailable(*args, **kwargs):
        manager.side_effects.append(("check_and_record_dedup", args))
        return None

    def session_factory():
        session = FakeDB(conv)
        opened_sessions.append(session)
        return session

    monkeypatch.setattr(ws, "ws_manager", manager)
    monkeypatch.setattr(ws, "_get_db_session", session_factory)
    monkeypatch.setattr(ws, "_get_dedup_state", lambda client_msg_id: None)
    monkeypatch.setattr(ws, "can_access_conversation", lambda *args, **kwargs: True)
    monkeypatch.setattr(ws, "get_active_conversation_participant_ids", lambda *args, **kwargs: [1, 2])
    monkeypatch.setattr(ws, "_can_send_to_participants", lambda *args, **kwargs: True)
    monkeypatch.setattr(ws, "moderate_route_fields", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager, "check_and_record_dedup", reservation_unavailable, raising=False)

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

    assert manager.side_effects == [("check_and_record_dedup", (1, "client-reserve-fail"))]
    assert len(opened_sessions) == 1
    assert opened_sessions[0].closed is True
    assert manager.raw[-1][1]["status"] == 503
    assert manager.raw[-1][1]["code"] == "MODERATION_UNAVAILABLE"
    assert "message_id" not in manager.raw[-1][1]



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
    monkeypatch.setattr(ws, "_get_dedup_state", lambda client_msg_id: None)
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
