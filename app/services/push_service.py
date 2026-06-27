"""
极光推送 (JPush) 服务 - 封装 JPush REST API v3。

调用点：当用户离线（ws_manager.is_connected(uid) == False）时，由
notification_service / ws 聊天 / HTTP 聊天三处触发，向该用户已注册的
设备发送系统推送。推送内容走厂商通道（华为/小米/OPPO/vivo/魅族）。

Master Secret 仅存在于服务端，绝不返回给客户端。
"""
import asyncio
import base64
import logging
from datetime import datetime
from typing import Iterable, Optional

import httpx
from sqlalchemy.orm import Session

from app.core.config import Config
from app.database import SessionLocal
from app.models.models import User, UserDevice

logger = logging.getLogger(__name__)

JPUSH_PUSH_URL = "https://api.jpush.cn/v3/push"
# 单次推送最多目标 registration_id 数量
MAX_REGISTRATION_IDS_PER_CALL = 1000


class PushService:
    """极光推送 REST 封装（异步，线程安全调度）"""

    # 复用 httpx.AsyncClient，连接池提升性能
    _client: Optional[httpx.AsyncClient] = None
    _client_lock = asyncio.Lock()

    @classmethod
    async def _get_client(cls) -> httpx.AsyncClient:
        if cls._client is None or cls._client.is_closed:
            async with cls._client_lock:
                if cls._client is None or cls._client.is_closed:
                    cls._client = httpx.AsyncClient(timeout=10.0)
        return cls._client

    @classmethod
    def _auth_header(cls) -> str:
        """HTTP Basic Auth: base64(app_key:master_secret)"""
        raw = f"{Config.JPUSH_APP_KEY}:{Config.JPUSH_MASTER_SECRET}"
        return "Basic " + base64.b64encode(raw.encode("utf-8")).decode("utf-8")

    @classmethod
    def _enabled(cls) -> bool:
        """极光是否配置完整（缺 AppKey/Master Secret 时直接跳过，避免无谓请求）"""
        return bool(Config.JPUSH_APP_KEY and Config.JPUSH_MASTER_SECRET)

    @classmethod
    def is_foreground_active(cls, device: UserDevice, now: Optional[datetime] = None) -> bool:
        """仅最近上报 foreground 的设备视为前台活跃。"""
        if getattr(device, "app_state", None) != "foreground":
            return False
        updated_at = getattr(device, "app_state_updated_at", None)
        if updated_at is None:
            return False
        now = now or datetime.utcnow()
        age_seconds = (now - updated_at).total_seconds()
        return 0 <= age_seconds <= Config.PUSH_FOREGROUND_ACTIVE_SECONDS

    @classmethod
    def filter_push_registration_ids(cls, devices: Iterable[UserDevice]) -> list[str]:
        """返回应接收系统推送的活跃 Android registration_id。"""
        targets: list[str] = []
        for device in devices:
            if not getattr(device, "is_active", False):
                continue
            if (getattr(device, "platform", "android") or "android").lower() != "android":
                continue
            registration_id = getattr(device, "registration_id", None)
            if not registration_id:
                continue
            if cls.is_foreground_active(device):
                continue
            targets.append(registration_id)
        return targets

    @classmethod
    def has_push_target(cls, db: Session, user_id: int) -> bool:
        devices = (
            db.query(UserDevice)
            .filter(UserDevice.user_id == user_id, UserDevice.is_active == True)
            .all()
        )
        return bool(cls.filter_push_registration_ids(devices))

    @classmethod
    def get_push_registration_ids(cls, db: Session, user_id: int) -> list[str]:
        """查询该用户应接收系统推送的 Android registration_id。"""
        devices = (
            db.query(UserDevice)
            .filter(UserDevice.user_id == user_id, UserDevice.is_active == True)
            .all()
        )
        return cls.filter_push_registration_ids(devices)

    @classmethod
    def is_push_enabled_for_user(cls, db: Session, user_id: int) -> bool:
        """用户是否开启了推送（User.notify_push 偏好）"""
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            return False
        # notify_push 默认 True；显式为 False 才关闭
        return getattr(user, "notify_push", True) is not False

    @classmethod
    def build_android_payload(
        cls,
        reg_ids: list[str],
        alert_title: str,
        alert_content: str,
        extras: Optional[dict] = None,
    ) -> dict:
        android_alert = {"alert": alert_content}
        if alert_title:
            android_alert["title"] = alert_title

        payload = {
            "platform": "android",
            "audience": {"registration_id": reg_ids[:MAX_REGISTRATION_IDS_PER_CALL]},
            "notification": {
                "android": {**android_alert, "extras": extras or {}},
            },
            "options": {"time_to_live": 86400},
        }
        if Config.JPUSH_ENABLE_THIRD_PARTY_CHANNEL:
            channel_templates = {
                "huawei": {"distribution": "ospush", "importance": "NORMAL"},
                "xiaomi": {"distribution": "ospush", "channel_id": "nonto_message"},
                "oppo": {"distribution": "ospush", "channel_id": "nonto_message"},
                "vivo": {"distribution": "ospush", "classification": 1},
                "meizu": {"distribution": "ospush"},
            }
            enabled_channels = {
                name: config
                for name, config in channel_templates.items()
                if name in Config.JPUSH_THIRD_PARTY_CHANNELS
            }
            if enabled_channels:
                payload["options"]["third_party_channel"] = enabled_channels
        return payload

    @classmethod
    async def send_to_user(
        cls,
        user_id: int,
        alert_title: str,
        alert_content: str,
        extras: Optional[dict] = None,
    ) -> bool:
        """
        向指定用户所有激活设备推送一条通知。

        :param user_id: 接收者用户 ID
        :param alert_title: 通知标题（部分厂商通道显示，android.alert 里拼 title）
        :param alert_content: 通知正文
        :param extras: 自定义键值，客户端点击时用于跳转路由
                       推荐 {type: message|like|comment|friend_request|..., related_id, related_type}
        :return: 是否成功（失败仅记日志，不抛异常，避免影响主流程）
        """
        extras = extras or {}
        if not cls._enabled():
            logger.debug("[JPUSH] disabled (missing app_key/master_secret)")
            return False

        db = SessionLocal()
        try:
            # 用户推送偏好
            if not cls.is_push_enabled_for_user(db, user_id):
                logger.info("[JPUSH] user disabled push uid=%s", user_id)
                return False

            reg_ids = cls.get_push_registration_ids(db, user_id)
            if not reg_ids:
                logger.info("[JPUSH] no eligible push target uid=%s", user_id)
                return False
        except Exception as e:
            logger.warning(f"[JPUSH] query devices failed uid={user_id}: {e}")
            return False
        finally:
            db.close()

        payload = cls.build_android_payload(
            reg_ids=reg_ids,
            alert_title=alert_title,
            alert_content=alert_content,
            extras=extras,
        )
        enabled_channels = sorted(Config.JPUSH_THIRD_PARTY_CHANNELS)

        try:
            client = await cls._get_client()
            resp = await client.post(
                JPUSH_PUSH_URL,
                json=payload,
                headers={"Authorization": cls._auth_header(), "Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                logger.info(
                    "[JPUSH] pushed uid=%s targets=%s type=%s third_party=%s channels=%s",
                    user_id,
                    len(reg_ids),
                    extras.get("type"),
                    Config.JPUSH_ENABLE_THIRD_PARTY_CHANNEL,
                    enabled_channels,
                )
                return True
            logger.warning(
                "[JPUSH] push failed uid=%s targets=%s status=%s third_party=%s channels=%s body=%s",
                user_id,
                len(reg_ids),
                resp.status_code,
                Config.JPUSH_ENABLE_THIRD_PARTY_CHANNEL,
                enabled_channels,
                resp.text[:300],
            )
            return False
        except Exception as e:
            logger.warning(f"[JPUSH] push exception uid={user_id}: {e}")
            return False

    @classmethod
    def schedule_send_to_user(
        cls,
        user_id: int,
        alert_title: str,
        alert_content: str,
        extras: Optional[dict] = None,
    ) -> None:
        """
        从同步上下文（如 SQLAlchemy 线程）安全调度一次推送。
        通过 ws_manager.schedule_push 投递到主事件循环执行。
        """
        if not cls._enabled():
            return
        try:
            from app.ws_manager import ws_manager

            coro = cls.send_to_user(user_id, alert_title, alert_content, extras)
            ws_manager.schedule_push(coro)
        except Exception as e:
            logger.warning(f"[JPUSH] schedule failed uid={user_id}: {e}")
