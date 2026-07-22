import asyncio
from types import SimpleNamespace

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

    async def send_raw(self, user_id, payload):
        self.raw.append((user_id, payload))


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


def test_ws_rejection_is_failed_ack_and_has_no_dedup_side_effect(monkeypatch):
    conv = SimpleNamespace(id=7, type="single", community_id=None)
    manager = FakeWSManager()
    dedup_events = []

    def reject(*args, **kwargs):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "CONTENT_REJECTED",
                "message": "内容未通过审核",
                "retryable": False,
            },
        )

    async def should_not_record_dedup(*args, **kwargs):
        dedup_events.append(args)
        return False

    monkeypatch.setattr(ws, "ws_manager", manager)
    monkeypatch.setattr(ws, "_get_db_session", lambda: FakeDB(conv))
    monkeypatch.setattr(ws, "_get_dedup_state", lambda client_msg_id: None)
    monkeypatch.setattr(ws, "can_access_conversation", lambda *args, **kwargs: True)
    monkeypatch.setattr(ws, "get_active_conversation_participant_ids", lambda *args, **kwargs: [1, 2])
    monkeypatch.setattr(ws, "_can_send_to_participants", lambda *args, **kwargs: True)
    monkeypatch.setattr(ws, "moderate_route_fields", reject)
    monkeypatch.setattr(manager, "check_and_record_dedup", should_not_record_dedup, raising=False)

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
    assert dedup_events == []
    assert "message_id" not in manager.raw[0][1]


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
