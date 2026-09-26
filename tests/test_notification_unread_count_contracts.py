import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import ws_manager as ws_manager_module
from app.database import Base
from app.models.models import Block, Notification, User
from app.routers import notifications as notifications_router
from app.routers import ws as ws_router
from app.services import notification_query_service, notification_service
from app.services.notification_service import NotificationService


@pytest.fixture()
def notification_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    db = Session()
    db.info["session_factory"] = Session
    try:
        db.add_all(
            [
                User(id=1, username="recipient", email="recipient@example.com", password_hash="x"),
                User(id=2, username="blocked", email="blocked@example.com", password_hash="x"),
                User(id=3, username="visible", email="visible@example.com", password_hash="x"),
                User(id=4, username="other", email="other@example.com", password_hash="x"),
                Block(blocker_id=1, blocked_id=2),
                Notification(
                    id=1,
                    user_id=1,
                    sender_id=2,
                    notification_type="mention",
                    title="hidden blocked notification",
                    is_read=False,
                ),
                Notification(
                    id=2,
                    user_id=1,
                    sender_id=3,
                    notification_type="mention",
                    title="visible user notification",
                    is_read=False,
                ),
                Notification(
                    id=3,
                    user_id=1,
                    sender_id=None,
                    notification_type="system",
                    title="visible system notification",
                    is_read=False,
                ),
                Notification(
                    id=4,
                    user_id=1,
                    sender_id=3,
                    notification_type="mention",
                    title="visible read notification",
                    is_read=True,
                ),
                Notification(
                    id=5,
                    user_id=4,
                    sender_id=3,
                    notification_type="mention",
                    title="other recipient notification",
                    is_read=False,
                ),
            ]
        )
        db.commit()
        yield db
    finally:
        db.close()
        engine.dispose()


def _run_ws_notifications_read(notification_db, monkeypatch, payload):
    sent = []
    Session = notification_db.info["session_factory"]

    async def fake_send_with_seq(user_id, event, event_payload):
        sent.append((user_id, event, event_payload))

    monkeypatch.setattr(ws_router, "_get_db_session", Session)
    monkeypatch.setattr(ws_router.ws_manager, "send_with_seq", fake_send_with_seq)

    asyncio.run(
        ws_router._handle_notifications_read(
            websocket=None,
            user_id=1,
            payload=payload,
        )
    )
    notification_db.expire_all()
    return sent


def test_ws_notifications_read_cannot_mark_blocked_sender_notification(
    notification_db, monkeypatch
):
    sent = _run_ws_notifications_read(
        notification_db, monkeypatch, {"notification_ids": [1]}
    )

    assert notification_db.get(Notification, 1).is_read is False
    assert sent == [
        (1, "notifications_read", {"notification_ids": [1], "unread_count": 2})
    ]


def test_ws_notifications_read_marks_visible_notification(notification_db, monkeypatch):
    _run_ws_notifications_read(
        notification_db, monkeypatch, {"notification_ids": [2]}
    )

    assert notification_db.get(Notification, 2).is_read is True


def test_ws_notifications_read_delivery_failure_keeps_committed_visible_state(
    notification_db, monkeypatch
):
    Session = notification_db.info["session_factory"]

    async def failing_send_with_seq(user_id, event, payload):
        raise RuntimeError("websocket unavailable")

    monkeypatch.setattr(ws_router, "_get_db_session", Session)
    monkeypatch.setattr(
        ws_router.ws_manager,
        "send_with_seq",
        failing_send_with_seq,
    )

    async def mark_notification_read():
        await ws_router._handle_notifications_read(
            websocket=None,
            user_id=1,
            payload={"notification_ids": [2]},
        )

    asyncio.run(mark_notification_read())

    notification_db.expire_all()
    assert notification_db.get(Notification, 2).is_read is True
    assert notification_db.get(Notification, 1).is_read is False
    assert notification_query_service.visible_unread_count(notification_db, 1) == 1


def test_ws_notifications_read_pushes_visible_unread_count_including_system_sender(
    notification_db, monkeypatch
):
    sent = _run_ws_notifications_read(
        notification_db, monkeypatch, {"notification_ids": [2]}
    )

    assert notification_db.get(Notification, 1).is_read is False
    assert notification_db.get(Notification, 3).is_read is False
    assert sent == [
        (1, "notifications_read", {"notification_ids": [2], "unread_count": 1})
    ]


def test_query_service_excludes_blocked_senders_but_keeps_null_system_senders(notification_db):
    visible_ids = {
        notification.id
        for notification in notification_query_service.visible_notification_query(
            notification_db, 1
        ).all()
    }

    assert visible_ids == {2, 3, 4}
    assert notification_query_service.visible_unread_count(notification_db, 1) == 2


def test_notification_list_and_unread_endpoint_share_visible_unread_behavior(notification_db):
    user = notification_db.get(User, 1)

    result = notifications_router.get_notifications(
        page=1,
        per_page=20,
        unread_only=False,
        user=user,
        db=notification_db,
    )
    unread_result = notifications_router.get_unread_count(user=user, db=notification_db)

    assert {item["id"] for item in result["notifications"]} == {2, 3, 4}
    assert result["total"] == 3
    assert result["unread_count"] == 2
    assert unread_result == {"unread_count": 2}


def test_mark_as_read_does_not_expose_a_blocked_sender_notification(notification_db):
    user = notification_db.get(User, 1)

    with pytest.raises(notifications_router.HTTPException) as exc_info:
        asyncio.run(
            notifications_router.mark_as_read(
                notification_id=1,
                user=user,
                db=notification_db,
            )
        )

    assert exc_info.value.status_code == 404
    assert notification_db.get(Notification, 1).is_read is False


def test_mark_as_read_decrements_visible_unread_count_with_autoflush_disabled(
    notification_db, monkeypatch
):
    assert notification_db.autoflush is False
    sent = []

    async def fake_send_with_seq(user_id, event, payload):
        sent.append((user_id, event, payload))

    monkeypatch.setattr(notifications_router.ws_manager, "send_with_seq", fake_send_with_seq)
    user = notification_db.get(User, 1)

    result = asyncio.run(
        notifications_router.mark_as_read(
            notification_id=2,
            user=user,
            db=notification_db,
        )
    )

    assert result["unread_count"] == 1
    assert sent == [
        (1, "notifications_read", {"notification_ids": [2], "unread_count": 1})
    ]


def test_mark_as_read_ws_failure_still_succeeds_and_keeps_committed_state(
    notification_db, monkeypatch
):
    async def failing_send_with_seq(user_id, event, payload):
        raise RuntimeError("websocket unavailable")

    monkeypatch.setattr(
        notifications_router.ws_manager,
        "send_with_seq",
        failing_send_with_seq,
    )
    user = notification_db.get(User, 1)

    result = asyncio.run(
        notifications_router.mark_as_read(
            notification_id=2,
            user=user,
            db=notification_db,
        )
    )

    assert result["message"] == "Notification marked as read"
    assert result["unread_count"] == 1
    notification_db.expire_all()
    assert notification_db.get(Notification, 2).is_read is True


def test_http_mark_all_reads_visible_rows_but_leaves_blocked_row_unread(
    notification_db, monkeypatch
):
    sent = []

    async def fake_send_with_seq(user_id, event, payload):
        sent.append((user_id, event, payload))

    monkeypatch.setattr(notifications_router.ws_manager, "send_with_seq", fake_send_with_seq)
    user = notification_db.get(User, 1)

    result = asyncio.run(notifications_router.mark_all_as_read(user=user, db=notification_db))

    notification_db.expire_all()
    assert notification_db.get(Notification, 1).is_read is False
    assert notification_db.get(Notification, 2).is_read is True
    assert notification_db.get(Notification, 3).is_read is True
    assert result["unread_count"] == 0
    assert sent == [(1, "notifications_read", {"unread_count": 0})]


def test_mark_all_ws_failure_still_succeeds_and_keeps_committed_state(
    notification_db, monkeypatch
):
    async def failing_send_with_seq(user_id, event, payload):
        raise RuntimeError("websocket unavailable")

    monkeypatch.setattr(
        notifications_router.ws_manager,
        "send_with_seq",
        failing_send_with_seq,
    )
    user = notification_db.get(User, 1)

    result = asyncio.run(
        notifications_router.mark_all_as_read(user=user, db=notification_db)
    )

    assert result == {"message": "All notifications marked as read", "unread_count": 0}
    notification_db.expire_all()
    assert notification_db.get(Notification, 1).is_read is False
    assert notification_db.get(Notification, 2).is_read is True
    assert notification_db.get(Notification, 3).is_read is True


def test_ws_mark_all_reads_visible_rows_but_leaves_blocked_row_unread(
    notification_db, monkeypatch
):
    sent = _run_ws_notifications_read(
        notification_db, monkeypatch, {"mark_all": True}
    )

    assert notification_db.get(Notification, 1).is_read is False
    assert notification_db.get(Notification, 2).is_read is True
    assert notification_db.get(Notification, 3).is_read is True
    assert sent == [
        (1, "notifications_read", {"notification_ids": [], "unread_count": 0})
    ]


def test_new_notification_ws_payload_uses_visible_unread_count(notification_db, monkeypatch):
    scheduled = []
    sent = []

    async def fake_send_with_seq(user_id, event, payload):
        sent.append((user_id, event, payload))

    def fake_schedule_push(coro):
        scheduled.append(coro)

    monkeypatch.setattr(notification_service, "SessionLocal", lambda: notification_db)
    monkeypatch.setattr(notifications_router.ws_manager, "schedule_push", fake_schedule_push)
    monkeypatch.setattr(notifications_router.ws_manager, "send_with_seq", fake_send_with_seq)
    monkeypatch.setattr(
        notifications_router.ws_manager,
        "get_blocked_user_ids",
        lambda user_id: set(),
    )

    NotificationService._push_new_notification(
        1,
        {
            "id": 3,
            "user_id": 1,
            "sender_id": None,
            "notification_type": "system",
            "title": "visible system notification",
        },
    )
    asyncio.run(scheduled.pop())

    assert sent == [
        (
            1,
            "new_notification",
            {
                "notification": {
                    "id": 3,
                    "user_id": 1,
                    "sender_id": None,
                    "notification_type": "system",
                    "title": "visible system notification",
                    "sender": None,
                },
                "unread_count": 2,
            },
        )
    ]


def test_new_notification_ws_is_not_delivered_for_db_block_when_cache_is_stale(
    notification_db, monkeypatch
):
    scheduled = []
    sent = []

    async def fake_send_with_seq(user_id, event, payload):
        sent.append((user_id, event, payload))

    def fake_schedule_push(coro):
        scheduled.append(coro)

    monkeypatch.setattr(notification_service, "SessionLocal", lambda: notification_db)
    monkeypatch.setattr(notifications_router.ws_manager, "schedule_push", fake_schedule_push)
    monkeypatch.setattr(notifications_router.ws_manager, "send_with_seq", fake_send_with_seq)
    monkeypatch.setattr(
        notifications_router.ws_manager,
        "get_blocked_user_ids",
        lambda user_id: set(),
    )

    NotificationService._push_new_notification(
        1,
        {
            "id": 1,
            "user_id": 1,
            "sender_id": 2,
            "notification_type": "mention",
            "title": "hidden blocked notification",
        },
    )
    asyncio.run(scheduled.pop())

    assert sent == []


def test_system_notification_remains_visible_and_readable(notification_db, monkeypatch):
    sent = []

    async def fake_send_with_seq(user_id, event, payload):
        sent.append((user_id, event, payload))

    monkeypatch.setattr(notifications_router.ws_manager, "send_with_seq", fake_send_with_seq)
    user = notification_db.get(User, 1)

    result = asyncio.run(
        notifications_router.mark_as_read(
            notification_id=3,
            user=user,
            db=notification_db,
        )
    )

    assert result["notification"]["id"] == 3
    assert result["unread_count"] == 1
    assert notification_db.get(Notification, 3).is_read is True
    assert sent == [
        (1, "notifications_read", {"notification_ids": [3], "unread_count": 1})
    ]


def test_notification_list_unread_count_is_global_when_filtered_and_paginated(notification_db):
    user = notification_db.get(User, 1)

    result = notifications_router.get_notifications(
        page=2,
        per_page=1,
        unread_only=True,
        user=user,
        db=notification_db,
    )

    assert len(result["notifications"]) == 1
    assert result["notifications"][0]["id"] in {2, 3}
    assert result["notifications"][0]["id"] != 1
    assert result["total"] == 2
    assert result["pages"] == 2
    assert result["current_page"] == 2
    assert result["has_more"] is False
    assert result["unread_count"] == 2


def test_ws_sync_omits_notification_hidden_after_log_but_replays_system_notification(
    notification_db, monkeypatch
):
    Session = notification_db.info["session_factory"]
    notification_db.query(Block).filter(
        Block.blocker_id == 1,
        Block.blocked_id == 2,
    ).delete(synchronize_session=False)
    notification_db.commit()

    monkeypatch.setattr(ws_manager_module, "SessionLocal", Session)
    hidden_seq = ws_router.ws_manager._next_seq_sync(
        1,
        {
            "event": "new_notification",
            "data": {
                "notification": {
                    "id": 1,
                    "sender_id": 2,
                    "title": "secret title",
                    "content": "secret content",
                },
                "unread_count": 3,
            },
        },
    )
    system_seq = ws_router.ws_manager._next_seq_sync(
        1,
        {
            "event": "new_notification",
            "data": {
                "notification": {
                    "id": 3,
                    "sender_id": None,
                    "title": "visible system notification",
                },
                "unread_count": 3,
            },
        },
    )
    assert (hidden_seq, system_seq) == (1, 2)

    notification_db.add(Block(blocker_id=1, blocked_id=2))
    notification_db.commit()
    sent = []

    async def fake_send_raw(user_id, payload):
        sent.append((user_id, payload))

    monkeypatch.setattr(ws_router.ws_manager, "send_raw", fake_send_raw)
    asyncio.run(
        ws_router._handle_sync(
            websocket=None,
            user_id=1,
            data={
                "request_id": "sync-hidden",
                "payload": {"last_received_seq": 0, "limit": 200},
            },
        )
    )

    assert len(sent) == 1
    user_id, response = sent[0]
    assert user_id == 1
    assert response["type"] == "sync_result"
    assert response["request_id"] == "sync-hidden"
    assert response["payload"]["current_max_seq"] == 2
    assert response["payload"]["count"] == 1
    assert response["payload"]["list"] == [
        {
            "seq": 2,
            "payload": {
                "event": "new_notification",
                "data": {
                    "notification": {
                        "id": 3,
                        "sender_id": None,
                        "title": "visible system notification",
                    },
                    "unread_count": 3,
                },
            },
        }
    ]
    assert "secret title" not in json.dumps(response)
    assert "secret content" not in json.dumps(response)


def test_ws_sync_scans_past_hidden_rows_to_preserve_visible_page_contract(
    notification_db, monkeypatch
):
    Session = notification_db.info["session_factory"]
    monkeypatch.setattr(ws_manager_module, "SessionLocal", Session)
    ws_router.ws_manager._next_seq_sync(
        1,
        {
            "event": "new_notification",
            "data": {
                "notification": {
                    "id": 1,
                    "title": "hidden page-boundary content",
                }
            },
        },
    )
    ws_router.ws_manager._next_seq_sync(
        1,
        {"event": "friend_online", "data": {"user_id": 3}},
    )
    sent = []

    async def fake_send_raw(user_id, payload):
        sent.append(payload)

    monkeypatch.setattr(ws_router.ws_manager, "send_raw", fake_send_raw)
    asyncio.run(
        ws_router._handle_sync(
            websocket=None,
            user_id=1,
            data={"payload": {"last_received_seq": 0, "limit": 1}},
        )
    )

    assert sent[0]["payload"]["list"] == [
        {
            "seq": 2,
            "payload": {"event": "friend_online", "data": {"user_id": 3}},
        }
    ]
    assert sent[0]["payload"]["has_more"] is True


def test_ws_sync_fails_closed_when_notification_visibility_lookup_errors(
    notification_db, monkeypatch
):
    Session = notification_db.info["session_factory"]
    monkeypatch.setattr(ws_manager_module, "SessionLocal", Session)
    ws_router.ws_manager._next_seq_sync(
        1,
        {
            "event": "new_notification",
            "data": {
                "notification": {
                    "id": 3,
                    "title": "must not leak on visibility error",
                }
            },
        },
    )
    ws_router.ws_manager._next_seq_sync(
        1,
        {"event": "friend_online", "data": {"user_id": 3}},
    )

    def fail_visibility_lookup(db, user_id, notification_ids):
        raise RuntimeError("visibility unavailable")

    monkeypatch.setattr(
        ws_manager_module,
        "visible_notification_ids",
        fail_visibility_lookup,
        raising=False,
    )

    messages = asyncio.run(ws_router.ws_manager.get_messages_after_seq(1, 0))

    assert messages == [
        {
            "seq": 2,
            "payload": {"event": "friend_online", "data": {"user_id": 3}},
        }
    ]


def test_delete_hidden_notification_returns_404_and_preserves_row(notification_db):
    user = notification_db.get(User, 1)

    with pytest.raises(notifications_router.HTTPException) as exc_info:
        notifications_router.delete_notification(
            notification_id=1,
            user=user,
            db=notification_db,
        )

    assert exc_info.value.status_code == 404
    assert notification_db.get(Notification, 1) is not None


@pytest.mark.parametrize("notification_id", [2, 3])
def test_delete_visible_or_system_notification_succeeds(notification_db, notification_id):
    user = notification_db.get(User, 1)

    result = notifications_router.delete_notification(
        notification_id=notification_id,
        user=user,
        db=notification_db,
    )

    assert result == {"message": "Notification deleted successfully"}
    assert notification_db.get(Notification, notification_id) is None


def test_clear_all_deletes_only_visible_notifications(notification_db):
    user = notification_db.get(User, 1)

    result = notifications_router.clear_all_notifications(user=user, db=notification_db)

    assert result == {"message": "All notifications cleared"}
    assert notification_db.get(Notification, 1) is not None
    assert notification_db.get(Notification, 2) is None
    assert notification_db.get(Notification, 3) is None
    assert notification_db.get(Notification, 4) is None
    assert notification_db.get(Notification, 5) is not None


def test_clear_all_static_route_precedes_notification_id_route():
    notification_paths = [route.path for route in notifications_router.router.routes]

    assert notification_paths.index("/clear-all") < notification_paths.index(
        "/{notification_id}"
    )
