"""
WebSocket 连接管理
管理已认证用户的 WebSocket 连接池
"""
import logging
from typing import Optional
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WSManager:
    """WebSocket 连接池管理器（单例）"""

    _instance: Optional["WSManager"] = None

    def __new__(cls) -> "WSManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._connections = {}
            cls._instance._conversation_users = {}  # conversation_id → set of user_ids
        return cls._instance

    def __init__(self):
        if not hasattr(self, "_connections"):
            self._connections: dict[int, WebSocket] = {}
            self._conversation_users: dict[int, set] = {}

    @property
    def connections(self) -> dict[int, WebSocket]:
        return self._connections

    async def connect(self, user_id: int, ws: WebSocket):
        """注册用户 WebSocket 连接"""
        old = self._connections.get(user_id)
        if old is not None:
            try:
                await old.close(code=1000, reason="duplicate_connection")
            except Exception:
                pass
        self._connections[user_id] = ws
        # 同步到 socket_handlers 全局在线状态（直接导入，不做静默忽略）
        from app.socket_handlers import set_user_online
        set_user_online(user_id)
        logger.info(f"WS connected: user_id={user_id}, total={len(self._connections)}")

    async def disconnect(self, user_id: int):
        """移除用户 WebSocket 连接"""
        self._connections.pop(user_id, None)
        self.remove_user_from_all_conversations(user_id)
        from app.socket_handlers import set_user_offline
        set_user_offline(user_id)
        logger.info(f"WS disconnected: user_id={user_id}, total={len(self._connections)}")

    def is_connected(self, user_id: int) -> bool:
        return user_id in self._connections

    async def send(self, user_id: int, data: dict):
        """向指定用户发送 JSON 消息"""
        ws = self._connections.get(user_id)
        if ws is None:
            return
        try:
            await ws.send_json(data)
        except Exception as e:
            logger.warning(f"WS send failed for user_id={user_id}: {e}")
            await self.disconnect(user_id)

    async def broadcast(self, data: dict, exclude: Optional[int] = None):
        """向所有已连接用户广播消息"""
        disconnected = []
        for uid, ws in list(self._connections.items()):
            if uid == exclude:
                continue
            try:
                await ws.send_json(data)
            except Exception as e:
                logger.warning(f"WS broadcast failed for user_id={uid}: {e}")
                disconnected.append(uid)
        for uid in disconnected:
            await self.disconnect(uid)

    def join_conversation(self, user_id: int, conversation_id: int):
        """用户加入会话房间"""
        if conversation_id not in self._conversation_users:
            self._conversation_users[conversation_id] = set()
        self._conversation_users[conversation_id].add(user_id)

    def leave_conversation(self, user_id: int, conversation_id: int):
        """用户离开会话房间"""
        users = self._conversation_users.get(conversation_id)
        if users:
            users.discard(user_id)
            if not users:
                del self._conversation_users[conversation_id]

    def remove_user_from_all_conversations(self, user_id: int):
        """用户断开连接时，从所有会话房间移除"""
        for conv_id in list(self._conversation_users.keys()):
            users = self._conversation_users.get(conv_id)
            if users:
                users.discard(user_id)
                if not users:
                    del self._conversation_users[conv_id]

    async def broadcast_to_conversation(self, conversation_id: int, data: dict, exclude: Optional[int] = None):
        """向指定会话房间内所有在线用户广播消息"""
        user_ids = self._conversation_users.get(conversation_id, set())
        for uid in user_ids:
            if uid == exclude:
                continue
            await self.send(uid, data)


# 全局单例
ws_manager = WSManager()
