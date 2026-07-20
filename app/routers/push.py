"""Authenticated mobile push device endpoints."""
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import PushDevice, User
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
    """Register or update the current user's Aliyun push device."""
    device_id = _trim(payload.get("device_id"), 128)
    if not device_id:
        raise HTTPException(status_code=400, detail="device_id is required")

    was_product_online = _capture_product_presence(db, current_user.id)
    device = db.query(PushDevice).filter(PushDevice.device_id == device_id).first()
    if not device:
        device = PushDevice(device_id=device_id, user_id=current_user.id)
        db.add(device)

    device.user_id = current_user.id
    device.platform = _trim(payload.get("platform") or "android", 20)
    device.provider = _trim(payload.get("provider") or "aliyun", 30)
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
    return {"registered": True, "device": device.to_dict()}


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
