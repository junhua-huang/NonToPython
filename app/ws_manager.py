"""
WebSocket 连接管理 + 序号机制
管理已认证用户的 WebSocket 连接池，为每条推送消息分配用户维度的单调递增序号。
支持断线补发（sync）和 ACK 去重。
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import WebSocket
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal

logger = logging.getLogger(__name__)


class WSManager:
    """WebSocket 连接池管理器（单例）"""

    _instance: Optional["WSManager"] = None

    def __new__(cls) -> "WSManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._connections = {}          # user_id -> WebSocket
            cls._instance._conversation_users = {}   # conversation_id -> set of user_ids
        return cls._instance

    def __init__(self):
        if not hasattr(self, "_connections"):
            self._connections: dict[int, WebSocket] = {}
            self._conversation_users: dict[int, set] = {}

    @property
    def connections(self) -> dict[int, WebSocket]:
        return self._connections

    # ================================================================
    # 连接管理
    # ================================================================

    async def connect(self, user_id: int, ws: WebSocket):
        """注册用户 WebSocket 连接"""
        old = self._connections.get(user_id)
        if old is not None:
            try:
                await old.close(code=1000, reason="duplicate_connection")
                logger.info(f"[WS CONNECT] user_id={user_id} action=kick_old_connection")
            except Exception:
                pass
        self._connections[user_id] = ws
        logger.info(f"[WS CONNECT] user_id={user_id} total_online={len(self._connections)} online_users={list(self._connections.keys())}")

    async def disconnect(self, user_id: int):
        """移除用户 WebSocket 连接"""
        self._connections.pop(user_id, None)
        self.remove_user_from_all_conversations(user_id)
        logger.info(f"[WS DISCONNECT] user_id={user_id} total_online={len(self._connections)} online_users={list(self._connections.keys())}")

    def is_connected(self, user_id: int) -> bool:
        """检查用户是否在线"""
        return user_id in self._connections

    def get_online_user_ids(self) -> list[int]:
        """获取所有在线用户 ID 列表"""
        return list(self._connections.keys())

    # ================================================================
    # 发送（带序号）
    # ================================================================

    async def send_with_seq(self, user_id: int, event: str, payload: dict):
        """
        向用户推送一条带序号的消息。
        1. 分配 seq（原子递增）
        2. 写入 ws_message_log
        3. 如果用户在线，推送 {type:"message", seq, payload:{event, ...}}
           如果离线，只写日志，等用户重连后 sync 补发
        """
        full_payload = {"event": event, **payload}
        seq = self._next_seq(user_id, full_payload)

        if seq is None:
            # 写日志失败，降级为直接推送（无序号）
            logger.warning(f"[WS SEND_SEQ] user_id={user_id} event={event} seq=FAILED fallback=raw_send")
            await self._raw_send(user_id, {"type": "message", "seq": 0, "payload": full_payload})
            return

        online = self.is_connected(user_id)
        logger.info(f"[WS SEND_SEQ] user_id={user_id} event={event} seq={seq} online={online}")

        msg_envelope = {"type": "message", "seq": seq, "payload": full_payload}

        if online:
            await self._raw_send(user_id, msg_envelope)
        # 离线用户：消息已在 _next_seq 中持久化到 ws_message_log，sync 时补发

    async def send_error(self, user_id: int, client_msg_id: Optional[str], message: str):
        """向用户推送错误通知"""
        err = {"type": "error", "message": message}
        if client_msg_id:
            err["clientMsgId"] = client_msg_id
        await self._raw_send(user_id, err)

    async def send_raw(self, user_id: int, data: dict):
        """直接发送原始 JSON（用于 auth_result / ack / sync_result / pong 等非序号消息）"""
        msg_type = data.get("type", "unknown")
        logger.info(f"[WS SEND_RAW] user_id={user_id} type={msg_type} keys={list(data.keys())}")
        await self._raw_send(user_id, data)

    async def _raw_send(self, user_id: int, data: dict):
        """内部：直接发送 JSON，不经过序号系统"""
        ws = self._connections.get(user_id)
        if ws is None:
            return
        try:
            await ws.send_json(data)
        except Exception as e:
            logger.warning(f"[WS SEND FAIL] user_id={user_id}: {e}")
            await self.disconnect(user_id)

    async def broadcast(self, data: dict, exclude: Optional[int] = None):
        """向所有已连接用户广播"""
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

    # ================================================================
    # 会话房间管理（typing 等广播用）
    # ================================================================

    def join_conversation(self, user_id: int, conversation_id: int):
        if conversation_id not in self._conversation_users:
            self._conversation_users[conversation_id] = set()
        self._conversation_users[conversation_id].add(user_id)
        logger.info(f"[WS] user_id={user_id} action=join conversation_id={conversation_id}")

    def leave_conversation(self, user_id: int, conversation_id: int):
        users = self._conversation_users.get(conversation_id)
        if users:
            users.discard(user_id)
            if not users:
                del self._conversation_users[conversation_id]
        logger.info(f"[WS] user_id={user_id} action=leave conversation_id={conversation_id}")

    def remove_user_from_all_conversations(self, user_id: int):
        for conv_id in list(self._conversation_users.keys()):
            users = self._conversation_users.get(conv_id)
            if users:
                users.discard(user_id)
                if not users:
                    del self._conversation_users[conv_id]

    async def broadcast_to_conversation(self, conversation_id: int, data: dict, exclude: Optional[int] = None):
        """向会话房间内所有在线用户广播（用于 typing 等非持久化事件）"""
        user_ids = self._conversation_users.get(conversation_id, set())
        for uid in user_ids:
            if uid == exclude:
                continue
            await self._raw_send(uid, data)

    # ================================================================
    # 序号系统（核心）
    # ================================================================

    def _next_seq(self, user_id: int, payload: dict) -> Optional[int]:
        """
        原子递增用户序号并持久化消息到 ws_message_log。
        返回新序号，失败返回 None。
        """
        db: Session = SessionLocal()
        try:
            from app.models.models import WSUserSeq, WSMessageLog

            # 1. 确保 ws_user_seq 记录存在
            row = db.query(WSUserSeq).filter(WSUserSeq.user_id == user_id).first()
            if row is None:
                row = WSUserSeq(user_id=user_id, current_seq=0)
                db.add(row)
                db.flush()

            # 2. 原子递增
            db.execute(
                text("UPDATE ws_user_seq SET current_seq = current_seq + 1 WHERE user_id = :uid"),
                {"uid": user_id}
            )
            new_seq = db.execute(
                text("SELECT current_seq FROM ws_user_seq WHERE user_id = :uid"),
                {"uid": user_id}
            ).scalar()

            # 3. 写入消息日志
            log_entry = WSMessageLog(
                user_id=user_id,
                seq=new_seq,
                payload=json.dumps(payload, ensure_ascii=False),
                created_at=datetime.utcnow(),
            )
            db.add(log_entry)
            db.commit()

            return new_seq
        except Exception as e:
            logger.error(f"[WS SEQ] _next_seq failed for user_id={user_id}: {e}")
            db.rollback()
            return None
        finally:
            db.close()

    def get_messages_after_seq(self, user_id: int, last_received_seq: int, limit: int = 200) -> list[dict]:
        """
        查询 seq > lastReceivedSeq 的所有消息（用于 sync 补发）。
        返回 [{seq, payload}, ...]
        """
        db: Session = SessionLocal()
        try:
            from app.models.models import WSMessageLog

            rows = (
                db.query(WSMessageLog)
                .filter(
                    WSMessageLog.user_id == user_id,
                    WSMessageLog.seq > last_received_seq,
                )
                .order_by(WSMessageLog.seq.asc())
                .limit(limit)
                .all()
            )

            result = []
            for row in rows:
                try:
                    payload = json.loads(row.payload)
                except (json.JSONDecodeError, TypeError):
                    payload = {}
                result.append({"seq": row.seq, "payload": payload})

            return result
        except Exception as e:
            logger.error(f"[WS SYNC] get_messages_after_seq failed for user_id={user_id}: {e}")
            return []
        finally:
            db.close()

    def get_current_seq(self, user_id: int) -> int:
        """获取用户当前最大序号（无记录返回 0）"""
        db: Session = SessionLocal()
        try:
            from app.models.models import WSUserSeq
            row = db.query(WSUserSeq).filter(WSUserSeq.user_id == user_id).first()
            return row.current_seq if row else 0
        except Exception:
            return 0
        finally:
            db.close()

    # ================================================================
    # ACK 去重
    # ================================================================

    def check_and_record_dedup(self, user_id: int, client_msg_id: str) -> bool:
        """
        检查 clientMsgId 是否已处理过。
        - 已存在：返回 True（重复，应跳过）
        - 不存在：记录并返回 False（首次，应处理）
        同时清理 24h 前的旧记录。
        """
        if not client_msg_id:
            return False

        db: Session = SessionLocal()
        try:
            from app.models.models import WSAckDedup

            existing = db.query(WSAckDedup).filter(
                WSAckDedup.client_msg_id == client_msg_id
            ).first()
            if existing:
                return True  # 重复

            entry = WSAckDedup(
                client_msg_id=client_msg_id,
                user_id=user_id,
                processed_at=datetime.utcnow(),
            )
            db.add(entry)

            # 顺便清理 24h 前的旧记录
            cutoff = datetime.utcnow() - timedelta(hours=24)
            db.query(WSAckDedup).filter(WSAckDedup.processed_at < cutoff).delete()

            db.commit()
            return False
        except Exception as e:
            logger.warning(f"[WS DEDUP] check_and_record_dedup failed: {e}")
            db.rollback()
            return False  # 出错时按首次处理
        finally:
            db.close()


# 全局单例
ws_manager = WSManager()
