"""Direct APNs HTTP/2 delivery, sharing the existing durable push claims/policies."""
from datetime import datetime
import json
from pathlib import Path
import threading
import time
import uuid

import httpx
from jose import jwt

from app.core.config import Config
from app.models.models import PushDevice, PushLog, User
from app.services.aliyun_push_service import AliyunPushService, ProviderSendResult
from app.services.notification_messages import REPORT_RECEIVED_TITLE, REPORT_RECEIVED_PUSH_BODY


class APNsPushService(AliyunPushService):
    NOT_CONFIGURED_CODE = "APNS_NOT_CONFIGURED"
    NOT_CONFIGURED_MESSAGE = "APNs provider configuration is incomplete"
    INVALID_RESPONSE_MESSAGE = "APNs response missing delivery acknowledgement"
    _jwt_lock = threading.Lock()
    _jwt_value = None
    _jwt_created = 0
    _jwt_identity = None
    _clients = {}
    _client_lock = threading.Lock()

    @classmethod
    def is_configured(cls):
        return bool(Config.APNS_KEY_ID and Config.APNS_TEAM_ID and Config.APNS_PRIVATE_KEY_PATH and Config.APNS_BUNDLE_ID)

    @classmethod
    def _load_enabled_devices(cls, db, user_id):
        return db.query(PushDevice).filter(
            PushDevice.user_id == user_id,
            PushDevice.enabled == True,
            PushDevice.provider == "apns",
            PushDevice.platform == "ios",
            PushDevice.apns_token.isnot(None),
            PushDevice.apns_topic == Config.APNS_BUNDLE_ID,
        ).all()

    @classmethod
    def schedule_notification_push(cls, user_id, notification_dict, *, require_visible=True, background_only=False):
        # A killed/suspended app cannot reliably report background state. APNs is
        # always eligible; the iOS delegate suppresses duplicate foreground alerts.
        if cls.is_configured():
            super().schedule_notification_push(user_id, notification_dict, require_visible=require_visible, background_only=False)

    @classmethod
    def schedule_message_push(cls, *args, **kwargs):
        if cls.is_configured():
            super().schedule_message_push(*args, **kwargs)

    @classmethod
    def _build_push_params(cls, device_id, notification):
        # The inherited claim loop passes an installation ID, never the APNs token.
        return {"device_id": device_id, "notification": notification}

    @classmethod
    def _provider_token(cls):
        identity = (Config.APNS_KEY_ID, Config.APNS_TEAM_ID, Config.APNS_PRIVATE_KEY_PATH)
        now = int(time.time())
        with cls._jwt_lock:
            if cls._jwt_identity != identity or not cls._jwt_value or not 0 <= now - cls._jwt_created < 3000:
                key = Path(Config.APNS_PRIVATE_KEY_PATH).read_text(encoding="utf-8")
                cls._jwt_value = jwt.encode({"iss": Config.APNS_TEAM_ID, "iat": now}, key, algorithm="ES256", headers={"kid": Config.APNS_KEY_ID})
                cls._jwt_created = now
                cls._jwt_identity = identity
            return cls._jwt_value

    @classmethod
    def _http_client(cls, environment):
        with cls._client_lock:
            client = cls._clients.get(environment)
            if client is None or client.is_closed:
                host = "https://api.sandbox.push.apple.com" if environment == "development" else "https://api.push.apple.com"
                client = httpx.Client(base_url=host, http2=True, timeout=5, follow_redirects=False)
                cls._clients[environment] = client
            return client

    @classmethod
    def _payload(cls, notification, user):
        kind = notification.get("notification_type")
        # Private chat text and sender names never enter the lock-screen preview.
        aps = {"alert": {"title": "南图", "body": "你收到了一条新消息" if kind == "message" else "你有一条新通知"}}
        if kind == "report_received" and notification.get("sender_id") is None and notification.get("related_type") == "report":
            # Only fixed, platform-authored receipt copy is safe on the lock screen.
            aps["alert"] = {"title": REPORT_RECEIVED_TITLE, "body": REPORT_RECEIVED_PUSH_BODY}
        if user and user.notify_sound is not False:
            aps["sound"] = "default"
        return {
            "aps": aps,
            "recipient_id": notification.get("user_id"),
            "notification_id": notification.get("id") if (notification.get("id") or 0) > 0 else None,
            "type": kind,
            "related_id": notification.get("related_id"),
            "related_type": notification.get("related_type"),
            "message_id": notification.get("message_id"),
            "dedupe_key": cls._canonical_dedupe_key(notification),
        }

    @classmethod
    def _send_request(cls, params):
        # Resolve the device immediately before sending and recheck account ownership.
        from app.services.aliyun_push_service import SessionLocal
        db = SessionLocal()
        try:
            note = params["notification"]
            device = db.query(PushDevice).filter(
                PushDevice.device_id == params["device_id"], PushDevice.user_id == note.get("user_id"),
                PushDevice.provider == "apns", PushDevice.enabled == True,
            ).first()
            if not device or device.apns_environment not in {"development", "production"} or device.apns_topic != Config.APNS_BUNDLE_ID:
                return ProviderSendResult(409, {"Code": "BINDING_CHANGED"})
            user = db.query(User).filter(User.id == device.user_id).first()
            token, environment, topic = device.apns_token, device.apns_environment, device.apns_topic
            if not token:
                return ProviderSendResult(400, {"Code": "MISSING_DEVICE_TOKEN"})
            payload = cls._payload(note, user)
        finally:
            db.close()
        apns_id = str(uuid.uuid4())
        response = cls._http_client(environment).post(
            "/3/device/" + token,
            headers={"authorization": "bearer " + cls._provider_token(), "content-type": "application/json", "apns-topic": topic,
                     "apns-push-type": "alert", "apns-priority": "10", "apns-id": apns_id,
                     "apns-expiration": str(int(time.time()) + 3600),
                     "apns-collapse-id": cls._canonical_dedupe_key(note) or apns_id},
            content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        )
        if response.status_code == 200:
            ack = response.headers.get("apns-id")
            return ProviderSendResult(200, {"RequestId": ack, "MessageId": ack})
        try:
            reason = response.json().get("reason")
        except (ValueError, AttributeError):
            reason = None
        reason = cls._safe_provider_code(reason) or "APNS_HTTP_ERROR"
        if reason in {"Unregistered", "BadDeviceToken", "DeviceTokenNotForTopic"}:
            db = SessionLocal()
            try:
                # A late response for a rotated token must not disable its replacement.
                db.query(PushDevice).filter(
                    PushDevice.device_id == params["device_id"], PushDevice.user_id == note.get("user_id"),
                    PushDevice.apns_token == token, PushDevice.apns_environment == environment,
                ).update({PushDevice.enabled: False, PushDevice.updated_at: datetime.utcnow()}, synchronize_session=False)
                db.commit()
            finally:
                db.close()
        return ProviderSendResult(response.status_code, {"Code": reason, "RequestId": response.headers.get("apns-id")})
