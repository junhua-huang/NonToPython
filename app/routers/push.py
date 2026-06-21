"""
推送路由 - 极光推送 registrationId 注册/注销。

客户端在登录成功后调用 /api/push/register 上报 registrationId，
注销时调用 /api/push/unregister。服务端据此向离线用户推送系统通知。
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import User, UserDevice

router = APIRouter()


class PushRegisterRequest(BaseModel):
    registration_id: str = Field(..., min_length=1, max_length=255)
    platform: str = Field("android", max_length=20)
    app_version: str | None = Field(None, max_length=50)


class PushUnregisterRequest(BaseModel):
    registration_id: str = Field(..., min_length=1, max_length=255)


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
        existing.is_active = True
        existing.last_active_at = datetime.utcnow()
        db.commit()
        db.refresh(existing)
        device = existing
    else:
        device = UserDevice(
            user_id=user.id,
            registration_id=body.registration_id,
            platform=body.platform,
            app_version=body.app_version,
            is_active=True,
            last_active_at=datetime.utcnow(),
        )
        db.add(device)
        db.commit()
        db.refresh(device)

    return {"success": True, "device": device.to_dict()}


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
