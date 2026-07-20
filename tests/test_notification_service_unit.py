import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import object_session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.services import notification_service
from app.services.notification_service import NotificationService


class FakeQuery:
    def __init__(self, result):
        self.result = result

    def filter(self, *args):
        return self

    def first(self):
        return self.result


class TrackingSession:
    def __init__(self, query_result=None):
        self.query_result = query_result
        self.close_calls = 0

    @property
    def closed(self):
        return self.close_calls > 0

    def query(self, model):
        assert not self.closed
        return FakeQuery(self.query_result)

    def close(self):
        self.close_calls += 1


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("notify_like", (10, 20, 30)),
        ("notify_comment", (10, 20, 30, "comment")),
        ("notify_friend_request", (10, 20)),
        ("notify_friend_accepted", (10, 20)),
        ("notify_message", (10, 20, "message", 30)),
        ("notify_mention", (10, 20, 30, "mention context")),
    ],
)
def test_owned_helper_session_stays_open_through_create_and_closes_once(
    method_name, args, monkeypatch
):
    session = TrackingSession(SimpleNamespace(username="sender"))
    create_calls = []

    def fake_create_notification(**kwargs):
        assert kwargs["db"] is session
        assert not session.closed
        create_calls.append(kwargs)

    monkeypatch.setattr(NotificationService, "_get_session", staticmethod(lambda: session))
    monkeypatch.setattr(
        NotificationService,
        "create_notification",
        staticmethod(fake_create_notification),
    )

    getattr(NotificationService, method_name)(*args)

    assert len(create_calls) == 1
    assert session.close_calls == 1


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("notify_like", (10, 20, 30)),
        ("notify_comment", (10, 20, 30, "comment")),
    ],
)
def test_caller_owned_helper_session_is_not_closed(method_name, args, monkeypatch):
    session = TrackingSession(SimpleNamespace(username="sender"))

    def fake_create_notification(**kwargs):
        assert kwargs["db"] is session
        assert not session.closed

    monkeypatch.setattr(
        NotificationService,
        "create_notification",
        staticmethod(fake_create_notification),
    )

    getattr(NotificationService, method_name)(*args, db=session)

    assert session.close_calls == 0


def test_create_notification_delivers_plain_dict_without_closing_caller_session(monkeypatch):
    session = Mock()
    ws_payloads = []
    mobile_payloads = []

    def flush():
        notification = session.add.call_args.args[0]
        notification.id = 99
        notification.is_read = False

    session.flush.side_effect = flush
    monkeypatch.setattr(
        NotificationService,
        "_push_new_notification",
        staticmethod(lambda user_id, payload: ws_payloads.append((user_id, payload))),
    )
    monkeypatch.setattr(
        notification_service.AliyunPushService,
        "schedule_notification_push",
        lambda user_id, payload: mobile_payloads.append((user_id, payload)),
    )

    result = NotificationService.create_notification(
        user_id=10,
        notification_type="system",
        title="title",
        content="content",
        db=session,
    )

    expected_payload = result.to_dict()
    assert ws_payloads == [(10, expected_payload)]
    assert mobile_payloads == [(10, expected_payload)]
    session.flush.assert_called_once_with()
    session.refresh.assert_not_called()
    session.close.assert_not_called()


def test_create_notification_commits_before_delivery_without_reopening_caller_transaction(
    monkeypatch,
):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    db = Session()
    delivered = []

    monkeypatch.setattr(
        NotificationService,
        "_push_new_notification",
        staticmethod(lambda user_id, payload: None),
    )

    def capture_mobile_delivery(user_id, payload):
        assert db.in_transaction() is False
        assert isinstance(payload, dict)
        assert payload["id"] is not None
        delivered.append((user_id, payload))

    monkeypatch.setattr(
        notification_service.AliyunPushService,
        "schedule_notification_push",
        capture_mobile_delivery,
    )

    try:
        result = NotificationService.create_notification(
            user_id=10,
            notification_type="system",
            title="title",
            content="content",
            db=db,
        )

        assert result is not None
        assert object_session(result) is db
        assert delivered[0][0] == 10
        assert delivered[0][1]["title"] == "title"
        assert db.in_transaction() is False
    finally:
        db.close()
        engine.dispose()


def test_create_notification_logs_sanitized_db_failure_without_raw_content(
    monkeypatch, caplog
):
    secret_title = "SECRET_TITLE_3e8866"
    secret_content = "SECRET_CONTENT_1ce752"
    session = Mock()
    persistence_error = RuntimeError(
        f"INSERT title={secret_title} content={secret_content} params={secret_content}"
    )
    persistence_error.code = f"unsafe-{secret_title}"
    session.flush.side_effect = persistence_error

    with caplog.at_level(logging.ERROR, logger=notification_service.__name__):
        result = NotificationService.create_notification(
            user_id=10,
            notification_type="system",
            title=secret_title,
            content=secret_content,
            db=session,
        )

    assert result is None
    session.rollback.assert_called_once_with()
    assert "notification persistence failed" in caplog.text
    assert "error_code=DB_PERSISTENCE_ERROR" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert secret_title not in caplog.text
    assert secret_content not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize(
    ("failure_stage", "expected_code"),
    [
        ("websocket", "WS_SCHEDULING_ERROR"),
        ("mobile", "PUSH_SCHEDULING_ERROR"),
    ],
)
def test_create_notification_best_effort_exceptions_log_only_safe_diagnostics(
    monkeypatch, caplog, failure_stage, expected_code
):
    private_phrase = "private violet parrot medical note 7429"
    session = Mock()

    def flush():
        notification = session.add.call_args.args[0]
        notification.id = 101
        notification.is_read = False

    session.flush.side_effect = flush
    failure = RuntimeError(private_phrase)
    monkeypatch.setattr(
        NotificationService,
        "_push_new_notification",
        staticmethod(
            (lambda user_id, payload: (_ for _ in ()).throw(failure))
            if failure_stage == "websocket"
            else (lambda user_id, payload: None)
        ),
    )
    monkeypatch.setattr(
        notification_service.AliyunPushService,
        "schedule_notification_push",
        (lambda user_id, payload: (_ for _ in ()).throw(failure))
        if failure_stage == "mobile"
        else (lambda user_id, payload: None),
    )

    with caplog.at_level(logging.WARNING, logger=notification_service.__name__):
        result = NotificationService.create_notification(
            user_id=10,
            notification_type="system",
            title="private title",
            content="private body",
            db=session,
        )

    assert result is not None
    assert expected_code in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert private_phrase not in caplog.text
    assert "private title" not in caplog.text
    assert "private body" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("caller_owns_session", [True, False])
def test_create_notification_keeps_commit_and_mobile_push_when_ws_scheduling_fails(
    caller_owns_session, monkeypatch
):
    session = Mock()
    mobile_payloads = []

    def flush():
        notification = session.add.call_args.args[0]
        notification.id = 100
        notification.is_read = False

    session.flush.side_effect = flush
    if not caller_owns_session:
        monkeypatch.setattr(
            NotificationService,
            "_get_session",
            staticmethod(lambda: session),
        )
    monkeypatch.setattr(
        NotificationService,
        "_push_new_notification",
        staticmethod(
            lambda user_id, payload: (_ for _ in ()).throw(
                RuntimeError("websocket scheduling unavailable")
            )
        ),
    )
    monkeypatch.setattr(
        notification_service.AliyunPushService,
        "schedule_notification_push",
        lambda user_id, payload: mobile_payloads.append((user_id, payload)),
    )

    kwargs = {"db": session} if caller_owns_session else {}
    result = NotificationService.create_notification(
        user_id=10,
        notification_type="system",
        title="title",
        content="content",
        **kwargs,
    )

    expected_payload = result.to_dict()
    assert expected_payload["id"] == 100
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()
    assert mobile_payloads == [(10, expected_payload)]
    if caller_owns_session:
        session.close.assert_not_called()
    else:
        session.close.assert_called_once_with()


def test_push_new_notification_sends_websocket_payload_from_plain_dict(monkeypatch):
    session = TrackingSession()
    scheduled = []
    sent = []

    def schedule_push(coro):
        scheduled.append(coro)

    async def send_with_seq(user_id, event, payload):
        sent.append((user_id, event, payload))

    monkeypatch.setattr(notification_service, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        notification_service,
        "is_notification_visible",
        lambda db, user_id, notification_id: True,
    )
    monkeypatch.setattr(
        notification_service,
        "visible_unread_count",
        lambda db, user_id: 4,
    )

    from app.ws_manager import ws_manager

    monkeypatch.setattr(ws_manager, "schedule_push", schedule_push)
    monkeypatch.setattr(ws_manager, "send_with_seq", send_with_seq)

    payload = {
        "id": 5,
        "user_id": 10,
        "sender_id": None,
        "notification_type": "system",
        "title": "notice",
    }
    NotificationService._push_new_notification(10, payload)
    asyncio.run(scheduled.pop())

    assert sent == [
        (
            10,
            "new_notification",
            {
                "notification": {**payload, "sender": None},
                "unread_count": 4,
            },
        )
    ]
    assert session.close_calls == 1
