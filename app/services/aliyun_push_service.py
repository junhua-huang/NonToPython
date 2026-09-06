"""Aliyun mobile push integration.

The service only reads credentials from environment-backed Config attributes. It never
logs or prints access keys or request signatures.
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import NamedTuple

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Config
from app.database import SessionLocal
from app.models.models import PushDevice, PushLog, User
from app.services.block_service import has_block_between
from app.services.notification_query_service import is_notification_visible

logger = logging.getLogger(__name__)


class ProviderSendResult(NamedTuple):
    """Authoritative transport status and untrusted parsed provider body."""

    http_status: int | None
    body: dict | None


class AliyunPushService:
    """Send Android notification pushes through Aliyun CloudPush."""

    ACTION = "PushNoticeToAndroid"
    VERSION = "2016-08-01"
    PENDING_CLAIM_TTL_SECONDS = 120
    PRE_DEVICE_LOG_KEY = "__pre_device__"
    MESSAGE_PUSH_ID_OFFSET = 1_000_000_000_000
    MAX_ERROR_MESSAGE_LENGTH = 500
    MAX_PROVIDER_CODE_LENGTH = 80
    MAX_PROVIDER_ID_LENGTH = 128
    INVALID_RESPONSE_CODE = "INVALID_PROVIDER_RESPONSE"
    INVALID_RESPONSE_MESSAGE = "Aliyun response missing delivery acknowledgement"
    SAFE_EXCEPTION_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    SAFE_PROVIDER_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
    SAFE_PROVIDER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

    @classmethod
    def is_configured(cls) -> bool:
        """Return True when required Aliyun push settings are present."""
        return bool(
            Config.ALIYUN_ACCESS_KEY_ID
            and Config.ALIYUN_ACCESS_KEY_SECRET
            and Config.ALIYUN_PUSH_APP_KEY_ANDROID
        )

    @staticmethod
    def _stable_notification_id(notification: dict) -> int | None:
        notification_id = notification.get("id")
        if isinstance(notification_id, bool) or not isinstance(notification_id, int):
            return None
        if notification_id > 0:
            return notification_id
        # Message-only push records use a negative synthetic ID because they are
        # delivery bookkeeping, never rows in the notifications table.
        if (
            notification.get("notification_type") == "message"
            and notification_id < 0
            and str(notification.get("message_id") or "").strip()
        ):
            return notification_id
        return None

    @classmethod
    def _canonical_dedupe_key(cls, notification: dict) -> str | None:
        notification_id = cls._stable_notification_id(notification)
        if notification_id is None:
            return None
        if notification.get("notification_type") == "message":
            message_id = str(notification.get("message_id") or "").strip()
            if message_id:
                return f"message:{message_id}"
        return f"notification:{notification_id}"

    @classmethod
    def _safe_exception_type(cls, exc: BaseException) -> str:
        name = exc.__class__.__name__
        return name if cls.SAFE_EXCEPTION_TYPE.fullmatch(name) else "Exception"

    @classmethod
    def _safe_provider_code(cls, value) -> str | None:
        if not isinstance(value, str):
            return None
        code = value.strip()
        return code if cls.SAFE_PROVIDER_CODE.fullmatch(code) else None

    @classmethod
    def _safe_provider_id(cls, value) -> str | None:
        if not isinstance(value, str):
            return None
        identifier = value.strip()
        return identifier if cls.SAFE_PROVIDER_ID.fullmatch(identifier) else None

    @staticmethod
    def _bounded_diagnostic(value: str | None, max_length: int) -> str | None:
        return value[:max_length] if isinstance(value, str) else None

    @classmethod
    def schedule_notification_push(
        cls,
        user_id: int,
        notification_dict: dict,
        *,
        require_visible: bool = True,
        background_only: bool = False,
    ) -> None:
        """Best-effort push with one durable delivery state per device.

        Ordinary notifications must be visible in the notification center before they
        can be pushed. Message alerts are allowed to use this same delivery machinery
        through ``schedule_message_push`` without creating a Notification row.
        """
        db = SessionLocal()
        try:
            notification_id = cls._stable_notification_id(notification_dict)
            if notification_id is None:
                logger.warning(
                    "[ALIYUN PUSH] missing stable notification id uid=%s; push suppressed",
                    user_id,
                )
                return
            if require_visible:
                try:
                    is_visible = is_notification_visible(
                        db,
                        user_id,
                        notification_id,
                    )
                except Exception as exc:
                    db.rollback()
                    exception_type = cls._safe_exception_type(exc)
                    logger.warning(
                        "[ALIYUN PUSH] notification visibility lookup failed uid=%s notification_id=%s error_code=%s exception_type=%s",
                        user_id,
                        notification_id,
                        "NOTIFICATION_VISIBILITY_CHECK_FAILED",
                        exception_type,
                    )
                    cls._write_log(
                        db,
                        user_id=user_id,
                        notification={"id": notification_id},
                        status="skipped",
                        error_code="NOTIFICATION_VISIBILITY_CHECK_FAILED",
                        error_message=(
                            "Notification visibility lookup failed; push suppressed "
                            f"(exception_type={exception_type})"
                        ),
                    )
                    db.commit()
                    return

                if not is_visible:
                    cls._write_log(
                        db,
                        user_id=user_id,
                        notification={"id": notification_id},
                        status="skipped",
                        error_code="NOTIFICATION_NOT_VISIBLE",
                        error_message="Notification is not visible to recipient; push suppressed",
                    )
                    db.commit()
                    return

            if cls._should_skip_for_user(db, user_id):
                cls._write_log(
                    db,
                    user_id=user_id,
                    notification=notification_dict,
                    status="skipped",
                    error_code="PUSH_DISABLED",
                    error_message="User disabled push notifications",
                )
                db.commit()
                return

            devices = cls._load_enabled_devices(db, user_id)
            if background_only:
                devices = [
                    device for device in devices
                    if device.app_state != "foreground"
                ]
            if not devices:
                return

            configured = cls.is_configured()
            for device in devices:
                device_id = device.device_id
                if not configured:
                    cls._record_nonterminal_skip(
                        db,
                        user_id,
                        device_id,
                        notification_dict,
                        "ALIYUN_PUSH_NOT_CONFIGURED",
                        "Aliyun push configuration is incomplete",
                    )
                    continue
                claim_token = cls._claim_delivery(
                    db, user_id, device_id, notification_dict
                )
                if claim_token is None:
                    continue

                try:
                    params = cls._build_push_params(device_id, notification_dict)
                    result = cls._send_request(params)
                    cls._finalize_delivery(
                        db,
                        notification_id,
                        device_id,
                        claim_token,
                        result,
                    )
                except Exception as exc:
                    exception_type = cls._safe_exception_type(exc)
                    logger.warning(
                        "[ALIYUN PUSH] device push failed uid=%s error_code=%s exception_type=%s",
                        user_id,
                        "PROVIDER_EXCEPTION",
                        exception_type,
                    )
                    cls._finalize_exception(
                        db,
                        notification_id,
                        device_id,
                        claim_token,
                        exception_type,
                    )
        except Exception as exc:
            db.rollback()
            logger.warning(
                "[ALIYUN PUSH] scheduling failed uid=%s error_code=%s exception_type=%s",
                user_id,
                "PUSH_SCHEDULING_ERROR",
                cls._safe_exception_type(exc),
            )
        finally:
            db.close()

    @classmethod
    def schedule_message_push(
        cls,
        receiver_id: int,
        sender_id: int,
        message_id: int,
        conversation_id: int,
        message_content: str | None,
    ) -> None:
        """Push a chat alert without creating a notification-center row."""
        if receiver_id == sender_id or message_id <= 0:
            return
        preview = str(message_content or "")
        if len(preview) > 50:
            preview = f"{preview[:50]}..."
        db = SessionLocal()
        try:
            sender = db.query(User).filter(User.id == sender_id).first()
            recipient = db.query(User).filter(User.id == receiver_id).first()
            if (
                not sender
                or not recipient
                or recipient.notify_message is False
                or has_block_between(db, receiver_id, sender_id)
            ):
                return
            payload = {
                "id": -cls.MESSAGE_PUSH_ID_OFFSET - message_id,
                "user_id": receiver_id,
                "sender_id": sender_id,
                "notification_type": "message",
                "title": f"来自 {sender.username} 的新消息",
                "content": preview,
                "related_id": conversation_id,
                "related_type": "conversation",
                "message_id": message_id,
            }
        finally:
            db.close()
        cls.schedule_notification_push(
            receiver_id,
            payload,
            require_visible=False,
            background_only=True,
        )

    @classmethod
    def _should_skip_for_user(cls, db: Session, user_id: int) -> bool:
        user = db.query(User).filter(User.id == user_id).first()
        return bool(user and user.notify_push is False)

    @classmethod
    def _load_enabled_devices(cls, db: Session, user_id: int):
        return (
            db.query(PushDevice)
            .filter(
                PushDevice.user_id == user_id,
                PushDevice.enabled == True,
                PushDevice.provider == "aliyun",
                or_(PushDevice.platform == "android", PushDevice.platform.is_(None)),
            )
            .all()
        )

    @classmethod
    def _record_nonterminal_skip(
        cls,
        db: Session,
        user_id: int,
        device_id: str,
        notification: dict,
        error_code: str,
        error_message: str,
    ) -> None:
        """Insert or refresh a nonterminal skip without creating attempt rows."""
        notification_id = cls._stable_notification_id(notification)
        if notification_id is None or not device_id:
            return
        now = datetime.utcnow()
        safe_error_code = cls._safe_provider_code(error_code)
        error_code = safe_error_code or ("INTERNAL_ERROR" if error_code else None)
        error_message = cls._bounded_diagnostic(
            error_message, cls.MAX_ERROR_MESSAGE_LENGTH
        )
        log = PushLog(
            user_id=user_id,
            device_id=device_id,
            notification_id=notification_id,
            notification_type=notification.get("notification_type"),
            status="skipped",
            attempt_count=0,
            error_code=error_code,
            error_message=error_message,
            updated_at=now,
        )
        db.add(log)
        try:
            db.commit()
            return
        except IntegrityError:
            db.rollback()

        stale_before = now - timedelta(seconds=cls.PENDING_CLAIM_TTL_SECONDS)
        db.query(PushLog).filter(
            PushLog.notification_id == notification_id,
            PushLog.device_id == device_id,
            or_(
                PushLog.status.in_(("failed", "skipped")),
                PushLog.status.is_(None),
                (PushLog.status == "pending") & (PushLog.claimed_at < stale_before),
                (PushLog.status == "pending") & (PushLog.claimed_at.is_(None)),
            ),
        ).update(
            {
                PushLog.user_id: user_id,
                PushLog.notification_type: notification.get("notification_type"),
                PushLog.title: None,
                PushLog.status: "skipped",
                PushLog.claim_token: None,
                PushLog.claimed_at: None,
                PushLog.error_code: error_code,
                PushLog.error_message: error_message,
                PushLog.updated_at: now,
            },
            synchronize_session=False,
        )
        db.commit()

    @classmethod
    def _claim_delivery(
        cls,
        db: Session,
        user_id: int,
        device_id: str,
        notification: dict,
    ) -> str | None:
        """Atomically claim a retryable delivery and commit before provider I/O."""
        notification_id = cls._stable_notification_id(notification)
        if notification_id is None or not device_id:
            return None
        now = datetime.utcnow()
        claim_token = str(uuid.uuid4())
        log = PushLog(
            user_id=user_id,
            device_id=device_id,
            notification_id=notification_id,
            notification_type=notification.get("notification_type"),
            status="pending",
            claim_token=claim_token,
            claimed_at=now,
            attempt_count=1,
            updated_at=now,
        )
        db.add(log)
        try:
            db.commit()
            return claim_token
        except IntegrityError:
            db.rollback()

        stale_before = now - timedelta(seconds=cls.PENDING_CLAIM_TTL_SECONDS)
        claimed = db.query(PushLog).filter(
            PushLog.notification_id == notification_id,
            PushLog.device_id == device_id,
            or_(
                PushLog.status == "failed",
                PushLog.status == "skipped",
                PushLog.status.is_(None),
                (PushLog.status == "pending") & (PushLog.claimed_at < stale_before),
                (PushLog.status == "pending") & (PushLog.claimed_at.is_(None)),
            ),
        ).update(
            {
                PushLog.user_id: user_id,
                PushLog.notification_type: notification.get("notification_type"),
                PushLog.title: None,
                PushLog.status: "pending",
                PushLog.claim_token: claim_token,
                PushLog.claimed_at: now,
                PushLog.attempt_count: PushLog.attempt_count + 1,
                PushLog.request_id: None,
                PushLog.message_id: None,
                PushLog.error_code: None,
                PushLog.error_message: None,
                PushLog.updated_at: now,
            },
            synchronize_session=False,
        )
        db.commit()
        return claim_token if claimed == 1 else None

    @classmethod
    def _finalize_delivery(
        cls,
        db: Session,
        notification_id: int,
        device_id: str,
        claim_token: str,
        result: ProviderSendResult,
    ) -> None:
        """Finalize the current claim using authoritative transport and body data."""
        http_status = result.http_status if isinstance(result, ProviderSendResult) else None
        body = result.body if isinstance(result, ProviderSendResult) else None
        parsed_body = body if isinstance(body, dict) else None
        response = parsed_body or {}

        request_id = cls._safe_provider_id(response.get("RequestId"))
        message_id = cls._safe_provider_id(response.get("MessageId"))
        error_indicators = [
            response[key]
            for key in ("Code", "ErrorCode")
            if key in response
            and response[key] is not None
            and not (isinstance(response[key], str) and not response[key].strip())
        ]
        has_provider_error = bool(error_indicators)
        raw_error = error_indicators[0] if error_indicators else None
        provider_code = cls._safe_provider_code(raw_error) if has_provider_error else None

        error_code = None
        error_message = None
        success = False
        if not isinstance(http_status, int) or isinstance(http_status, bool):
            error_code = "MISSING_HTTP_STATUS"
            error_message = "Provider response missing HTTP status"
        elif not 200 <= http_status <= 299:
            error_code = provider_code or "PROVIDER_HTTP_ERROR"
            error_message = f"Provider returned HTTP status {http_status}"
        elif parsed_body is None:
            error_code = cls.INVALID_RESPONSE_CODE
            error_message = cls.INVALID_RESPONSE_MESSAGE
        elif has_provider_error:
            error_code = provider_code or "PROVIDER_ERROR"
            error_message = f"Provider declared an error (HTTP status {http_status})"
        elif request_id is None or message_id is None:
            error_code = cls.INVALID_RESPONSE_CODE
            error_message = cls.INVALID_RESPONSE_MESSAGE
        else:
            success = True

        db.query(PushLog).filter(
            PushLog.notification_id == notification_id,
            PushLog.device_id == device_id,
            PushLog.status == "pending",
            PushLog.claim_token == claim_token,
        ).update(
            {
                PushLog.status: "success" if success else "failed",
                PushLog.claim_token: None,
                PushLog.claimed_at: None,
                PushLog.request_id: request_id,
                PushLog.message_id: message_id if success else None,
                PushLog.error_code: error_code,
                PushLog.error_message: error_message,
                PushLog.updated_at: datetime.utcnow(),
            },
            synchronize_session=False,
        )
        db.commit()

    @classmethod
    def _finalize_exception(
        cls,
        db: Session,
        notification_id: int,
        device_id: str,
        claim_token: str,
        exception_type: str,
    ) -> None:
        db.query(PushLog).filter(
            PushLog.notification_id == notification_id,
            PushLog.device_id == device_id,
            PushLog.status == "pending",
            PushLog.claim_token == claim_token,
        ).update(
            {
                PushLog.status: "failed",
                PushLog.claim_token: None,
                PushLog.claimed_at: None,
                PushLog.request_id: None,
                PushLog.message_id: None,
                PushLog.error_code: "PROVIDER_EXCEPTION",
                PushLog.error_message: f"Provider request failed (exception_type={exception_type})",
                PushLog.updated_at: datetime.utcnow(),
            },
            synchronize_session=False,
        )
        db.commit()

    @classmethod
    def _build_push_params(cls, device_id: str, notification: dict) -> dict:
        title = str(notification.get("title") or "NanTuPy")[:200]
        body = str(notification.get("content") or title or "你有一条新通知")
        existing_ext = notification.get("AndroidExtParameters") or {}
        if isinstance(existing_ext, str):
            try:
                existing_ext = json.loads(existing_ext)
            except (TypeError, json.JSONDecodeError):
                existing_ext = {}
        if not isinstance(existing_ext, dict):
            existing_ext = {}
        ext = dict(existing_ext)
        ext.update(
            {
                "notification_id": str(notification.get("id") or ""),
                "type": str(notification.get("notification_type") or ""),
                "related_id": str(notification.get("related_id") or ""),
                "message_id": str(notification.get("message_id") or ""),
                "dedupe_key": cls._canonical_dedupe_key(notification),
            }
        )
        return {
            "Action": cls.ACTION,
            "AppKey": Config.ALIYUN_PUSH_APP_KEY_ANDROID,
            "Target": "DEVICE",
            "TargetValue": device_id,
            "Title": title,
            "Body": body,
            "AndroidOpenType": "ACTIVITY",
            "AndroidActivity": Config.ALIYUN_PUSH_ANDROID_ACTIVITY or "com.nonto.nonto.MainActivity",
            "AndroidNotificationChannel": Config.ALIYUN_PUSH_ANDROID_CHANNEL_ID or "nonto_message_alerts",
            "AndroidExtParameters": json.dumps(
                ext, ensure_ascii=False, separators=(",", ":"), sort_keys=True
            ),
        }

    @classmethod
    def _common_params(cls) -> dict:
        return {
            "Format": "JSON",
            "Version": cls.VERSION,
            "AccessKeyId": Config.ALIYUN_ACCESS_KEY_ID,
            "SignatureMethod": "HMAC-SHA1",
            "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "SignatureVersion": "1.0",
            "SignatureNonce": str(uuid.uuid4()),
            "RegionId": Config.ALIYUN_PUSH_REGION_ID,
        }

    @staticmethod
    def _percent_encode(value) -> str:
        return urllib.parse.quote(str(value), safe="~")

    @classmethod
    def _sign_request(cls, params: dict, method: str = "GET") -> str:
        canonicalized = "&".join(
            f"{cls._percent_encode(key)}={cls._percent_encode(params[key])}"
            for key in sorted(params)
        )
        string_to_sign = f"{method}&%2F&{cls._percent_encode(canonicalized)}"
        signing_key = f"{Config.ALIYUN_ACCESS_KEY_SECRET}&".encode("utf-8")
        digest = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha1).digest()
        return base64.b64encode(digest).decode("utf-8")

    @classmethod
    def _signed_params(cls, action_params: dict) -> dict:
        params = cls._common_params()
        params.update(action_params)
        params["Signature"] = cls._sign_request(params)
        return params

    @classmethod
    def _send_request(cls, action_params: dict) -> ProviderSendResult:
        params = cls._signed_params(action_params)
        query = urllib.parse.urlencode(params)
        endpoint = Config.ALIYUN_PUSH_ENDPOINT.rstrip("/") or "https://cloudpush.aliyuncs.com"
        url = f"{endpoint}/?{query}"
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                http_status = getattr(response, "status", None)
                if http_status is None:
                    http_status = response.getcode()
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", errors="replace")
            return ProviderSendResult(
                http_status=exc.code,
                body=cls._parse_response_payload(payload),
            )
        return ProviderSendResult(
            http_status=http_status,
            body=cls._parse_response_payload(payload),
        )

    @staticmethod
    def _parse_response_payload(payload: str) -> dict | None:
        try:
            response = json.loads(payload) if payload else None
        except (TypeError, json.JSONDecodeError):
            return None
        return response if isinstance(response, dict) else None

    @classmethod
    def _write_log(
        cls,
        db: Session,
        user_id: int,
        notification: dict,
        status: str,
        device_id: str | None = None,
        request_id: str | None = None,
        message_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> PushLog | None:
        notification_id = cls._stable_notification_id(notification)
        if notification_id is None:
            return None
        delivery_key = device_id or cls.PRE_DEVICE_LOG_KEY
        safe_error_code = cls._safe_provider_code(error_code)
        error_code = safe_error_code or ("INTERNAL_ERROR" if error_code else None)
        error_message = cls._bounded_diagnostic(
            error_message, cls.MAX_ERROR_MESSAGE_LENGTH
        )
        request_id = cls._safe_provider_id(request_id)
        message_id = cls._safe_provider_id(message_id)
        log = (
            db.query(PushLog)
            .filter(
                PushLog.notification_id == notification_id,
                PushLog.device_id == delivery_key,
            )
            .first()
        )
        if log is None:
            log = PushLog(
                user_id=user_id,
                device_id=delivery_key,
                notification_id=notification_id,
                attempt_count=0,
            )
            db.add(log)
        log.notification_type = notification.get("notification_type")
        log.title = None
        log.status = status
        log.claim_token = None
        log.claimed_at = None
        log.request_id = request_id
        log.message_id = message_id
        log.error_code = error_code
        log.error_message = error_message
        log.updated_at = datetime.utcnow()
        return log
