"""
WebSocket 端点 — 即时通讯协议

协议规范: 统一 envelope {type, request_id?, payload}
- auth 消息鉴权（message-based，不再 URL-based）
- send_message 发送聊天消息
- send_event 发送状态事件（已读等）
- 所有需持久化的推送统一走 message 帧（带 seq）

连接后先收到 auth 消息鉴权，通过后自动推送 session_list + auth_result。
"""

from datetime import datetime
import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.ws_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter()

AUTH_TIMEOUT = 15  # 连接后 15s 内必须发 auth，否则断开


# ================================================================
# 工具函数
# ================================================================

def _get_db_session() -> Session:
    return SessionLocal()


def _get_conversation(db: Session, user_id: int, other_id: int):
    from app.models.models import Conversation, ConversationParticipant

    uid1, uid2 = min(user_id, other_id), max(user_id, other_id)
    conv = (
        db.query(Conversation)
        .filter(Conversation.user1_id == uid1, Conversation.user2_id == uid2)
        .first()
    )

    if not conv:
        conv = Conversation(user1_id=uid1, user2_id=uid2)
        db.add(conv)
        db.flush()
        p1 = ConversationParticipant(conversation_id=conv.id, user_id=user_id)
        p2 = ConversationParticipant(conversation_id=conv.id, user_id=other_id)
        db.add(p1)
        db.add(p2)
        db.flush()

    return conv


def _get_conversation_participant_ids(db: Session, conversation_id: int) -> list[int]:
    from app.models.models import ConversationParticipant
    return [
        r.user_id
        for r in db.query(ConversationParticipant)
        .filter(ConversationParticipant.conversation_id == conversation_id)
        .all()
    ]


def _can_send_to_user(db: Session, from_user_id: int, to_user_id: int) -> bool:
    from app.models.models import Friendship, User

    target = db.query(User).filter(User.id == to_user_id).first()
    if not target:
        return False

    blocked_by_to = ws_manager.get_blocked_user_ids(to_user_id)
    if from_user_id in blocked_by_to:
        return False
    blocked_by_from = ws_manager.get_blocked_user_ids(from_user_id)
    if to_user_id in blocked_by_from:
        return False

    is_friend = (
        db.query(Friendship)
        .filter(
            ((Friendship.sender_id == from_user_id) & (Friendship.receiver_id == to_user_id)) |
            ((Friendship.sender_id == to_user_id) & (Friendship.receiver_id == from_user_id)),
            Friendship.status == 'accepted'
        )
        .first()
    )
    return is_friend is not None


def _build_session_list(db: Session, user_id: int) -> list[dict]:
    from sqlalchemy import func
    from app.models.models import Conversation, ConversationParticipant, Message, User

    conversations = (
        db.query(Conversation)
        .join(ConversationParticipant)
        .filter(ConversationParticipant.user_id == user_id)
        .order_by(Conversation.last_message_at.desc())
        .limit(50)
        .all()
    )

    if not conversations:
        return []

    blocked_ids = ws_manager.get_blocked_user_ids(user_id)
    if blocked_ids:
        conversations = [
            c for c in conversations
            if (c.user1_id if c.user2_id == user_id else c.user2_id) not in blocked_ids
        ]
        if not conversations:
            return []

    conv_ids = [c.id for c in conversations]

    unread_counts = dict(
        db.query(Message.conversation_id, func.count(Message.id))
        .filter(Message.conversation_id.in_(conv_ids), Message.is_read == False, Message.sender_id != user_id)
        .group_by(Message.conversation_id)
        .all()
    )

    last_messages = {}
    for conv_id in conv_ids:
        msg = db.query(Message).filter(Message.conversation_id == conv_id).order_by(Message.created_at.desc()).first()
        if msg:
            last_messages[conv_id] = {
                'id': msg.id,
                'conversation_id': msg.conversation_id,
                'sender_id': msg.sender_id,
                'content': msg.content,
                'message_type': msg.message_type,
                'media_url': msg.media_url,
                'related_id': msg.related_id,
                'is_read': msg.is_read,
                'created_at': msg.created_at.isoformat() if msg.created_at else None,
            }

    all_partner_ids = set()
    for conv in conversations:
        all_partner_ids.add(conv.user1_id if conv.user2_id == user_id else conv.user2_id)

    partners = {}
    if all_partner_ids:
        for u in db.query(User).filter(User.id.in_(all_partner_ids)).all():
            partners[u.id] = u.to_dict()

    session_list = []
    for conv in conversations:
        partner_id = conv.user1_id if conv.user2_id == user_id else conv.user2_id
        if partner_id not in partners:
            continue
        session_list.append({
            'id': conv.id,
            'conversation_id': conv.id,
            'partner_id': partner_id,
            'partner': partners.get(partner_id),
            'last_message': last_messages.get(conv.id),
            'unread_count': unread_counts.get(conv.id, 0),
            'created_at': conv.created_at.isoformat() if conv.created_at else None,
            'updated_at': conv.updated_at.isoformat() if conv.updated_at else None,
        })

    return session_list


# ================================================================
# 响应构造器
# ================================================================

def _make_response(resp_type: str, request_id: str = None, **payload_fields) -> dict:
    """构造统一下行 envelope: {type, request_id?, payload:{...}}"""
    msg: dict = {"type": resp_type, "payload": payload_fields or {}}
    if request_id:
        msg["request_id"] = request_id
    return msg


def _get_payload(data: dict) -> dict:
    """兼容旧协议与新协议：优先读取 payload，否则读取平铺字段。"""
    payload = data.get("payload")
    if isinstance(payload, dict):
        return payload
    return {k: v for k, v in data.items() if k not in {"type", "request_id"}}


# ================================================================
# 鉴权 & 初始化
# ================================================================

async def _do_auth_init(websocket: WebSocket, user_id: int, request_id: str = None) -> tuple[dict, str]:
    """鉴权通过后的初始化流程：注册连接 + 推会话列表 + 加入房间 + 通知好友。返回 (auth_result_dict, conn_id)"""
    from app.models.models import Conversation, ConversationParticipant

    conn_id = await ws_manager.connect(user_id, websocket)
    loop = asyncio.get_event_loop()

    # ── 获取好友列表（后续多处使用） ──
    def _get_friend_ids():
        db = _get_db_session()
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

    # ── 会话列表 ──
    sessions = ws_manager.get_cached_sessions(user_id)
    if sessions is not None:
        logger.info(f"[WS AUTH] uid={user_id} sessions=cache count={len(sessions)}")
    else:
        def _load():
            db = _get_db_session()
            try:
                return _build_session_list(db, user_id)
            finally:
                db.close()
        sessions = await loop.run_in_executor(None, _load)
        ws_manager.set_cached_sessions(user_id, sessions)
        logger.info(f"[WS AUTH] uid={user_id} sessions=db count={len(sessions)}")

    if sessions:
        await ws_manager.send_with_seq(user_id, "session_list", {"sessions": sessions})

    # ── 通知当前用户哪些好友已在线 ──
    # （friend_online 事件只会通知别人"你上线了"，但你自己看不到之前已在线的好友）
    online_friend_ids = [fid for fid in friend_ids if ws_manager.is_connected(fid)]
    if online_friend_ids:
        await ws_manager.send_with_seq(user_id, "online_friends", {"user_ids": online_friend_ids})
        logger.info(f"[WS AUTH] uid={user_id} online_friends={online_friend_ids}")

    # ── 自动加入会话房间 ──
    conv_ids = ws_manager.get_cached_conv_ids(user_id)
    if conv_ids is not None:
        logger.debug(f"[WS AUTH] uid={user_id} conv_ids=cache count={len(conv_ids)}")
    else:
        def _load_convs():
            db = _get_db_session()
            try:
                return [
                    c.id for c in
                    db.query(Conversation)
                    .join(ConversationParticipant)
                    .filter(ConversationParticipant.user_id == user_id)
                    .all()
                ]
            finally:
                db.close()
        conv_ids = await loop.run_in_executor(None, _load_convs)
        ws_manager.set_cached_conv_ids(user_id, conv_ids)

    for cid in conv_ids:
        ws_manager.join_conversation(user_id, cid)

    # ── 通知在线好友该用户已上线 ──
    for fid in online_friend_ids:
        # online_friend_ids 就是当前在线的好友，直接推送 friend_online
        pass  # 已通过上方的 online_friends 推送，不需要重复
    for fid in friend_ids:
        if ws_manager.is_connected(fid):
            await ws_manager.send_with_seq(fid, "friend_online", {"user_id": user_id})

    return _make_response("auth_result", request_id, success=True, user_id=user_id), conn_id


async def _notify_friends_online(websocket: WebSocket, user_id: int):
    loop = asyncio.get_event_loop()

    def _get_friend_ids():
        db = _get_db_session()
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
    for fid in friend_ids:
        if ws_manager.is_connected(fid):
            await ws_manager.send_with_seq(fid, "friend_online", {"user_id": user_id})


# ================================================================
# 业务处理器
# ================================================================

async def _handle_send_message(websocket: WebSocket, user_id: int, data: dict):
    """处理 send_message：发送聊天消息"""
    from app.models.models import Message, Conversation

    request_id = data.get("request_id", "")
    payload = _get_payload(data)

    client_msg_id = payload.get("client_msg_id", "")
    conversation_id = payload.get("conversation_id")
    receiver_id = payload.get("receiver_id")
    content = payload.get("content", "").strip()
    media_url = payload.get("media_url")
    related_id = payload.get("related_id")
    message_type = payload.get("message_type", "text")
    quote_message_id = payload.get("quote_message_id")
    quote_preview = payload.get("quote_preview")

    # 1. 幂等
    if client_msg_id and await ws_manager.check_and_record_dedup(user_id, client_msg_id):
        # 尝试获取首次处理时的 message_id，以便重复 ACK 也能携带
        dedup_msg_id = await ws_manager.get_dedup_message_id(client_msg_id)
        await ws_manager.send_raw(user_id, _make_response("ack", request_id,
            client_msg_id=client_msg_id, server_seq=0, message_id=dedup_msg_id,
            status=200, msg="duplicate"))
        if client_msg_id:
            await ws_manager.send_raw(user_id, {
                "type": "ack",
                "clientMsgId": client_msg_id,
                "client_msg_id": client_msg_id,
                "message_id": dedup_msg_id,
                "server_seq": 0,
            })
        return

    if not conversation_id and not receiver_id:
        await _send_error(user_id, request_id, 400, "conversation_id or receiver_id required")
        return

    # 2. 持久化
    def _persist():
        db = _get_db_session()
        try:
            if conversation_id:
                conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
                if not conv:
                    return {"error": "Conversation not found"}
                participant_ids = _get_conversation_participant_ids(db, conversation_id)
                if user_id not in participant_ids:
                    return {"error": "Not a conversation participant"}
            elif receiver_id:
                if not _can_send_to_user(db, user_id, receiver_id):
                    return {"error": "Cannot send message to this user"}
                conv = _get_conversation(db, user_id, receiver_id)

            msg = Message(
                conversation_id=conv.id,
                sender_id=user_id,
                content=content,
                message_type=message_type,
                media_url=media_url,
                related_id=related_id,
                quote_message_id=quote_message_id,
                quote_preview=quote_preview,
                is_read=False,
            )
            db.add(msg)
            conv.last_message_at = datetime.utcnow()
            db.commit()
            db.refresh(msg)

            participant_ids = _get_conversation_participant_ids(db, conv.id)
            unread_counts = {}
            for pid in participant_ids:
                unread_counts[pid] = (
                    db.query(Message)
                    .filter(
                        Message.conversation_id == conv.id,
                        Message.is_read == False,
                        Message.sender_id != pid,
                    )
                    .count()
                )

            return {
                "conv_id": conv.id,
                "msg": {
                    "id": msg.id,
                    "conversation_id": conv.id,
                    "sender_id": user_id,
                    "content": msg.content,
                    "message_type": msg.message_type,
                    "media_url": msg.media_url,
                    "related_id": msg.related_id,
                    "quote_message_id": msg.quote_message_id,
                    "quote_preview": msg.quote_preview,
                    "is_read": False,
                    "created_at": msg.created_at.isoformat() if msg.created_at else None,
                },
                "participant_ids": participant_ids,
                "unread_counts": unread_counts,
            }
        except Exception as e:
            logger.error(f"[WS SEND] uid={user_id} persist_error: {e}")
            db.rollback()
            return {"error": "Message send failed"}
        finally:
            db.close()

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _persist)

    if isinstance(result, dict) and "error" in result:
        await _send_error(user_id, request_id, 500, result["error"])
        return

    # 3. ACK
    ack_seq = await ws_manager.get_current_seq(user_id)
    msg_id = result["msg"]["id"]
    await ws_manager.send_raw(user_id, _make_response("ack", request_id,
        client_msg_id=client_msg_id, server_seq=ack_seq,
        message_id=msg_id, status=200, msg="success"))

    if client_msg_id:
        await ws_manager.send_raw(user_id, {
            "type": "ack",
            "clientMsgId": client_msg_id,
            "client_msg_id": client_msg_id,
            "message_id": msg_id,
            "server_seq": ack_seq,
        })
        # 记录 message_id 到 dedup 表，后续重复请求可回传
        await ws_manager.update_dedup_message_id(client_msg_id, msg_id)

    # 4. 推送接收方（异步入队）
    push_items = []
    for pid in result["participant_ids"]:
        if pid != user_id:
            push_items.append((pid, "new_message", {
                **result["msg"],
                "unread_count": result.get("unread_counts", {}).get(pid),
            }))
    if push_items:
        ws_manager.enqueue_push_batch(push_items)

    # 同步给发送方自己的其它设备，并让当前设备通过同一事件刷新本地服务端字段
    await ws_manager.send_with_seq(user_id, "new_message", {
        **result["msg"],
        "client_msg_id": client_msg_id,
    })

    # #region agent log
    try:
        from app.debug_agent_log import agent_log
        agent_log("ws.py:_handle_send_message", "message_pushed", {
            "sender_id": user_id,
            "conv_id": result["conv_id"],
            "msg_id": result["msg"]["id"],
            "push_targets": [p for p in result["participant_ids"] if p != user_id],
            "targets_online": [p for p in result["participant_ids"] if p != user_id and ws_manager.is_connected(p)],
        }, "D")
    except Exception:
        pass
    # #endregion

    # 离线用户写入通知中心（在线用户已通过 WS 实时推送）
    offline_targets = [
        p for p in result["participant_ids"]
        if p != user_id and not ws_manager.is_connected(p)
    ]
    if offline_targets:
        from app.services.notification_service import NotificationService
        content = result["msg"].get("content") or ""
        for pid in offline_targets:
            NotificationService.notify_message(pid, user_id, content, result["conv_id"])

    # 5. 失效缓存
    ws_manager.invalidate_participant_caches(result["participant_ids"])


async def _handle_send_event(websocket: WebSocket, user_id: int, data: dict):
    """处理 send_event：已读、通知已读等状态事件"""
    request_id = data.get("request_id", "")
    payload = _get_payload(data)
    if not payload.get("event") and data.get("event"):
        payload = {**payload, "event": data["event"]}
    if not payload.get("conversation_id") and data.get("conversation_id"):
        payload = {**payload, "conversation_id": data["conversation_id"]}
    event = payload.get("event", "")

    if event == "conversation_read":
        await _handle_conversation_read(websocket, user_id, payload)
    elif event == "notifications_read":
        await _handle_notifications_read(websocket, user_id, payload)
    else:
        await _send_error(user_id, request_id, 400, f"Unknown event: {event}")


async def _handle_recall_message(websocket: WebSocket, user_id: int, data: dict):
    """处理 recall_message：撤回聊天消息"""
    from app.models.models import Message, Conversation

    request_id = data.get("request_id", "")
    payload = _get_payload(data)
    message_id = payload.get("message_id")

    if not message_id:
        await _send_error(user_id, request_id, 400, "message_id required")
        return

    def _do_recall():
        db = _get_db_session()
        try:
            msg = db.query(Message).filter(Message.id == message_id).first()
            if not msg:
                return {"error": "Message not found"}
            if msg.sender_id != user_id:
                return {"error": "Cannot recall other's message"}
            if msg.is_recalled:
                return {"error": "Message already recalled"}

            # 检查时间限制（2分钟内可撤回）
            from datetime import timedelta
            if msg.created_at and datetime.utcnow() - msg.created_at > timedelta(minutes=2):
                return {"error": "Recall time limit exceeded"}

            msg.is_recalled = True
            msg.recalled_at = datetime.utcnow()
            db.commit()
            db.refresh(msg)

            conv = db.query(Conversation).filter(Conversation.id == msg.conversation_id).first()
            participant_ids = _get_conversation_participant_ids(db, msg.conversation_id) if conv else []

            return {
                "msg_id": msg.id,
                "conv_id": msg.conversation_id,
                "is_recalled": True,
                "recalled_at": msg.recalled_at.isoformat() if msg.recalled_at else None,
                "participant_ids": participant_ids,
            }
        except Exception as e:
            logger.error(f"[WS RECALL] uid={user_id} error: {e}")
            db.rollback()
            return {"error": "Recall failed"}
        finally:
            db.close()

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _do_recall)

    if isinstance(result, dict) and "error" in result:
        await _send_error(user_id, request_id, 400, result["error"])
        return

    # ACK 给撤回者
    await ws_manager.send_raw(user_id, _make_response("ack", request_id,
        message_id=result["msg_id"], status=200, msg="recalled"))

    # 推送 message_recalled 事件给所有参与者
    recall_data = {
        "message_id": result["msg_id"],
        "conversation_id": result["conv_id"],
        "is_recalled": True,
        "recalled_at": result["recalled_at"],
    }
    for pid in result["participant_ids"]:
        await ws_manager.send_with_seq(pid, "message_recalled", recall_data)

    # 失效缓存
    ws_manager.invalidate_participant_caches(result["participant_ids"])


async def _handle_conversation_read(websocket: WebSocket, user_id: int, payload: dict):
    """会话标记已读"""
    from app.models.models import Message

    conv_id = payload.get("conversation_id")
    if not conv_id:
        return

    def _mark():
        db = _get_db_session()
        try:
            result = (
                db.query(Message)
                .filter(Message.conversation_id == conv_id, Message.sender_id != user_id, Message.is_read == False)
                .update({"is_read": True})
            )
            db.commit()
            if result > 0:
                return _get_conversation_participant_ids(db, conv_id)
            return []
        finally:
            db.close()

    loop = asyncio.get_event_loop()
    participant_ids = await loop.run_in_executor(None, _mark)

    for pid in participant_ids:
        if pid != user_id:
            await ws_manager.send_with_seq(pid, "conversation_read", {
                "conversation_id": conv_id,
                "read_by": user_id,
            })

    ws_manager.invalidate_participant_caches(participant_ids)


async def _handle_notifications_read(websocket: WebSocket, user_id: int, payload: dict):
    """通知标记已读"""
    from app.models.models import Notification

    notif_ids = payload.get("notification_ids", [])
    mark_all = payload.get("all") is True or payload.get("mark_all") is True

    def _mark():
        db = _get_db_session()
        try:
            query = db.query(Notification).filter(Notification.user_id == user_id)
            if notif_ids:
                query = query.filter(Notification.id.in_(notif_ids))
            elif not mark_all:
                return None
            query.update({"is_read": True}, synchronize_session=False)
            db.commit()
            unread_count = db.query(Notification).filter(
                Notification.user_id == user_id,
                Notification.is_read == False,
            ).count()
            return unread_count
        finally:
            db.close()

    loop = asyncio.get_event_loop()
    unread_count = await loop.run_in_executor(None, _mark)
    if unread_count is not None:
        await ws_manager.send_with_seq(user_id, "notifications_read", {
            "notification_ids": notif_ids,
            "unread_count": unread_count,
        })


async def _handle_sync(websocket: WebSocket, user_id: int, data: dict):
    """离线同步：payload.last_received_seq"""
    request_id = data.get("request_id", "")
    payload = _get_payload(data)
    last_seq = payload.get("last_received_seq", 0)
    limit = payload.get("limit", 200)

    messages = await ws_manager.get_messages_after_seq(user_id, last_seq, limit)
    current_max = await ws_manager.get_current_seq(user_id)

    await ws_manager.send_raw(user_id, _make_response("sync_result", request_id,
        last_received_seq=last_seq,
        current_max_seq=current_max,
        has_more=len(messages) >= limit,
        list=messages,
        count=len(messages),
    ))


async def _handle_ack_receive(websocket: WebSocket, user_id: int, data: dict):
    """累计确认：payload.seq"""
    payload = _get_payload(data)
    seq = payload.get("seq")
    logger.debug(f"[WS ACK_RX] uid={user_id} seq={seq}")


async def _handle_join(websocket: WebSocket, user_id: int, data: dict):
    """加入会话房间"""
    payload = _get_payload(data)
    conv_id = payload.get("conversation_id") or data.get("conversation_id")
    if not conv_id:
        return

    def _check():
        db = _get_db_session()
        try:
            from app.models.models import ConversationParticipant
            p = (
                db.query(ConversationParticipant)
                .filter(ConversationParticipant.conversation_id == conv_id,
                        ConversationParticipant.user_id == user_id)
                .first()
            )
            return p is not None
        finally:
            db.close()

    if await asyncio.get_event_loop().run_in_executor(None, _check):
        ws_manager.join_conversation(user_id, conv_id)


async def _handle_leave(websocket: WebSocket, user_id: int, data: dict):
    """离开会话房间"""
    payload = _get_payload(data)
    conv_id = payload.get("conversation_id") or data.get("conversation_id")
    if conv_id:
        ws_manager.leave_conversation(user_id, conv_id)


async def _handle_typing(websocket: WebSocket, user_id: int, data: dict, is_typing: bool):
    """输入状态广播（瞬时信令）"""
    payload = _get_payload(data)
    conv_id = payload.get("conversation_id") or data.get("conversation_id")
    if not conv_id:
        return

    event_type = "typing" if is_typing else "stop_typing"
    await ws_manager.broadcast_to_conversation(
        conv_id,
        {"type": "message", "seq": 0, "payload": {
            "event": event_type,
            "data": {"conversation_id": conv_id, "user_id": user_id}
        }},
        exclude=user_id,
    )


async def _send_error(user_id: int, request_id: str, code: int, msg: str):
    """发送 error 帧（仅在 async 上下文中调用）"""
    await ws_manager.send_raw(user_id, _make_response("error", request_id, code=code, msg=msg))


# ================================================================
# WebSocket 端点
# ================================================================

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    协议流程:
    1. 客户端连上 → 直接 accept
    2. 客户端发 {type:"auth", payload:{token:"..."}}
    3. 服务端验证 → 推 session_list → 推 auth_result
    4. 进入正常收发
    """
    headers = dict(websocket.headers)
    print(f"[WS-DEBUG] incoming: headers={headers}, client={websocket.client}")
    await websocket.accept()
    user_id = None
    conn_id = None

    try:
        # ── 阶段 1: 等待 auth ──
        try:
            data = await asyncio.wait_for(websocket.receive_json(), timeout=AUTH_TIMEOUT)
        except asyncio.TimeoutError:
            await websocket.close(code=4001, reason="auth_timeout")
            return

        msg_type = data.get("type", "")
        if msg_type != "auth":
            await websocket.close(code=4001, reason="auth_required")
            return

        token = data.get("payload", {}).get("token", "")
        request_id = data.get("request_id", "")

        if not token:
            await websocket.send_json(_make_response("auth_result", request_id,
                success=False, code=4001, msg="token required"))
            await websocket.close(code=4001, reason="token required")
            return

        from app.core.auth_core import verify_token, AuthError
        try:
            user = verify_token(token)
        except AuthError as e:
            await websocket.send_json(_make_response("auth_result", request_id,
                success=False, code=4001, msg=e.message))
            await websocket.close(code=4001, reason=e.message)
            return

        user_id = user.id
        logger.info(f"[WS AUTH] uid={user_id} token_ok")

        # ── 阶段 2: 初始化 ──
        result, conn_id = await _do_auth_init(websocket, user_id, request_id)
        await ws_manager.send_raw(user_id, result)
        logger.info(f"[WS AUTH] uid={user_id} cid={conn_id[:8]} ok online={ws_manager.online_count}")

        # ── 阶段 3: 正常收发 ──
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")
            logger.debug(f"[WS RECV] uid={user_id} {msg_type} {json.dumps(data, ensure_ascii=False)}")

            if msg_type == "ping":
                ws_manager.heartbeat(conn_id)
                request_id_ping = data.get("request_id", "")
                await ws_manager.send_raw(user_id, _make_response("pong", request_id_ping))
                continue

            if msg_type == "sync":
                await _handle_sync(websocket, user_id, data)
                continue

            if msg_type == "send_message":
                await _handle_send_message(websocket, user_id, data)
                continue

            if msg_type == "send_event":
                await _handle_send_event(websocket, user_id, data)
                continue

            if msg_type == "ack_receive":
                await _handle_ack_receive(websocket, user_id, data)
                continue

            if msg_type == "join":
                await _handle_join(websocket, user_id, data)
                continue

            if msg_type == "leave":
                await _handle_leave(websocket, user_id, data)
                continue

            if msg_type == "typing":
                await _handle_typing(websocket, user_id, data, is_typing=True)
                continue

            if msg_type == "stop_typing":
                await _handle_typing(websocket, user_id, data, is_typing=False)
                continue

            if msg_type == "recall_message":
                await _handle_recall_message(websocket, user_id, data)
                continue

            # 未知类型
            request_id_unk = data.get("request_id", "")
            await ws_manager.send_raw(user_id, _make_response("error", request_id_unk,
                code=400, msg=f"Unknown message type: {msg_type}"))

    except WebSocketDisconnect:
        if user_id is not None:
            await ws_manager.disconnect(user_id, conn_id)
            logger.info(f"[WS -] uid={user_id} cid={conn_id[:8] if conn_id else '?'} client_close")
    except RuntimeError:
        if user_id is not None:
            await ws_manager.disconnect(user_id, conn_id)
            logger.info(f"[WS -] uid={user_id} cid={conn_id[:8] if conn_id else '?'} kicked_by_dup")
    except Exception as e:
        logger.error(f"[WS ERROR] uid={user_id} cid={conn_id[:8] if conn_id else '?'} {e}", exc_info=True)
        if user_id is not None and conn_id:
            await ws_manager.disconnect(user_id, conn_id)
