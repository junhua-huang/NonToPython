import hashlib
import json
from unittest.mock import Mock

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jose import jwt
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Config
from app.database import Base
from app.models.models import User, PushDevice, PushLog, Notification, Block
from app.routers import push
from app.services import aliyun_push_service
from app.services.apns_push_service import APNsPushService

DEVICE = "apns:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SECRET = "12" * 32
TOKEN = "ab" * 32


@pytest.fixture
def db_env(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    db = factory()
    db.add_all([User(id=1, username="one", email="one@example.test", password_hash="x", notify_push=True, notify_message=True, notify_sound=True),
                User(id=2, username="two", email="two@example.test", password_hash="x"),
                Notification(id=10, user_id=1, sender_id=2, notification_type="mention", title="private", content="secret", is_read=False)])
    db.commit()
    monkeypatch.setattr(aliyun_push_service, "SessionLocal", factory)
    monkeypatch.setattr(push, "_capture_product_presence", lambda *_: False)
    monkeypatch.setattr(push, "_after_device_state_change", lambda *_: None)
    monkeypatch.setattr(APNsPushService, "is_configured", classmethod(lambda cls: True))
    monkeypatch.setattr(APNsPushService, "_provider_token", classmethod(lambda cls: "provider-jwt"))
    yield db
    db.close(); engine.dispose()


def registration(**extra):
    return {"device_id": DEVICE, "platform": "ios", "provider": "apns", "apns_token": TOKEN,
            "apns_environment": "production", "apns_topic": Config.APNS_BUNDLE_ID,
            "binding_secret": SECRET, "app_state": "foreground", **extra}


def bind(db, **extra):
    return push.register_device(registration(**extra), db.get(User, 1), db)


def test_registration_rotation_is_account_scoped_and_secrets_are_not_returned(db_env):
    db = db_env
    result = bind(db)
    assert result["apns_supported"] and result["apns_configured"]
    assert result["device"]["apns_environment"] == "production"
    assert TOKEN not in json.dumps(result) and SECRET not in json.dumps(result)
    device = db.query(PushDevice).one()
    assert device.binding_secret_hash == hashlib.sha256(SECRET.encode()).hexdigest()
    push.register_device(registration(apns_token="cd" * 40), db.get(User, 2), db)
    assert db.query(PushDevice).count() == 1
    assert device.user_id == 2 and device.apns_token == "cd" * 40
    with pytest.raises(HTTPException) as error:
        push.register_device(registration(binding_secret="34" * 32), db.get(User, 1), db)
    assert error.value.status_code == 403


@pytest.mark.parametrize("extra", [{"apns_token": "not-hex"}, {"apns_token": "a"}, {"apns_environment": "arbitrary"},
                                     {"apns_topic": "other.bundle"}, {"platform": "android"}, {"binding_secret": "short"}])
def test_invalid_registration_is_rejected_before_writes(db_env, extra):
    with pytest.raises(HTTPException):
        bind(db_env, **extra)
    assert db_env.query(PushDevice).count() == 0


def test_logout_revocation_needs_installation_secret_and_clears_token(db_env):
    bind(db_env)
    push.revoke_installation({"device_id": DEVICE, "binding_secret": "34" * 32}, db_env)
    assert db_env.query(PushDevice).one().enabled
    assert push.revoke_installation({"device_id": DEVICE, "binding_secret": SECRET}, db_env)["revoked"]
    device = db_env.query(PushDevice).one()
    assert not device.enabled and device.apns_token is None


def test_foreground_stale_state_does_not_prevent_apns_and_claim_dedupes(db_env, monkeypatch):
    bind(db_env)
    requests = []
    def receive(request):
        requests.append(request)
        return httpx.Response(200, headers={"apns-id": "11111111-2222-3333-4444-555555555555"})
    client = httpx.Client(base_url="https://api.push.apple.com", transport=httpx.MockTransport(receive))
    monkeypatch.setattr(APNsPushService, "_http_client", classmethod(lambda cls, environment: client))
    APNsPushService.schedule_message_push(1, 2, 99, 55, "private chat body")
    APNsPushService.schedule_message_push(1, 2, 99, 55, "private chat body")
    assert len(requests) == 1
    sent = requests[0]
    payload = json.loads(sent.content)
    assert payload["related_type"] == "conversation" and payload["related_id"] == 55
    assert payload["recipient_id"] == 1 and payload["dedupe_key"] == "message:99"
    assert "private chat body" not in sent.content.decode() and "two" not in sent.content.decode()
    assert sent.headers["apns-topic"] == Config.APNS_BUNDLE_ID
    assert sent.headers["apns-push-type"] == "alert"
    assert sent.headers["authorization"] == "bearer provider-jwt"
    assert db_env.query(PushLog).one().status == "success"


def test_user_preference_block_and_visibility_are_enforced(db_env, monkeypatch):
    bind(db_env)
    send = Mock(); monkeypatch.setattr(APNsPushService, "_send_request", send)
    db_env.get(User, 1).notify_push = False; db_env.commit()
    APNsPushService.schedule_message_push(1, 2, 99, 55, "secret")
    send.assert_not_called()
    db_env.get(User, 1).notify_push = True
    db_env.get(User, 1).notify_message = False; db_env.commit()
    APNsPushService.schedule_message_push(1, 2, 100, 55, "secret")
    send.assert_not_called()
    db_env.get(User, 1).notify_message = True
    db_env.add(Block(blocker_id=1, blocked_id=2)); db_env.commit()
    APNsPushService.schedule_message_push(1, 2, 101, 55, "secret")
    APNsPushService.schedule_notification_push(1, {"id": 10, "user_id": 1, "notification_type": "mention"})
    send.assert_not_called()


@pytest.mark.parametrize("reason,status,disabled", [("Unregistered", 410, True), ("BadDeviceToken", 400, True), ("TooManyRequests", 429, False)])
def test_invalid_tokens_are_disabled_but_transient_errors_are_not(db_env, monkeypatch, reason, status, disabled):
    bind(db_env)
    client = httpx.Client(base_url="https://api.push.apple.com", transport=httpx.MockTransport(lambda _: httpx.Response(status, json={"reason": reason})))
    monkeypatch.setattr(APNsPushService, "_http_client", classmethod(lambda cls, environment: client))
    APNsPushService.schedule_message_push(1, 2, 99, 55, "secret")
    db_env.expire_all()
    assert db_env.query(PushDevice).one().enabled is not disabled
    assert db_env.query(PushLog).one().status == "failed"
    assert db_env.query(PushLog).one().error_code == reason


def test_environment_and_sound_preference_are_preserved(db_env, monkeypatch):
    bind(db_env, apns_environment="development")
    db_env.get(User, 1).notify_sound = False; db_env.commit()
    received = []
    client = httpx.Client(base_url="https://api.sandbox.push.apple.com", transport=httpx.MockTransport(lambda request: (received.append(request) or httpx.Response(200, headers={"apns-id": "ack"}))))
    select = Mock(return_value=client)
    monkeypatch.setattr(APNsPushService, "_http_client", select)
    APNsPushService.schedule_message_push(1, 2, 99, 55, "secret")
    select.assert_called_once_with("development")
    assert "sound" not in json.loads(received[0].content)["aps"]


def test_provider_jwt_uses_es256_and_reuses_token(tmp_path, monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1())
    path = tmp_path / "AuthKey_test.p8"
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    monkeypatch.setattr(Config, "APNS_PRIVATE_KEY_PATH", str(path))
    monkeypatch.setattr(Config, "APNS_TEAM_ID", "TEAM123456")
    monkeypatch.setattr(Config, "APNS_KEY_ID", "KEY1234567")
    monkeypatch.setattr(APNsPushService, "_jwt_value", None)
    first = APNsPushService._provider_token(); second = APNsPushService._provider_token()
    assert first == second
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    assert jwt.decode(first, public, algorithms=["ES256"])["iss"] == "TEAM123456"
    assert jwt.get_unverified_header(first)["kid"] == "KEY1234567"


def test_remote_test_uses_real_notification_pipeline_and_rate_limits(db_env, monkeypatch):
    from app.services.notification_service import NotificationService
    bind(db_env)
    monkeypatch.setattr(NotificationService, "_push_new_notification", lambda *_: None)
    client = httpx.Client(base_url="https://api.push.apple.com", transport=httpx.MockTransport(lambda _: httpx.Response(200, headers={"apns-id": "test-ack"})))
    monkeypatch.setattr(APNsPushService, "_http_client", classmethod(lambda cls, environment: client))
    result = push.test_apns({"device_id": DEVICE}, db_env.get(User, 1), db_env)
    assert result["submitted"]
    with pytest.raises(HTTPException) as error:
        push.test_apns({"device_id": DEVICE}, db_env.get(User, 1), db_env)
    assert error.value.status_code == 429
    with pytest.raises(HTTPException) as error:
        push.test_apns({"device_id": DEVICE}, db_env.get(User, 2), db_env)
    assert error.value.status_code == 404


def test_notification_deep_link_is_owned_and_respects_blocks(db_env):
    from app.routers.notifications import get_notification
    assert get_notification(10, db_env.get(User, 1), db_env)["notification"]["id"] == 10
    with pytest.raises(HTTPException):
        get_notification(10, db_env.get(User, 2), db_env)
    db_env.add(Block(blocker_id=1, blocked_id=2)); db_env.commit()
    with pytest.raises(HTTPException):
        get_notification(10, db_env.get(User, 1), db_env)


def test_late_invalid_token_response_cannot_disable_rotated_token(db_env, monkeypatch):
    bind(db_env)
    def rotate(_):
        db_env.query(PushDevice).one().apns_token = "cd" * 32
        db_env.commit()
        return httpx.Response(410, json={"reason": "Unregistered"})
    client = httpx.Client(base_url="https://api.push.apple.com", transport=httpx.MockTransport(rotate))
    monkeypatch.setattr(APNsPushService, "_http_client", classmethod(lambda cls, environment: client))
    APNsPushService.schedule_message_push(1, 2, 99, 55, "secret")
    db_env.expire_all()
    assert db_env.query(PushDevice).one().enabled
    assert db_env.query(PushDevice).one().apns_token == "cd" * 32


def test_account_transfer_rechecks_before_provider_send(db_env, monkeypatch):
    bind(db_env)
    push.register_device(registration(), db_env.get(User, 2), db_env)
    client = Mock(); monkeypatch.setattr(APNsPushService, "_http_client", lambda *_: client)
    result = APNsPushService._send_request({"device_id": DEVICE, "notification": {"id": 10, "user_id": 1}})
    assert result.http_status == 409
    client.post.assert_not_called()


def test_apns_migration_upgrades_and_downgrades_without_losing_legacy_columns():
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect, text
    path = Path(__file__).parents[1] / "alembic/versions/2026_10_18_0100_apns_devices.py"
    spec = importlib.util.spec_from_file_location("apns_migration", path)
    migration = importlib.util.module_from_spec(spec); spec.loader.exec_module(migration)
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE push_devices (id INTEGER PRIMARY KEY, device_id VARCHAR(128))"))
        conn.execute(text("INSERT INTO push_devices VALUES (1, 'android-existing')"))
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            assert {"apns_token", "apns_environment", "apns_topic", "binding_secret_hash"} <= {c["name"] for c in inspect(conn).get_columns("push_devices")}
            migration.downgrade()
        assert {c["name"] for c in inspect(conn).get_columns("push_devices")} == {"id", "device_id"}
        assert conn.execute(text("SELECT device_id FROM push_devices WHERE id=1")).scalar() == "android-existing"
    engine.dispose()



def test_inert_registration_from_an_old_backend_can_be_upgraded(db_env):
    db_env.add(PushDevice(device_id=DEVICE, user_id=2, provider="apns", platform="ios", enabled=True))
    db_env.commit()
    result = bind(db_env)
    assert result["registered"]
    assert db_env.query(PushDevice).one().apns_token == TOKEN
    assert db_env.query(PushDevice).one().binding_secret_hash is not None
