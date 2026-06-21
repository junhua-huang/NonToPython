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
from typing import Optional

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
    def get_active_registration_ids(cls, db: Session, user_id: int) -> list:
        """查询用户所有激活设备的 registration_id"""
        devices = (
            db.query(UserDevice)
            .filter(UserDevice.user_id == user_id, UserDevice.is_active == True)
            .all()
        )
        return [d.registration_id for d in devices if d.registration_id]

    @classmethod
    def is_push_enabled_for_user(cls, db: Session, user_id: int) -> bool:
        """用户是否开启了推送（User.notify_push 偏好）"""
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            return False
        # notify_push 默认 True；显式为 False 才关闭
        return getattr(user, "notify_push", True) is not False

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
        if not cls._enabled():
            logger.debug("[JPUSH] disabled (missing app_key/master_secret)")
            return False

        db = SessionLocal()
        try:
            # 用户推送偏好
            if not cls.is_push_enabled_for_user(db, user_id):
                logger.debug(f"[JPUSH] user {user_id} disabled push")
                return False

            reg_ids = cls.get_active_registration_ids(db, user_id)
            if not reg_ids:
                logger.debug(f"[JPUSH] no active device for user {user_id}")
                return False
        except Exception as e:
            logger.warning(f"[JPUSH] query devices failed uid={user_id}: {e}")
            return False
        finally:
            db.close()

        # 极光 android alert 同时支持 title + alert 字段
        android_alert = {"alert": alert_content}
        if alert_title:
            android_alert["title"] = alert_title

        extras = extras or {}
        # extra_type 用于客户端路由分发
        payload = {
            "platform": "android",
            "audience": {"registration_id": reg_ids[:MAX_REGISTRATION_IDS_PER_CALL]},
            "notification": {
                "android": {**android_alert, "extras": extras},
            },
            "options": {
                "time_to_live": 86400,
                # 第三方厂商通道：让 JPush 自动下发到华为/小米/OPPO/vivo/魅族
                # 实际到达率取决于是否在极光控制台配置了各厂商证书
                "third_party_channel": {
                    "huawei": {"distribution": "ospush", "importance": "NORMAL"},
                    "xiaomi": {"distribution": "ospush", "channel_id": "nonto_message"},
                    "oppo": {"distribution": "ospush", "channel_id": "nonto_message"},
                    "vivo": {"distribution": "ospush", "classification": 1},
                    "meizu": {"distribution": "ospush"},
                },
            },
        }

        try:
            client = await cls._get_client()
            resp = await client.post(
                JPUSH_PUSH_URL,
                json=payload,
                headers={"Authorization": cls._auth_header(), "Content-Type": "application/json"},
            )
            if resp.status_code == 200:
                logger.info(f"[JPUSH] pushed uid={user_id} targets={len(reg_ids)} extras={extras.get('type')}")
                return True
            logger.warning(
                f"[JPUSH] push failed uid={user_id} status={resp.status_code} body={resp.text[:300]}"
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
