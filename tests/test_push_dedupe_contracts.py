import importlib.util
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.models import Notification, PushDevice, PushLog, User
from app.services import aliyun_push_service
from app.services.aliyun_push_service import AliyunPushService, ProviderSendResult


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def push_db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    db = Session()
    db.add_all(
        [
            User(
                id=1,
                username="recipient",
                email="recipient@example.com",
                password_hash="x",
                notify_push=True,
            ),
            User(id=2, username="sender", email="sender@example.com", password_hash="x"),
            Notification(
                id=10,
                user_id=1,
                sender_id=2,
                notification_type="message",
                title="title-10",
                content="content-10",
                related_id=501,
                is_read=False,
            ),
            Notification(
                id=11,
                user_id=1,
                sender_id=2,
                notification_type="mention",
                title="title-11",
                content="content-11",
                related_id=502,
                is_read=False,
            ),
            PushDevice(
                user_id=1,
                device_id="device-a",
                provider="aliyun",
                platform="android",
                enabled=True,
                app_state="background",
            ),
        ]
    )
    db.commit()
    monkeypatch.setattr(aliyun_push_service, "SessionLocal", Session)
    monkeypatch.setattr(AliyunPushService, "is_configured", classmethod(lambda cls: True))
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def payload(notification_id=10, notification_type="message", related_id=501, message_id=701):
    return {
        "id": notification_id,
        "user_id": 1,
        "sender_id": 2,
        "notification_type": notification_type,
        "title": f"title-{notification_id}",
        "content": f"content-{notification_id}",
        "related_id": related_id,
        "message_id": message_id,
    }


def provider_result(body, http_status=200):
    return ProviderSendResult(http_status=http_status, body=body)


def test_sequential_duplicate_notification_device_sends_once(push_db, monkeypatch):
    send_request = Mock(
        return_value=provider_result({"RequestId": "req-1", "MessageId": "msg-1"})
    )
    monkeypatch.setattr(AliyunPushService, "_send_request", send_request)

    AliyunPushService.schedule_notification_push(1, payload())
    AliyunPushService.schedule_notification_push(1, payload())

    assert send_request.call_count == 1
    push_db.expire_all()
    logs = push_db.query(PushLog).all()
    assert len(logs) == 1
    assert logs[0].status == "success"
    assert logs[0].attempt_count == 1


def test_different_notification_device_pairs_are_independent(push_db, monkeypatch):
    push_db.add(
        PushDevice(
            user_id=1,
            device_id="device-b",
            provider="aliyun",
            platform="android",
            enabled=True,
            app_state="background",
        )
    )
    push_db.commit()
    send_request = Mock(
        return_value=provider_result({"RequestId": "req", "MessageId": "msg"})
    )
    monkeypatch.setattr(AliyunPushService, "_send_request", send_request)

    AliyunPushService.schedule_notification_push(1, payload(10))
    AliyunPushService.schedule_notification_push(
        1, payload(11, notification_type="mention", related_id=502)
    )

    assert send_request.call_count == 4
    push_db.expire_all()
    pairs = {
        (row.notification_id, row.device_id)
        for row in push_db.query(PushLog).filter(PushLog.device_id.is_not(None)).all()
    }
    assert pairs == {
        (10, "device-a"),
        (10, "device-b"),
        (11, "device-a"),
        (11, "device-b"),
    }


def test_failed_delivery_can_retry_but_success_is_terminal(push_db, monkeypatch):
    send_request = Mock(
        side_effect=[
            provider_result(
                {
                    "RequestId": "req-1",
                    "MessageId": "msg-must-not-ack",
                    "Code": "Throttled",
                    "Message": "retry",
                }
            ),
            provider_result({"RequestId": "req-2", "MessageId": "msg-2"}),
        ]
    )
    monkeypatch.setattr(AliyunPushService, "_send_request", send_request)

    AliyunPushService.schedule_notification_push(1, payload())
    push_db.expire_all()
    failed = push_db.query(PushLog).one()
    assert failed.status == "failed"
    assert failed.error_code == "Throttled"
    assert failed.error_message == "Provider declared an error (HTTP status 200)"
    assert failed.request_id == "req-1"
    assert failed.message_id is None

    AliyunPushService.schedule_notification_push(1, payload())
    AliyunPushService.schedule_notification_push(1, payload())

    assert send_request.call_count == 2
    push_db.expire_all()
    log = push_db.query(PushLog).one()
    assert log.status == "success"
    assert log.attempt_count == 2
    assert log.request_id == "req-2"
    assert log.message_id == "msg-2"


@pytest.mark.parametrize("provider_code", [None, ""])
def test_http_400_with_ids_and_empty_code_fails_then_retries(
    push_db, monkeypatch, provider_code
):
    send_request = Mock(
        side_effect=[
            provider_result(
                {
                    "RequestId": "req-http-400",
                    "MessageId": "msg-must-not-ack",
                    "Code": provider_code,
                },
                http_status=400,
            ),
            provider_result({"RequestId": "req-ok", "MessageId": "msg-ok"}),
        ]
    )
    monkeypatch.setattr(AliyunPushService, "_send_request", send_request)

    AliyunPushService.schedule_notification_push(1, payload())

    push_db.expire_all()
    failed = push_db.query(PushLog).one()
    assert failed.status == "failed"
    assert failed.error_code == "PROVIDER_HTTP_ERROR"
    assert failed.error_message == "Provider returned HTTP status 400"
    assert failed.request_id == "req-http-400"
    assert failed.message_id is None

    AliyunPushService.schedule_notification_push(1, payload())
    AliyunPushService.schedule_notification_push(1, payload())

    assert send_request.call_count == 2
    push_db.expire_all()
    succeeded = push_db.query(PushLog).one()
    assert succeeded.status == "success"
    assert succeeded.attempt_count == 2
    assert succeeded.request_id == "req-ok"
    assert succeeded.message_id == "msg-ok"


@pytest.mark.parametrize("http_status", [None, 300, 399, 500, 599])
def test_non_2xx_or_missing_http_status_with_ids_fails_then_retries(
    push_db, monkeypatch, http_status
):
    send_request = Mock(
        side_effect=[
            provider_result(
                {"RequestId": "req-not-success", "MessageId": "msg-not-success"},
                http_status=http_status,
            ),
            provider_result({"RequestId": "req-ok", "MessageId": "msg-ok"}),
        ]
    )
    monkeypatch.setattr(AliyunPushService, "_send_request", send_request)

    AliyunPushService.schedule_notification_push(1, payload())
    push_db.expire_all()
    failed = push_db.query(PushLog).one()
    assert failed.status == "failed"
    assert failed.error_code in {"MISSING_HTTP_STATUS", "PROVIDER_HTTP_ERROR"}

    AliyunPushService.schedule_notification_push(1, payload())
    assert send_request.call_count == 2


@pytest.mark.parametrize("provider_code", [429, False, {"error": "throttled"}])
def test_non_string_provider_code_with_ids_fails_then_retries(
    push_db, monkeypatch, provider_code
):
    send_request = Mock(
        side_effect=[
            provider_result(
                {
                    "RequestId": "req-provider-error",
                    "MessageId": "msg-must-not-ack",
                    "Code": provider_code,
                }
            ),
            provider_result({"RequestId": "req-ok", "MessageId": "msg-ok"}),
        ]
    )
    monkeypatch.setattr(AliyunPushService, "_send_request", send_request)

    AliyunPushService.schedule_notification_push(1, payload())

    push_db.expire_all()
    failed = push_db.query(PushLog).one()
    assert failed.status == "failed"
    assert failed.error_code == "PROVIDER_ERROR"
    assert failed.message_id is None

    AliyunPushService.schedule_notification_push(1, payload())
    assert send_request.call_count == 2


@pytest.mark.parametrize(
    "unacknowledged_response",
    [
        {},
        [],
        "not-an-object",
        {"RawResponse": "upstream returned HTML"},
        {"RequestId": "req-only"},
        {"MessageId": "msg-only"},
        {"RequestId": True, "MessageId": True},
        {"RequestId": "   ", "MessageId": "\t"},
    ],
)
def test_unacknowledged_2xx_response_fails_and_next_call_retries(
    push_db, monkeypatch, unacknowledged_response
):
    send_request = Mock(
        side_effect=[
            provider_result(unacknowledged_response),
            provider_result({"RequestId": "req-valid", "MessageId": "msg-valid"}),
        ]
    )
    monkeypatch.setattr(AliyunPushService, "_send_request", send_request)

    AliyunPushService.schedule_notification_push(1, payload())

    push_db.expire_all()
    failed = push_db.query(PushLog).one()
    assert failed.status == "failed"
    assert failed.error_code == "INVALID_PROVIDER_RESPONSE"
    assert failed.error_message == "Aliyun response missing delivery acknowledgement"

    AliyunPushService.schedule_notification_push(1, payload())
    AliyunPushService.schedule_notification_push(1, payload())

    assert send_request.call_count == 2
    push_db.expire_all()
    succeeded = push_db.query(PushLog).one()
    assert succeeded.status == "success"
    assert succeeded.attempt_count == 2
    assert succeeded.request_id == "req-valid"
    assert succeeded.message_id == "msg-valid"
    assert succeeded.error_code is None


def test_recent_foreground_device_delivery_is_terminal_and_deduplicated(
    push_db, monkeypatch
):
    device = push_db.query(PushDevice).filter_by(device_id="device-a").one()
    device.app_state = "foreground"
    device.app_state_updated_at = datetime.utcnow()
    push_db.commit()
    send_request = Mock(
        return_value=provider_result({"RequestId": "req", "MessageId": "msg"})
    )
    monkeypatch.setattr(AliyunPushService, "_send_request", send_request)

    AliyunPushService.schedule_notification_push(1, payload())
    AliyunPushService.schedule_notification_push(1, payload())

    assert send_request.call_count == 1
    push_db.expire_all()
    logs = push_db.query(PushLog).all()
    assert len(logs) == 1
    assert logs[0].status == "success"
    assert logs[0].attempt_count == 1
    assert logs[0].request_id == "req"
    assert logs[0].message_id == "msg"


def test_fresh_pending_suppresses_duplicate_and_stale_pending_is_reclaimed(
    push_db, monkeypatch
):
    push_db.add(
        PushLog(
            user_id=1,
            device_id="device-a",
            notification_id=10,
            notification_type="message",
            title="title-10",
            status="pending",
            claim_token="old-claim",
            claimed_at=datetime.utcnow(),
            attempt_count=1,
        )
    )
    push_db.commit()
    send_request = Mock(
        return_value=provider_result({"RequestId": "req", "MessageId": "msg"})
    )
    monkeypatch.setattr(AliyunPushService, "_send_request", send_request)

    AliyunPushService.schedule_notification_push(1, payload())
    assert send_request.call_count == 0

    log = push_db.query(PushLog).one()
    log.claimed_at = datetime.utcnow() - timedelta(
        seconds=AliyunPushService.PENDING_CLAIM_TTL_SECONDS + 1
    )
    push_db.commit()
    AliyunPushService.schedule_notification_push(1, payload())

    assert send_request.call_count == 1
    push_db.expire_all()
    log = push_db.query(PushLog).one()
    assert log.status == "success"
    assert log.attempt_count == 2
    assert log.claim_token is None


def test_repeated_pre_device_visibility_skips_do_not_grow_logs(push_db, monkeypatch):
    monkeypatch.setattr(aliyun_push_service, "is_notification_visible", Mock(return_value=False))
    send_request = Mock()
    monkeypatch.setattr(AliyunPushService, "_send_request", send_request)

    AliyunPushService.schedule_notification_push(1, payload())
    AliyunPushService.schedule_notification_push(1, payload())

    send_request.assert_not_called()
    push_db.expire_all()
    logs = push_db.query(PushLog).all()
    assert len(logs) == 1
    assert logs[0].status == "skipped"
    assert logs[0].error_code == "NOTIFICATION_NOT_VISIBLE"


def test_android_ext_parameters_merge_stable_string_ids_and_canonical_message_dedupe_key():
    params = AliyunPushService._build_push_params(
        "device-a",
        {
            **payload(message_id=88),
            "AndroidExtParameters": json.dumps(
                {
                    "custom": "kept",
                    "notification_id": "wrong",
                    "dedupe_key": "caller:override",
                }
            ),
        },
    )

    ext = json.loads(params["AndroidExtParameters"])
    assert ext == {
        "custom": "kept",
        "notification_id": "10",
        "type": "message",
        "related_id": "501",
        "message_id": "88",
        "dedupe_key": "message:88",
    }


def test_android_ext_parameters_use_notification_dedupe_key_for_non_message():
    params = AliyunPushService._build_push_params(
        "device-a", payload(notification_type="mention", message_id=88)
    )

    ext = json.loads(params["AndroidExtParameters"])
    assert ext["dedupe_key"] == "notification:10"


@pytest.mark.parametrize("message_id", [None, "", "   "])
def test_android_ext_parameters_fall_back_when_message_id_is_missing_or_blank(
    message_id,
):
    notification = payload(message_id=message_id)
    if message_id is None:
        notification.pop("message_id")

    params = AliyunPushService._build_push_params("device-a", notification)

    ext = json.loads(params["AndroidExtParameters"])
    assert ext["dedupe_key"] == "notification:10"


def test_model_requires_complete_delivery_key():
    table = PushLog.__table__
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("notification_id", "device_id") in unique_columns
    assert table.c.notification_id.nullable is False
    assert table.c.device_id.nullable is False
    for column in ("claim_token", "claimed_at", "attempt_count", "updated_at"):
        assert column in table.c


def test_missing_notification_id_is_not_logged_or_sent(push_db, monkeypatch):
    send_request = Mock()
    monkeypatch.setattr(AliyunPushService, "_send_request", send_request)

    AliyunPushService.schedule_notification_push(1, payload(None))

    send_request.assert_not_called()
    assert push_db.query(PushLog).count() == 0


def test_delivery_logs_omit_content_and_sanitize_provider_errors(push_db, monkeypatch):
    secret = "provider-secret-value"
    access_key = "provider-access-key"
    monkeypatch.setattr(aliyun_push_service.Config, "ALIYUN_ACCESS_KEY_SECRET", secret)
    monkeypatch.setattr(aliyun_push_service.Config, "ALIYUN_ACCESS_KEY_ID", access_key)
    send_request = Mock(
        side_effect=[
            provider_result({"RequestId": "req-ok", "MessageId": "msg-ok"}),
            provider_result(
                {
                    "RequestId": "req-provider-error",
                    "Code": "E" * 200,
                    "Message": (
                        f"line one\n{secret}\r\nAccessKeyId={access_key}"
                        "&Signature=sensitive-signature&SignatureNonce=sensitive-nonce"
                        + ("x" * 1000)
                    ),
                }
            ),
        ]
    )
    monkeypatch.setattr(AliyunPushService, "_send_request", send_request)

    AliyunPushService.schedule_notification_push(1, payload(10))
    AliyunPushService.schedule_notification_push(
        1, payload(11, notification_type="mention", related_id=502)
    )

    push_db.expire_all()
    success = push_db.query(PushLog).filter_by(notification_id=10).one()
    failed = push_db.query(PushLog).filter_by(notification_id=11).one()
    assert success.title is None
    assert failed.title is None
    assert failed.error_code == "PROVIDER_ERROR"
    assert failed.error_message == "Provider declared an error (HTTP status 200)"
    assert failed.request_id == "req-provider-error"
    assert secret not in failed.error_message
    assert access_key not in failed.error_message
    assert "sensitive-signature" not in failed.error_message
    assert "sensitive-nonce" not in failed.error_message


@pytest.mark.parametrize(
    ("failure_stage", "expected_context", "expected_error_code"),
    [
        (
            "visibility",
            "visibility lookup failed",
            "NOTIFICATION_VISIBILITY_CHECK_FAILED",
        ),
        ("provider", "device push failed", "PROVIDER_EXCEPTION"),
        ("scheduling", "scheduling failed", "PUSH_SCHEDULING_ERROR"),
    ],
)
def test_unknown_exception_private_phrase_is_absent_from_logs_and_persistence(
    push_db,
    monkeypatch,
    caplog,
    failure_stage,
    expected_context,
    expected_error_code,
):
    private_phrase = "violet parrot medical note 7429 must remain private"
    provider_error = RuntimeError(private_phrase)

    if failure_stage == "visibility":
        monkeypatch.setattr(
            aliyun_push_service,
            "is_notification_visible",
            Mock(side_effect=provider_error),
        )
    elif failure_stage == "provider":
        monkeypatch.setattr(
            AliyunPushService,
            "_send_request",
            Mock(side_effect=provider_error),
        )
    else:
        def fail_user_preference_lookup(cls, db, user_id):
            raise provider_error

        monkeypatch.setattr(
            AliyunPushService,
            "_should_skip_for_user",
            classmethod(fail_user_preference_lookup),
        )

    with caplog.at_level(logging.WARNING, logger=aliyun_push_service.__name__):
        AliyunPushService.schedule_notification_push(1, payload())

    assert expected_context in caplog.text
    assert f"error_code={expected_error_code}" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    assert private_phrase not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)

    push_db.expire_all()
    logs = push_db.query(PushLog).all()
    for log in logs:
        assert private_phrase not in (log.error_code or "")
        assert private_phrase not in (log.error_message or "")
    if failure_stage in {"visibility", "provider"}:
        assert len(logs) == 1
        failed = logs[0]
        assert failed.error_code == expected_error_code
        assert "RuntimeError" in failed.error_message


def _file_session_factory(tmp_path):
    database_path = tmp_path / "push-claims.sqlite3"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 15},
    )
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        connection.exec_driver_sql("PRAGMA busy_timeout=15000")
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _run_parallel_claims(Session, notification, stale=False):
    barrier = Barrier(2)

    def claim():
        db = Session()
        try:
            barrier.wait(timeout=10)
            token = AliyunPushService._claim_delivery(
                db, 1, "device-concurrent", notification
            )
            # This query proves the losing IntegrityError path rolled back cleanly.
            count = db.query(PushLog).count()
            return token, count
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        return list(executor.map(lambda _: claim(), range(2)))


def test_concurrent_fresh_claim_has_one_winner_and_losing_session_is_reusable(tmp_path):
    engine, Session = _file_session_factory(tmp_path)
    try:
        results = _run_parallel_claims(Session, payload())
        tokens = [token for token, _ in results if token is not None]
        assert len(tokens) == 1
        assert [count for _, count in results] == [1, 1]
        with Session() as db:
            row = db.query(PushLog).one()
            assert row.claim_token == tokens[0]
            assert row.status == "pending"
            assert row.attempt_count == 1
    finally:
        engine.dispose()


def test_concurrent_stale_reclaim_is_cas_and_old_token_cannot_finalize(tmp_path):
    engine, Session = _file_session_factory(tmp_path)
    try:
        with Session() as db:
            db.add(
                PushLog(
                    user_id=1,
                    notification_id=10,
                    device_id="device-concurrent",
                    status="pending",
                    claim_token="old-token",
                    claimed_at=datetime.utcnow()
                    - timedelta(seconds=AliyunPushService.PENDING_CLAIM_TTL_SECONDS + 1),
                    attempt_count=1,
                )
            )
            db.commit()

        results = _run_parallel_claims(Session, payload())
        tokens = [token for token, _ in results if token is not None]
        assert len(tokens) == 1
        assert [count for _, count in results] == [1, 1]

        with Session() as db:
            row = db.query(PushLog).one()
            assert row.claim_token == tokens[0]
            assert row.attempt_count == 2
            AliyunPushService._finalize_delivery(
                db,
                10,
                "device-concurrent",
                "old-token",
                provider_result({"RequestId": "obsolete", "MessageId": "obsolete"}),
            )
            db.refresh(row)
            assert row.status == "pending"
            assert row.claim_token == tokens[0]
            assert row.request_id is None

            AliyunPushService._finalize_delivery(
                db,
                10,
                "device-concurrent",
                tokens[0],
                provider_result({"RequestId": "current", "MessageId": "current"}),
            )
            db.refresh(row)
            assert row.status == "success"
            assert row.claim_token is None
            assert row.request_id == "current"
    finally:
        engine.dispose()


def _load_claim_migration():
    path = (
        ROOT
        / "alembic"
        / "versions"
        / "2026_07_18_0100-add_push_log_delivery_claims.py"
    )
    spec = importlib.util.spec_from_file_location("push_log_claim_migration", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _create_legacy_push_logs(connection):
    metadata = sa.MetaData()
    table = sa.Table(
        "push_logs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("device_id", sa.String(128), nullable=True),
        sa.Column("notification_id", sa.Integer, nullable=True),
        sa.Column("notification_type", sa.String(50)),
        sa.Column("title", sa.String(200)),
        sa.Column("status", sa.String(30)),
        sa.Column("request_id", sa.String(128)),
        sa.Column("message_id", sa.String(128)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text),
        sa.Column("created_at", sa.DateTime),
    )
    metadata.create_all(connection)
    connection.execute(
        table.insert(),
        [
            {"id": 1, "user_id": 1, "notification_id": 10, "device_id": "real-a", "status": "failed"},
            {"id": 2, "user_id": 1, "notification_id": 10, "device_id": "real-a", "status": "success", "request_id": "winner"},
            {"id": 3, "user_id": 1, "notification_id": 20, "device_id": None, "status": "failed"},
            {"id": 4, "user_id": 1, "notification_id": 20, "device_id": None, "status": "success"},
            {"id": 5, "user_id": 1, "notification_id": None, "device_id": "orphan-device", "status": "failed"},
            {"id": 6, "user_id": 1, "notification_id": None, "device_id": "orphan-device", "status": "failed"},
            {"id": 7, "user_id": 1, "notification_id": None, "device_id": None, "status": "skipped"},
            # The reserved key represents the same non-device audit state as a
            # legacy NULL device, so success should win when they are merged.
            {"id": 8, "user_id": 1, "notification_id": 20, "device_id": "__pre_device__", "status": "success"},
        ],
    )


def test_dirty_legacy_migration_upgrade_enforces_keys_and_downgrade_restores_nulls(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'migration.sqlite3').as_posix()}")
    migration = _load_claim_migration()
    try:
        with engine.begin() as connection:
            _create_legacy_push_logs(connection)
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration.upgrade()

        inspector = sa.inspect(engine)
        columns = {column["name"]: column for column in inspector.get_columns("push_logs")}
        assert columns["notification_id"]["nullable"] is False
        assert columns["device_id"]["nullable"] is False
        uniques = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("push_logs")
        }
        assert ("notification_id", "device_id") in uniques

        upgraded = sa.Table("push_logs", sa.MetaData(), autoload_with=engine)
        with engine.connect() as connection:
            rows = connection.execute(sa.select(upgraded).order_by(upgraded.c.id)).mappings().all()
        assert len(rows) == 5
        winner = next(row for row in rows if row["notification_id"] == 10)
        assert winner["id"] == 2 and winner["status"] == "success"
        pre_device = next(row for row in rows if row["notification_id"] == 20)
        assert pre_device["id"] == 8 and pre_device["device_id"] == "__pre_device__"
        orphan_rows = [row for row in rows if row["id"] in (5, 6, 7)]
        assert len({row["notification_id"] for row in orphan_rows}) == 3
        assert all(row["notification_id"] < 0 for row in orphan_rows)
        assert all(row["device_id"] is not None for row in rows)

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    upgraded.insert().values(
                        user_id=1,
                        notification_id=10,
                        device_id="real-a",
                        status="pending",
                        attempt_count=1,
                    )
                )
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    upgraded.insert().values(
                        user_id=1,
                        notification_id=None,
                        device_id=None,
                        status="skipped",
                        attempt_count=0,
                    )
                )

        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration.downgrade()

        inspector = sa.inspect(engine)
        columns = {column["name"]: column for column in inspector.get_columns("push_logs")}
        assert columns["notification_id"]["nullable"] is True
        assert columns["device_id"]["nullable"] is True
        for removed in ("claim_token", "claimed_at", "attempt_count", "updated_at"):
            assert removed not in columns
        assert not inspector.get_unique_constraints("push_logs")

        downgraded = sa.Table("push_logs", sa.MetaData(), autoload_with=engine)
        with engine.connect() as connection:
            rows = connection.execute(sa.select(downgraded)).mappings().all()
        assert all(row["notification_id"] is None for row in rows if row["id"] in (5, 6, 7))
        assert next(row for row in rows if row["id"] == 8)["device_id"] is None
        assert next(row for row in rows if row["id"] == 7)["device_id"] is None
    finally:
        engine.dispose()
