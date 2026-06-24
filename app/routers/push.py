"""
推送路由 - 极光推送 registrationId 注册/注销。

客户端在登录成功后调用 /api/push/register 上报 registrationId，
注销时调用 /api/push/unregister。服务端据此向离线用户推送系统通知。
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import User, UserDevice

router = APIRouter()
logger = logging.getLogger(__name__)

ALLOWED_APP_STATES = {"foreground", "background", "unknown"}


class PushRegisterRequest(BaseModel):
    registration_id: str = Field(..., min_length=1, max_length=255)
    platform: str = Field("android", max_length=20)
    app_version: str | None = Field(None, max_length=50)


class PushUnregisterRequest(BaseModel):
    registration_id: str = Field(..., min_length=1, max_length=255)


class PushDeviceStateRequest(BaseModel):
    registration_id: str = Field(..., min_length=1, max_length=255)
    app_state: str = Field(..., max_length=20)


@router.post("/register")
def register_device(
    body: PushRegisterRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """注册/更新设备 registrationId（upsert：同一 registration_id 更新归属与活跃时间）"""
    if not body.registration_id:
        raise HTTPException(status_code=400, detail="registration_id 不能为空")

    existing = (
        db.query(UserDevice)
        .filter(UserDevice.registration_id == body.registration_id)
        .first()
    )
    if existing:
        # 同一设备换账号登录：更新归属用户、激活状态、活跃时间
        existing.user_id = user.id
        existing.platform = body.platform or existing.platform
        existing.app_version = body.app_version or existing.app_version
        now = datetime.utcnow()
        existing.is_active = True
        existing.last_active_at = now
        existing.app_state = existing.app_state or "unknown"
        existing.app_state_updated_at = existing.app_state_updated_at or now
        db.commit()
        db.refresh(existing)
        device = existing
    else:
        now = datetime.utcnow()
        device = UserDevice(
            user_id=user.id,
            registration_id=body.registration_id,
            platform=body.platform,
            app_version=body.app_version,
            is_active=True,
            last_active_at=now,
            app_state="unknown",
            app_state_updated_at=now,
        )
        db.add(device)
        db.commit()
        db.refresh(device)

    return {"success": True, "device": device.to_dict()}


@router.post("/device-state")
def update_device_state(
    body: PushDeviceStateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新当前登录用户某个设备的前后台状态。"""
    app_state = (body.app_state or "unknown").strip().lower()
    if app_state not in ALLOWED_APP_STATES:
        raise HTTPException(status_code=400, detail="app_state must be foreground, background, or unknown")

    device = (
        db.query(UserDevice)
        .filter(
            UserDevice.registration_id == body.registration_id,
            UserDevice.user_id == user.id,
        )
        .first()
    )
    if not device:
        logger.warning("[PUSH STATE] device not found uid=%s state=%s", user.id, app_state)
        return {"success": True, "updated": False}

    now = datetime.utcnow()
    device.app_state = app_state
    device.app_state_updated_at = now
    device.last_active_at = now
    device.is_active = True
    db.commit()
    db.refresh(device)
    logger.info("[PUSH STATE] updated uid=%s device=%s state=%s", user.id, device.id, app_state)
    return {"success": True, "updated": True, "device": device.to_dict()}


@router.post("/unregister")
def unregister_device(
    body: PushUnregisterRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """注销设备（标记为 inactive，保留记录便于审计）"""
    device = (
        db.query(UserDevice)
        .filter(
            UserDevice.registration_id == body.registration_id,
            UserDevice.user_id == user.id,
        )
        .first()
    )
    if device:
        device.is_active = False
        db.commit()
    return {"success": True}
