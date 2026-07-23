import io
import pathlib
import unittest
import urllib.error
from unittest.mock import ANY, Mock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Config
from app.database import Base
from app.models.models import Block, Notification, PushDevice, PushLog, User
from app.services import aliyun_push_service
from app.services.aliyun_push_service import AliyunPushService, ProviderSendResult
from app.services.notification_query_service import (
    is_notification_visible as real_is_notification_visible,
)
from app.services.notification_service import NotificationService


ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture()
def push_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    db = Session()
    try:
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
                    notification_type="mention",
                    title="private notification title",
                    content="private notification content",
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
                PushDevice(
                    user_id=1,
                    device_id="device-b",
                    provider="aliyun",
                    platform="android",
                    enabled=True,
                    app_state="background",
                ),
            ]
        )
        db.commit()
        yield db, Session
    finally:
        db.close()
        engine.dispose()


class AliyunPushContracts(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_models_define_aliyun_push_devices_and_logs(self):
        source = self.read("app/models/models.py")

        self.assertIn("class PushDevice", source)
        self.assertIn("__tablename__ = 'push_devices'", source)
        self.assertIn("device_id", source)
        self.assertIn("UniqueConstraint('device_id', name='uq_push_devices_device_id')", source)
        self.assertIn("provider", source)
        self.assertIn("enabled", source)
        self.assertIn("last_seen_at", source)
        self.assertIn("class PushLog", source)
        self.assertIn("__tablename__ = 'push_logs'", source)
        self.assertIn("notification_id", source)
        self.assertIn("request_id", source)
        self.assertIn("message_id", source)

    def test_migration_creates_aliyun_push_tables(self):
        migration = self.read("alembic/versions/2026_07_02_0200-add_aliyun_push_devices.py")

        self.assertIn("op.create_table('push_devices'", migration)
        self.assertIn("op.create_table('push_logs'", migration)
        self.assertIn("sa.UniqueConstraint('device_id'", migration)
        self.assertIn("op.drop_table('push_logs')", migration)
        self.assertIn("op.drop_table('push_devices')", migration)

    def test_push_router_is_registered_and_auth_scoped(self):
        main_source = self.read("app/main.py")
        router = self.read("app/routers/push.py")

        self.assertIn("from app.routers import", main_source)
        self.assertIn("push", main_source)
        self.assertIn("include_router(push.router, prefix=\"/api/push\"", main_source)
        self.assertIn("@router.post(\"/devices/register\")", router)
        self.assertIn("@router.post(\"/devices/unregister\")", router)
        self.assertIn("@router.get(\"/devices/status\")", router)
        self.assertIn("get_current_user", router)
        self.assertIn("PushDevice", router)

    def test_aliyun_push_service_uses_env_config_and_android_notice(self):
        attrs = dir(Config)
        self.assertIn("ALIYUN_ACCESS_KEY_ID", attrs)
        self.assertIn("ALIYUN_ACCESS_KEY_SECRET", attrs)
        self.assertIn("ALIYUN_PUSH_APP_KEY_ANDROID", attrs)
        self.assertIn("ALIYUN_PUSH_ANDROID_CHANNEL_ID", attrs)
        self.assertIn("ALIYUN_PUSH_ANDROID_ACTIVITY", attrs)

        service = self.read("app/services/aliyun_push_service.py")
        self.assertIn("class AliyunPushService", service)
        self.assertIn("PushNoticeToAndroid", service)
        self.assertIn("AndroidNotificationChannel", service)
        self.assertIn("nonto_message_alerts", service)
        self.assertIn("com.nonto.nonto.MainActivity", service)
        self.assertIn("Signature", service)
        self.assertIn("hmac", service)
        self.assertNotIn("AccessKeySecret=", service)

    def test_notification_service_schedules_mobile_push_for_all_notifications(self):
        db = Mock()

        def refresh(notification):
            notification.id = 1

        db.refresh.side_effect = refresh
        with (
            patch.object(NotificationService, "_push_new_notification"),
            patch.object(AliyunPushService, "schedule_notification_push") as mobile_push,
        ):
            result = NotificationService.create_notification(
                user_id=1,
                notification_type="system",
                title="title",
                content="content",
                db=db,
            )

        mobile_push.assert_called_once_with(1, result.to_dict())

    def test_malformed_2xx_response_is_normalized_to_retryable_failure(self):
        malformed_response = Mock()
        malformed_response.__enter__ = Mock(return_value=malformed_response)
        malformed_response.__exit__ = Mock(return_value=False)
        malformed_response.status = 200
        malformed_response.read.return_value = b"<html>bad gateway</html>"

        with patch("urllib.request.urlopen", return_value=malformed_response):
            response = AliyunPushService._send_request(
                {"Action": "PushNoticeToAndroid"}
            )

        self.assertEqual(response.http_status, 200)
        self.assertIsNone(response.body)
        self.assertNotIn("<html>", str(response))

    def test_aliyun_http_error_response_is_parsed(self):
        error_body = b'{"RequestId":"req-1","Code":"InvalidParameter","Message":"bad param"}'
        http_error = urllib.error.HTTPError(
            url="https://cloudpush.aliyuncs.com/",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(error_body),
        )

        with patch("urllib.request.urlopen", side_effect=http_error):
            response = AliyunPushService._send_request({"Action": "PushNoticeToAndroid"})

        self.assertEqual(response.http_status, 400)
        self.assertEqual(response.body["RequestId"], "req-1")
        self.assertEqual(response.body["Code"], "InvalidParameter")
        self.assertEqual(response.body["Message"], "bad param")


def _notification_payload():
    return {
        "id": 10,
        "user_id": 1,
        "sender_id": 2,
        "notification_type": "mention",
        "title": "private notification title",
        "content": "private notification content",
    }


def test_db_block_suppresses_mobile_push_with_stale_empty_cache(push_db, monkeypatch):
    db, Session = push_db
    db.add(Block(blocker_id=1, blocked_id=2))
    db.commit()

    monkeypatch.setattr(aliyun_push_service, "SessionLocal", Session)
    visibility_check = Mock(wraps=real_is_notification_visible)
    monkeypatch.setattr(aliyun_push_service, "is_notification_visible", visibility_check)
    monkeypatch.setattr(AliyunPushService, "is_configured", classmethod(lambda cls: True))
    send_request = Mock(
        return_value=ProviderSendResult(
            http_status=200,
            body={"RequestId": "request", "MessageId": "message"},
        )
    )
    monkeypatch.setattr(AliyunPushService, "_send_request", send_request)
    monkeypatch.setattr(
        "app.ws_manager.ws_manager.get_blocked_user_ids",
        lambda user_id: set(),
    )

    AliyunPushService.schedule_notification_push(1, _notification_payload())

    visibility_check.assert_called_once_with(ANY, 1, 10)
    send_request.assert_not_called()
    db.expire_all()
    logs = db.query(PushLog).all()
    assert len(logs) == 1
    assert logs[0].status == "skipped"
    assert logs[0].error_code == "NOTIFICATION_NOT_VISIBLE"


def test_visibility_lookup_error_fails_closed_once_and_exposes_no_content(
    push_db, monkeypatch
):
    db, Session = push_db
    visibility_check = Mock(side_effect=RuntimeError("database unavailable"))

    monkeypatch.setattr(aliyun_push_service, "SessionLocal", Session)
    monkeypatch.setattr(
        aliyun_push_service,
        "is_notification_visible",
        visibility_check,
        raising=False,
    )
    monkeypatch.setattr(AliyunPushService, "is_configured", classmethod(lambda cls: True))
    send_request = Mock(
        return_value=ProviderSendResult(
            http_status=200,
            body={"RequestId": "request", "MessageId": "message"},
        )
    )
    monkeypatch.setattr(AliyunPushService, "_send_request", send_request)

    AliyunPushService.schedule_notification_push(1, _notification_payload())

    visibility_check.assert_called_once_with(ANY, 1, 10)
    send_request.assert_not_called()
    db.expire_all()
    logs = db.query(PushLog).all()
    assert len(logs) == 1
    assert logs[0].status == "skipped"
    assert logs[0].error_code == "NOTIFICATION_VISIBILITY_CHECK_FAILED"
    assert logs[0].title is None
    assert "private notification" not in (logs[0].error_message or "")


if __name__ == "__main__":
    unittest.main()
