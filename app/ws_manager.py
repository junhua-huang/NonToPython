"""
WebSocket 连接管理 + 序号机制

特性:
- 多设备支持：同一用户可同时多设备在线，推送覆盖所有连接
- 心跳超时：120s 未收到心跳主动断开
- 序号系统：单用户严格递增 seq，离线可补发
- ACK 去重：24h clientMsgId 幂等
"""
import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import WebSocket
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal

logger = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT = 120  # 120 秒无心跳视为断线
PER_USER_BACKLOG_LIMIT = 50  # 单用户推送积压上限，超过则跳过实时推送（走 sync 补发）


class WSManager:
    """WebSocket 连接池管理器（单例）"""

    _instance: Optional["WSManager"] = None

    def __new__(cls) -> "WSManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._connections: dict[int, dict[str, WebSocket]] = {}
            cls._instance._last_ping: dict[str, float] = {}
            cls._instance._conversation_users: dict[int, set[int]] = {}
            cls._instance._session_cache: dict[int, tuple[float, list[dict]]] = {}
            cls._instance._conv_ids_cache: dict[int, tuple[float, list[int]]] = {}
            cls._instance._blocked_cache: dict[int, tuple[float, set[int]]] = {}
            cls._instance._loop: Optional[asyncio.AbstractEventLoop] = None
            cls._instance._heartbeat_task_started = False
            cls._instance._push_queue: asyncio.Queue = asyncio.Queue()
            cls._instance._push_worker_started = False
            cls._instance._per_user_pending: dict[int, int] = {}
            cls._instance._presence_generation: dict[int, int] = {}
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        if not hasattr(self, "_connections"):
            self._connections: dict[int, dict[str, WebSocket]] = {}
            self._last_ping: dict[str, float] = {}
            self._conversation_users: dict[int, set[int]] = {}
            self._session_cache: dict[int, tuple[float, list[dict]]] = {}
            self._conv_ids_cache: dict[int, tuple[float, list[int]]] = {}
            self._blocked_cache: dict[int, tuple[float, set[int]]] = {}
            self._loop: Optional[asyncio.AbstractEventLoop] = None
            self._heartbeat_task_started = False
            self._push_queue: asyncio.Queue = asyncio.Queue()
            self._push_worker_started = False
            self._per_user_pending: dict[int, int] = {}
            self._presence_generation: dict[int, int] = {}
        self._initialized = True

    # ================================================================
    # 推送队列 — 解耦"落库+ACK"与"推送接收方"
    # ================================================================

    def _start_push_worker(self):
        if self._push_worker_started:
            return
        self._push_worker_started = True
        if self._loop and self._loop.is_running():
            asyncio.ensure_future(self._push_worker())
        else:
            logger.warning("[WS QUEUE] Cannot start: event loop not ready")

    async def _push_worker(self):
        logger.info("[WS QUEUE] worker started")
        while True:
            try:
                task = await self._push_queue.get()
                user_id, event, payload = task
                try:
                    await self.send_with_seq(user_id, event, payload)
                finally:
                    # 递减该用户的 pending 计数
                    pending = self._per_user_pending.get(user_id, 0)
                    if pending <= 1:
                        self._per_user_pending.pop(user_id, None)
                    else:
                        self._per_user_pending[user_id] = pending - 1
                    self._push_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[WS QUEUE] worker error: {e}")

    def enqueue_push(self, user_id: int, event: str, payload: dict):
        """将推送任务入队。自动判断：在线且未积压 → 走推送队列；否则只写序号日志（用户重连后 sync 补发）"""
        self._start_push_worker()

        # 用户离线 → 只写序号日志，不入推送队列
        # （消息已持久化在 messages 表，序号日志保证 sync 可补发，WS 推了也收不到）
        if not self.is_connected(user_id):
            self._ensure_seq_logged(user_id, event, payload)
            return

        # 在线但积压过多 → 同理，跳过实时推送，让 sync 接盘
        pending = self._per_user_pending.get(user_id, 0)
        if pending >= PER_USER_BACKLOG_LIMIT:
            logger.info(f"[WS QUEUE] backlog limit({PER_USER_BACKLOG_LIMIT}) for uid={user_id}, falling back to sync")
            self._ensure_seq_logged(user_id, event, payload)
            return

        # 正常入队
        self._per_user_pending[user_id] = pending + 1
        self._push_queue.put_nowait((user_id, event, payload))

    def _ensure_seq_logged(self, user_id: int, event: str, payload: dict):
        """为离线/积压用户写入序号日志（不入推送队列）。
        用户重连后通过 sync 机制补发，无需实时 WS 推送。"""
        full_payload = {"event": event, "data": payload}
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._next_seq(user_id, full_payload), self._loop)
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._next_seq_sync(user_id, full_payload)
            return
        loop.create_task(self._next_seq(user_id, full_payload))

    def enqueue_push_batch(self, items: list[tuple[int, str, dict]]):
        for uid, evt, pld in items:
            self.enqueue_push(uid, evt, pld)

    # ================================================================
    # 在线用户数
    # ================================================================

    @property
    def online_count(self) -> int:
        return len(self._connections)

    @property
    def total_connections(self) -> int:
        return sum(len(conns) for conns in self._connections.values())

    # ================================================================
    # 连接管理（多设备）
    # ================================================================

    async def connect(self, user_id: int, ws: WebSocket) -> str:
        """注册用户 WebSocket 连接，返回 connection_id。支持多设备。"""
        if self._loop is None:
            self._loop = asyncio.get_event_loop()

        conn_id = str(uuid.uuid4())
        if user_id not in self._connections:
            self._connections[user_id] = {}
        self._connections[user_id][conn_id] = ws
        self._last_ping[conn_id] = time.time()

        self._start_heartbeat_checker()

        logger.info(f"[WS +] uid={user_id} cid={conn_id[:8]} "
                     f"user_devices={len(self._connections[user_id])} "
                     f"online_users={len(self._connections)} "
                     f"total_conns={self.total_connections}")
        return conn_id

    async def disconnect(self, user_id: int, conn_id: str = None):
        """移除用户连接。conn_id 为空则移除该用户全部连接。"""
        if user_id not in self._connections:
            return

        if conn_id:
            removed = self._connections[user_id].pop(conn_id, None)
            self._last_ping.pop(conn_id, None)
            if removed:
                try:
                    await removed.close(code=1000, reason="server_disconnect")
                except Exception:
                    pass
        else:
            for cid in list(self._connections[user_id].keys()):
                self._last_ping.pop(cid, None)
                try:
                    await self._connections[user_id][cid].close(code=1000, reason="server_disconnect")
                except Exception:
                    pass
            self._connections[user_id].clear()

        if not self._connections[user_id]:
            del self._connections[user_id]
            self.remove_user_from_all_conversations(user_id)
            self.invalidate_user_caches(user_id)

            # 用户全部设备离线 → 通知相关社群成员和在线好友该用户已下线
            presence_generation = self.bump_presence_generation(user_id)
            asyncio.ensure_future(self.notify_community_presence(user_id, False, presence_generation))
            asyncio.ensure_future(self._notify_friends_offline(user_id, presence_generation))

        logger.info(f"[WS -] uid={user_id} cid={conn_id[:8] if conn_id else 'all'} "
                     f"online_users={len(self._connections)} total_conns={self.total_connections}")

    def heartbeat(self, conn_id: str):
        """更新连接最后心跳时间"""
        self._last_ping[conn_id] = time.time()

    def is_connected(self, user_id: int) -> bool:
        """检查用户是否有任一设备在线"""
        return user_id in self._connections and len(self._connections[user_id]) > 0

    def get_online_user_ids(self) -> list[int]:
        """获取所有在线用户 ID 列表"""
        return list(self._connections.keys())

    def bump_presence_generation(self, user_id: int) -> int:
        """Increment and return the user's presence transition generation."""
        generation = self._presence_generation.get(user_id, 0) + 1
        self._presence_generation[user_id] = generation
        return generation

    def _is_presence_generation_current(self, user_id: int, expected_generation: int | None) -> bool:
        if expected_generation is None:
            return True
        return self._presence_generation.get(user_id, 0) == expected_generation

    # ================================================================
    # 心跳超时检测
    # ================================================================

    _heartbeat_task_started = False
    _log_prune_task_started = False
    _LOG_PRUNE_DAYS = 7  # 保留最近 7 天的 ws_message_log

    def _start_heartbeat_checker(self):
        """启动心跳超时检查后台任务（仅一次）"""
        if self._heartbeat_task_started:
            return
        self._heartbeat_task_started = True
        if self._loop and self._loop.is_running():
            asyncio.ensure_future(self._heartbeat_check_loop())
            self._start_log_prune_task()
        else:
            logger.warning("[WS HB] Cannot start heartbeat checker: event loop not running")

    async def _heartbeat_check_loop(self):
        """每 30 秒检查一次，断开超时连接"""
        while True:
            await asyncio.sleep(30)
            now = time.time()
            stale = []
            for conn_id, last in list(self._last_ping.items()):
                if now - last > HEARTBEAT_TIMEOUT:
                    stale.append(conn_id)

            for conn_id in stale:
                # 找到归属用户
                for uid, conns in list(self._connections.items()):
                    if conn_id in conns:
                        logger.info(f"[WS HB] timeout uid={uid} cid={conn_id[:8]}")
                        await self.disconnect(uid, conn_id)
                        break

    def _start_log_prune_task(self):
        """启动 ws_message_log 定期清理任务（仅一次）"""
        if self._log_prune_task_started:
            return
        self._log_prune_task_started = True
        asyncio.ensure_future(self._log_prune_loop())

    async def _log_prune_loop(self):
        """每小时清理一次超过 _LOG_PRUNE_DAYS 天的 ws_message_log"""
        while True:
            await asyncio.sleep(3600)  # 每小时执行一次
            try:
                await self._prune_message_logs()
            except Exception as e:
                logger.error(f"[WS PRUNE] error: {e}")

    async def _prune_message_logs(self):
        """删除超过保留天数的 ws_message_log 记录"""
        loop = asyncio.get_event_loop()

        def _sync():
            db = SessionLocal()
            try:
                from app.models.models import WSMessageLog
                cutoff = datetime.utcnow() - timedelta(days=self._LOG_PRUNE_DAYS)
                count = db.query(WSMessageLog).filter(
                    WSMessageLog.created_at < cutoff
                ).delete(synchronize_session=False)
                db.commit()
                if count > 0:
                    logger.info(f"[WS PRUNE] deleted {count} message_log entries older than {self._LOG_PRUNE_DAYS} days")
            except Exception as e:
                logger.error(f"[WS PRUNE] sync error: {e}")
                db.rollback()
            finally:
                db.close()

        await loop.run_in_executor(None, _sync)

    # ================================================================
    # 发送 — 推送到用户所有设备
    # ================================================================

    async def _raw_send_to_connections(self, user_id: int, data: dict):
        """向用户的所有在线设备发送消息"""
        conns = self._connections.get(user_id, {})
        if not conns:
            return
        dead = []
        for conn_id, ws in list(conns.items()):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(conn_id)
        for conn_id in dead:
            await self.disconnect(user_id, conn_id)

    async def send_with_seq(self, user_id: int, event: str, data: dict):
        """向用户推送一条带序号的消息。覆盖所有设备。
        格式: {type:"message", seq:N, payload:{event, data:{...}}}"""
        full_payload = {"event": event, "data": data}
        seq = await self._next_seq(user_id, full_payload)

        if seq is None:
            logger.warning(f"[WS SEND] uid={user_id} {event} seq=FAILED")
            await self._raw_send_to_connections(user_id, {
                "type": "message", "seq": 0, "payload": full_payload
            })
            return

        if self.is_connected(user_id):
            logger.debug(f"[WS SEND] uid={user_id} {event} seq={seq} {json.dumps(full_payload, ensure_ascii=False)[:200]}")

        msg_envelope = {"type": "message", "seq": seq, "payload": full_payload}
        if self.is_connected(user_id):
            await self._raw_send_to_connections(user_id, msg_envelope)

    async def _send_presence_with_seq(
        self,
        recipient_id: int,
        event: str,
        data: dict,
        subject_user_id: int,
        expected_generation: int | None,
    ):
        """Send presence only if the subject generation is still current at seq allocation."""
        if (
            not self.is_connected(recipient_id)
            or not self._is_presence_generation_current(subject_user_id, expected_generation)
        ):
            return

        full_payload = {"event": event, "data": data}
        seq = self._next_seq_sync(recipient_id, full_payload)
        if seq is None:
            logger.warning(f"[WS SEND] uid={recipient_id} {event} seq=FAILED")
            return

        if (
            self.is_connected(recipient_id)
            and self._is_presence_generation_current(subject_user_id, expected_generation)
        ):
            logger.debug(f"[WS SEND] uid={recipient_id} {event} seq={seq} {json.dumps(full_payload, ensure_ascii=False)[:200]}")
            await self._raw_send_to_connections(recipient_id, {
                "type": "message", "seq": seq, "payload": full_payload
            })

    async def send_error(self, user_id: int, client_msg_id: Optional[str], message: str):
        """向用户推送错误通知"""
        err = {"type": "error", "message": message}
        if client_msg_id:
            err["clientMsgId"] = client_msg_id
        await self._raw_send_to_connections(user_id, err)

    async def send_raw(self, user_id: int, data: dict):
        """直接发送原始 JSON。覆盖所有设备。"""
        msg_type = data.get("type", "unknown")
        logger.debug(f"[WS SEND] uid={user_id} {msg_type} {json.dumps(data, ensure_ascii=False)[:200]}")
        await self._raw_send_to_connections(user_id, data)

    def schedule_push(self, coro):
        """从同步线程安全调度协程到可用事件循环。"""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self._loop)
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coro)
            return
        loop.create_task(coro)

    async def _notify_friends_offline(self, user_id: int, expected_generation: int | None = None):
        """用户全部设备离线时，通知在线好友该用户已下线"""
        if self.is_connected(user_id) or not self._is_presence_generation_current(user_id, expected_generation):
            return

        loop = asyncio.get_event_loop()

        def _get_friend_ids():
            db = SessionLocal()
            try:
                from app.models.models import Friendship
                friends = db.query(Friendship).filter(
                    ((Friendship.sender_id == user_id) | (Friendship.receiver_id == user_id)),
                    Friendship.status == 'accepted'
                ).all()
                return [
                    f.sender_id if f.receiver_id == user_id else f.receiver_id
                    for f in friends
                ]
            finally:
                db.close()

        friend_ids = await loop.run_in_executor(None, _get_friend_ids)
        if self.is_connected(user_id) or not self._is_presence_generation_current(user_id, expected_generation):
            return
        for fid in friend_ids:
            if not self._is_presence_generation_current(user_id, expected_generation):
                return
            if self.is_connected(fid):
                await self._send_presence_with_seq(
                    fid,
                    "friend_offline",
                    {"user_id": user_id},
                    user_id,
                    expected_generation,
                )

    def _get_community_presence_targets_sync(self, user_id: int, online_user_ids: list[int]) -> list[dict]:
        """Return active online community recipient groups for a user's presence change."""
        if not online_user_ids:
            return []

        db = SessionLocal()
        try:
            from app.models.community import CommunityMember
            from app.models.models import Conversation

            community_rows = (
                db.query(CommunityMember.community_id)
                .filter(
                    CommunityMember.user_id == user_id,
                    CommunityMember.status == 'active',
                )
                .all()
            )
            community_ids = [row[0] for row in community_rows]
            if not community_ids:
                return []

            conversations = (
                db.query(Conversation)
                .filter(
                    Conversation.type == 'community',
                    Conversation.community_id.in_(community_ids),
                )
                .all()
            )
            conversation_by_community = {
                conv.community_id: conv.id
                for conv in conversations
                if conv.community_id is not None
            }

            member_rows = (
                db.query(CommunityMember.community_id, CommunityMember.user_id)
                .filter(
                    CommunityMember.community_id.in_(community_ids),
                    CommunityMember.status == 'active',
                    CommunityMember.user_id.in_(online_user_ids),
                )
                .all()
            )
            recipient_ids_by_community: dict[int, list[int]] = {
                community_id: [] for community_id in community_ids
            }
            for community_id, member_user_id in member_rows:
                if member_user_id != user_id:
                    recipient_ids_by_community.setdefault(community_id, []).append(member_user_id)

            return [
                {
                    "community_id": community_id,
                    "conversation_id": conversation_by_community.get(community_id),
                    "recipient_ids": recipient_ids_by_community.get(community_id, []),
                }
                for community_id in community_ids
            ]
        finally:
            db.close()

    async def notify_community_presence(self, user_id: int, is_online: bool, expected_generation: int | None = None):
        """Notify active community members that a member's app-level presence changed."""
        try:
            if (
                self.is_connected(user_id) != is_online
                or not self._is_presence_generation_current(user_id, expected_generation)
            ):
                return

            loop = asyncio.get_event_loop()
            online_user_ids = self.get_online_user_ids()
            targets = await loop.run_in_executor(None, self._get_community_presence_targets_sync, user_id, online_user_ids)
            if (
                self.is_connected(user_id) != is_online
                or not self._is_presence_generation_current(user_id, expected_generation)
            ):
                return

            for target in targets:
                payload = {
                    "community_id": target["community_id"],
                    "conversation_id": target.get("conversation_id"),
                    "user_id": user_id,
                    "is_online": is_online,
                }
                for recipient_id in target.get("recipient_ids", []):
                    if not self._is_presence_generation_current(user_id, expected_generation):
                        return
                    if self.is_connected(recipient_id):
                        await self._send_presence_with_seq(recipient_id, "community_member_presence", payload, user_id, expected_generation)
        except Exception as e:
            logger.error(f"[WS PRESENCE] community presence failed uid={user_id} online={is_online}: {e}", exc_info=True)

    # ================================================================
    # 会话列表缓存
    # ================================================================

    _SESSION_CACHE_TTL = 10.0

    def get_cached_sessions(self, user_id: int) -> Optional[list[dict]]:
        entry = self._session_cache.get(user_id)
        if entry and time.time() - entry[0] < self._SESSION_CACHE_TTL:
            return entry[1]
        return None

    def set_cached_sessions(self, user_id: int, sessions: list[dict]):
        self._session_cache[user_id] = (time.time(), sessions)

    def get_cached_conv_ids(self, user_id: int) -> Optional[list[int]]:
        entry = self._conv_ids_cache.get(user_id)
        if entry and time.time() - entry[0] < self._SESSION_CACHE_TTL:
            return entry[1]
        return None

    def set_cached_conv_ids(self, user_id: int, conv_ids: list[int]):
        self._conv_ids_cache[user_id] = (time.time(), conv_ids)

    def invalidate_session_cache(self, user_id: int):
        self._session_cache.pop(user_id, None)

    def invalidate_participant_caches(self, participant_ids: list[int]):
        for pid in participant_ids:
            self.invalidate_session_cache(pid)

    def invalidate_user_caches(self, user_id: int):
        self._session_cache.pop(user_id, None)
        self._conv_ids_cache.pop(user_id, None)
        self._blocked_cache.pop(user_id, None)

    def get_blocked_user_ids(self, user_id: int, ttl: float = 30.0) -> set[int]:
        now = time.time()
        entry = self._blocked_cache.get(user_id)
        if entry and now - entry[0] < ttl:
            return entry[1]

        from app.models.models import Block
        db = SessionLocal()
        try:
            rows = db.query(Block.blocked_id).filter(Block.blocker_id == user_id).all()
            result = {row[0] for row in rows}
        finally:
            db.close()

        self._blocked_cache[user_id] = (now, result)
        return result

    def invalidate_blocked_cache(self, user_id: int):
        self._blocked_cache.pop(user_id, None)

    # ================================================================
    # 会话房间管理
    # ================================================================

    def join_conversation(self, user_id: int, conversation_id: int):
        if conversation_id not in self._conversation_users:
            self._conversation_users[conversation_id] = set()
        self._conversation_users[conversation_id].add(user_id)

    def leave_conversation(self, user_id: int, conversation_id: int):
        users = self._conversation_users.get(conversation_id)
        if users:
            users.discard(user_id)
            if not users:
                del self._conversation_users[conversation_id]

    def remove_user_from_all_conversations(self, user_id: int):
        for conv_id in list(self._conversation_users.keys()):
            users = self._conversation_users.get(conv_id)
            if users:
                users.discard(user_id)
                if not users:
                    del self._conversation_users[conv_id]

    async def broadcast_to_conversation(self, conversation_id: int, data: dict, exclude: Optional[int] = None):
        user_ids = self._conversation_users.get(conversation_id, set())
        for uid in user_ids:
            if uid == exclude:
                continue
            await self._raw_send_to_connections(uid, data)

    # ================================================================
    # 序号系统
    # ================================================================

    def _next_seq_sync(self, user_id: int, payload: dict) -> Optional[int]:
        db: Session = SessionLocal()
        try:
            from app.models.models import WSUserSeq, WSMessageLog
            row = db.query(WSUserSeq).filter(WSUserSeq.user_id == user_id).first()
            if row is None:
                row = WSUserSeq(user_id=user_id, current_seq=0)
                db.add(row)
                db.flush()
            db.execute(
                text("UPDATE ws_user_seq SET current_seq = current_seq + 1 WHERE user_id = :uid"),
                {"uid": user_id}
            )
            new_seq = db.execute(
                text("SELECT current_seq FROM ws_user_seq WHERE user_id = :uid"),
                {"uid": user_id}
            ).scalar()
            log_entry = WSMessageLog(
                user_id=user_id, seq=new_seq,
                payload=json.dumps(payload, ensure_ascii=False),
                created_at=datetime.utcnow(),
            )
            db.add(log_entry)
            db.commit()
            return new_seq
        except Exception as e:
            logger.error(f"[WS SEQ] _next_seq failed uid={user_id}: {e}")
            db.rollback()
            return None
        finally:
            db.close()

    async def _next_seq(self, user_id: int, payload: dict) -> Optional[int]:
        return await asyncio.get_event_loop().run_in_executor(None, self._next_seq_sync, user_id, payload)

    async def get_messages_after_seq(self, user_id: int, last_received_seq: int, limit: int = 200) -> list[dict]:
        def _sync():
            db: Session = SessionLocal()
            try:
                from app.models.models import WSMessageLog
                rows = (
                    db.query(WSMessageLog)
                    .filter(WSMessageLog.user_id == user_id, WSMessageLog.seq > last_received_seq)
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
                logger.error(f"[WS SYNC] failed uid={user_id}: {e}")
                return []
            finally:
                db.close()
        return await asyncio.get_event_loop().run_in_executor(None, _sync)

    async def get_current_seq(self, user_id: int) -> int:
        def _sync():
            db: Session = SessionLocal()
            try:
                from app.models.models import WSUserSeq
                row = db.query(WSUserSeq).filter(WSUserSeq.user_id == user_id).first()
                return row.current_seq if row else 0
            except Exception:
                return 0
            finally:
                db.close()
        return await asyncio.get_event_loop().run_in_executor(None, _sync)

    # ================================================================
    # ACK 去重
    # ================================================================

    async def check_and_record_dedup(self, user_id: int, client_msg_id: str) -> bool:
        """检查是否为重复 client_msg_id。首次见到时记录并返回 False，重复时返回 True。"""
        if not client_msg_id:
            return False
        def _sync():
            db: Session = SessionLocal()
            try:
                from app.models.models import WSAckDedup
                existing = db.query(WSAckDedup).filter(
                    WSAckDedup.client_msg_id == client_msg_id
                ).first()
                if existing:
                    return True
                entry = WSAckDedup(
                    client_msg_id=client_msg_id, user_id=user_id,
                    processed_at=datetime.utcnow(),
                )
                db.add(entry)
                cutoff = datetime.utcnow() - timedelta(hours=24)
                db.query(WSAckDedup).filter(WSAckDedup.processed_at < cutoff).delete()
                db.commit()
                return False
            except Exception as e:
                logger.warning(f"[WS DEDUP] failed: {e}")
                db.rollback()
                return False
            finally:
                db.close()
        return await asyncio.get_event_loop().run_in_executor(None, _sync)

    async def get_dedup_message_id(self, client_msg_id: str) -> Optional[int]:
        """查询已去重的 client_msg_id 对应的 message_id。"""
        if not client_msg_id:
            return None
        def _sync():
            db: Session = SessionLocal()
            try:
                from app.models.models import WSAckDedup
                row = db.query(WSAckDedup).filter(
                    WSAckDedup.client_msg_id == client_msg_id
                ).first()
                return row.message_id if row else None
            except Exception:
                return None
            finally:
                db.close()
        return await asyncio.get_event_loop().run_in_executor(None, _sync)

    async def update_dedup_message_id(self, client_msg_id: str, message_id: int):
        """在去重记录中补充 message_id（首次处理完成后调用）。"""
        if not client_msg_id or not message_id:
            return
        def _sync():
            db: Session = SessionLocal()
            try:
                from app.models.models import WSAckDedup
                row = db.query(WSAckDedup).filter(
                    WSAckDedup.client_msg_id == client_msg_id
                ).first()
                if row and row.message_id is None:
                    row.message_id = message_id
                    db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()
        await asyncio.get_event_loop().run_in_executor(None, _sync)


# 全局单例
ws_manager = WSManager()
