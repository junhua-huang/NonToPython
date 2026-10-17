"""Authenticated mobile push device endpoints."""
from datetime import datetime, timedelta
import hashlib
import hmac
import re

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.config import Config
from app.services.apns_push_service import APNsPushService
from app.dependencies import get_current_user
from app.models.models import PushDevice, PushLog, Notification, User
from app.services.presence_service import ACTIVE_APP_STATES, BACKGROUND_ACTIVE_SECONDS, is_user_product_online
from app.ws_manager import ws_manager

router = APIRouter()

VALID_APP_STATES = {"foreground", "background", "inactive", "unknown"}


def _trim(value, max_length: int):
    if value is None:
        return None
    return str(value).strip()[:max_length]


def _capture_product_presence(db: Session, user_id: int) -> bool:
    """Capture product presence before mutating device state."""
    return is_user_product_online(
        db,
        user_id,
        raw_ws_online=ws_manager.is_connected(user_id),
    )


def _notify_product_presence_transition(user_id: int, was_product_online: bool, app_state: str | None):
    """Emit safe product-presence transition notifications after a committed device update."""
    presence_generation = ws_manager.bump_presence_generation(user_id)
    if app_state in ACTIVE_APP_STATES:
        if not was_product_online and not ws_manager.is_connected(user_id):
            ws_manager.schedule_presence_online_from_product_state(user_id, presence_generation)
        ws_manager.schedule_offline_presence_check(user_id, BACKGROUND_ACTIVE_SECONDS, presence_generation)
    elif was_product_online and not ws_manager.is_connected(user_id):
        ws_manager.schedule_offline_presence_check(
            user_id,
            delay_seconds=0,
            expected_generation=presence_generation,
        )


def _after_device_state_change(user_id: int, was_product_online: bool, app_state: str | None):
    _notify_product_presence_transition(user_id, was_product_online, app_state)


@router.post("/devices/register")
def register_device(
    payload: dict = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Register Android or native iOS push for the authenticated account."""
    provider = _trim(payload.get("provider") or "aliyun", 30)
    apns = provider == "apns"
    if apns:
        raw_id = payload.get("device_id")
        token = payload.get("apns_token")
        secret = payload.get("binding_secret")
        environment = payload.get("apns_environment")
        topic = payload.get("apns_topic")
        if (not isinstance(raw_id, str) or not re.fullmatch(r"apns:[a-f0-9-]{36}", raw_id)
                or not isinstance(token, str) or not re.fullmatch(r"[a-fA-F0-9]{2,512}", token) or len(token) % 2
                or not isinstance(secret, str) or not re.fullmatch(r"[a-f0-9]{64}", secret)
                or payload.get("platform") != "ios"
                or environment not in {"development", "production"} or topic != Config.APNS_BUNDLE_ID):
            raise HTTPException(status_code=400, detail="invalid APNs registration")
    device_id = _trim(payload.get("device_id"), 128)
    if not device_id:
        raise HTTPException(status_code=400, detail="device_id is required")

    was_product_online = _capture_product_presence(db, current_user.id)
    device = db.query(PushDevice).filter(PushDevice.device_id == device_id).first()
    if device and device.provider == "apns":
        digest = hashlib.sha256(str(payload.get("binding_secret", "")).encode()).hexdigest()
        # Older servers accepted provider=apns without storing any APNs token or
        # secret. Such inert rows can be upgraded; a usable binding always needs its secret.
        legacy_empty = device.binding_secret_hash is None and device.apns_token is None
        if not apns or (not legacy_empty and (not device.binding_secret_hash or not hmac.compare_digest(device.binding_secret_hash, digest))):
            raise HTTPException(status_code=403, detail="invalid installation credential")
    if not device:
        device = PushDevice(device_id=device_id, user_id=current_user.id)
        db.add(device)

    device.user_id = current_user.id
    device.platform = _trim(payload.get("platform") or "android", 20)
    device.provider = provider
    if apns:
        device.apns_token = token.lower()
        device.apns_environment = environment
        device.apns_topic = topic
        device.binding_secret_hash = hashlib.sha256(secret.encode()).hexdigest()
    device.manufacturer = _trim(payload.get("manufacturer"), 80)
    device.model = _trim(payload.get("model"), 120)
    device.app_version = _trim(payload.get("app_version"), 50)
    now = datetime.utcnow()
    app_state = _trim(payload.get("app_state"), 20)
    if app_state:
        if app_state not in VALID_APP_STATES:
            raise HTTPException(status_code=400, detail="invalid app_state")
        device.app_state = app_state
        device.app_state_updated_at = now
        if app_state == "foreground":
            device.last_foreground_at = now
        elif app_state == "background":
            device.last_background_at = now
    elif not device.app_state:
        device.app_state = "unknown"
        device.app_state_updated_at = now
    device.enabled = True
    device.last_seen_at = now
    device.updated_at = now

    db.commit()
    db.refresh(device)
    _after_device_state_change(current_user.id, was_product_online, app_state)
    return {"registered": True, "device": device.to_dict(),
            "apns_supported": True, "apns_configured": APNsPushService.is_configured()}


@router.post("/devices/unregister")
def unregister_device(
    payload: dict = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Disable a push device for the current user."""
    device_id = _trim(payload.get("device_id"), 128)
    if not device_id:
        raise HTTPException(status_code=400, detail="device_id is required")

    was_product_online = _capture_product_presence(db, current_user.id)
    device = (
        db.query(PushDevice)
        .filter(PushDevice.device_id == device_id, PushDevice.user_id == current_user.id)
        .first()
    )
    if not device:
        return {"unregistered": False}

    device.enabled = False
    device.updated_at = datetime.utcnow()
    db.commit()
    _after_device_state_change(current_user.id, was_product_online, "unknown")
    return {"unregistered": True}


@router.post("/devices/revoke")
def revoke_installation(payload: dict = Body(...), db: Session = Depends(get_db)):
    """Installation-secret revocation also works after an account token expires.

    No user identity or binding state is disclosed. The secret is never returned.
    """
    device_id, secret = payload.get("device_id"), payload.get("binding_secret")
    if not isinstance(device_id, str) or not isinstance(secret, str) or not re.fullmatch(r"[a-f0-9]{64}", secret):
        raise HTTPException(status_code=400, detail="invalid installation credential")
    device = db.query(PushDevice).filter(PushDevice.device_id == device_id, PushDevice.provider == "apns").first()
    if device and device.binding_secret_hash and hmac.compare_digest(device.binding_secret_hash, hashlib.sha256(secret.encode()).hexdigest()):
        was_online = _capture_product_presence(db, device.user_id)
        user_id = device.user_id
        device.enabled = False
        device.apns_token = None
        device.updated_at = datetime.utcnow()
        db.commit()
        _after_device_state_change(user_id, was_online, "unknown")
    return {"revoked": True}


@router.post("/devices/state")
def update_device_state(
    payload: dict = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update the current device foreground/background state."""
    device_id = _trim(payload.get("device_id"), 128)
    app_state = _trim(payload.get("app_state"), 20)
    provider = _trim(payload.get("provider") or "aliyun", 30)
    if not device_id:
        raise HTTPException(status_code=400, detail="device_id is required")
    if app_state not in VALID_APP_STATES:
        raise HTTPException(status_code=400, detail="invalid app_state")

    was_product_online = _capture_product_presence(db, current_user.id)
    device = (
        db.query(PushDevice)
        .filter(
            PushDevice.device_id == device_id,
            PushDevice.user_id == current_user.id,
            PushDevice.provider == provider,
        )
        .first()
    )
    if not device:
        raise HTTPException(status_code=404, detail="device not registered")

    now = datetime.utcnow()
    device.app_state = app_state
    device.app_state_updated_at = now
    device.last_seen_at = now
    device.updated_at = now
    if app_state == "foreground":
        device.last_foreground_at = now
    elif app_state == "background":
        device.last_background_at = now
    device.enabled = True
    db.commit()
    db.refresh(device)
    _after_device_state_change(current_user.id, was_product_online, app_state)
    return {"updated": True, "device": device.to_dict()}


@router.get("/devices/status")
def device_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return enabled push devices for the current user."""
    devices = (
        db.query(PushDevice)
        .filter(PushDevice.user_id == current_user.id, PushDevice.enabled == True)
        .order_by(PushDevice.last_seen_at.desc())
        .all()
    )
    return {"devices": [device.to_dict() for device in devices]}


@router.post("/devices/test")
def test_apns(payload: dict = Body(...), current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """An authenticated user can test only their own enabled APNs installation."""
    if not APNsPushService.is_configured():
        raise HTTPException(status_code=503, detail="APNs server credentials are not configured")
    if current_user.notify_push is False:
        raise HTTPException(status_code=409, detail="Push notifications are disabled")
    device = db.query(PushDevice).filter(PushDevice.user_id == current_user.id,
        PushDevice.device_id == _trim(payload.get("device_id"), 128), PushDevice.provider == "apns", PushDevice.enabled == True).first()
    if not device:
        raise HTTPException(status_code=404, detail="APNs device not registered")
    recent = db.query(Notification.id).filter(Notification.user_id == current_user.id,
        Notification.related_type == "push_test", Notification.created_at >= datetime.utcnow() - timedelta(seconds=60)).first()
    if recent:
        raise HTTPException(status_code=429, detail="Please wait 60 seconds before testing again")
    from app.services.notification_service import NotificationService
    notification = NotificationService.create_notification(user_id=current_user.id, notification_type="system",
        title="系统推送测试", content="这是一条通过 APNs 发送的远程测试通知。", related_type="push_test", db=db)
    if notification is None:
        raise HTTPException(status_code=503, detail="Could not create test notification")
    delivery = db.query(PushLog).filter(PushLog.device_id == device.device_id, PushLog.notification_id == notification.id).first()
    return {"submitted": bool(delivery and delivery.status == "success"),
            "error_code": delivery.error_code if delivery else "NO_DELIVERY", "notification_id": notification.id}
