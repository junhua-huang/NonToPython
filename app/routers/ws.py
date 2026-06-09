"""
原生 WebSocket 端点 — v3.0 序号协议
路径: /ws （不带 query 参数，连接后发送 auth 消息认证）

上行消息: auth, send, ack_receive, sync, ping, join, leave, typing, stop_typing
下行消息: auth_result, message(seq+payload), ack, sync_result, pong, error
"""
from datetime import datetime
import json
import logging
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from app.core.config import Config
from app.database import SessionLocal
from app.ws_manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter()


# ================================================================
# 工具函数
# ================================================================

def _verify_token(token: str) -> Optional[int]:
    """验证 JWT Token，返回 user_id（失败返回 None）"""
    try:
        payload = jwt.decode(token, Config.JWT_SECRET_KEY, algorithms=["HS256"])
        user_id = payload.get('sub')
        if user_id is None:
            return None
        if isinstance(user_id, str):
            user_id = int(user_id)
        return user_id
    except (JWTError, ValueError, TypeError) as e:
        logger.error(f"[WS AUTH] Token 验证失败: {e}")
        return None


def _get_db_session() -> Session:
    """创建数据库会话（同步）"""
    return SessionLocal()


def _get_conversation(db: Session, user_id: int, other_id: int):
    """获取或创建两人之间的会话"""
    from app.models.models import Conversation, ConversationParticipant

    conv = (
        db.query(Conversation)
        .filter(
            ((Conversation.user1_id == user_id) & (Conversation.user2_id == other_id)) |
            ((Conversation.user1_id == other_id) & (Conversation.user2_id == user_id))
        )
        .first()
    )

    if not conv:
        conv = Conversation(user1_id=user_id, user2_id=other_id)
        db.add(conv)
        db.flush()
        p1 = ConversationParticipant(conversation_id=conv.id, user_id=user_id)
        p2 = ConversationParticipant(conversation_id=conv.id, user_id=other_id)
        db.add(p1)
        db.add(p2)
        db.flush()

    return conv


def _get_conversation_participant_ids(db: Session, conversation_id: int) -> list[int]:
    """获取会话所有参与者 ID"""
    from app.models.models import ConversationParticipant
    return [
        r.user_id
        for r in db.query(ConversationParticipant)
        .filter(ConversationParticipant.conversation_id == conversation_id)
        .all()
    ]


def _can_send_to_user(db: Session, from_user_id: int, to_user_id: int) -> bool:
    """检查发送权限（好友关系 + 隐私设置）"""
    from app.models.models import Friendship, User

    target = db.query(User).filter(User.id == to_user_id).first()
    if not target:
        return False

    if target.allow_friend_requests == 'nobody':
        return False

    if target.allow_friend_requests == 'friends':
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

    return True


def _build_session_list(db: Session, user_id: int) -> list[dict]:
    """构建会话列表数据"""
    from sqlalchemy import func, text
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
# 消息处理器
# ================================================================

async def _handle_auth(websocket: WebSocket, user_id: int) -> dict:
    """认证成功后自动触发的初始化流程"""
    from app.models.models import Conversation, ConversationParticipant, Message

    # 1. 注册连接
    await ws_manager.connect(user_id, websocket)

    # 2. 会话列表推送
    db = _get_db_session()
    try:
        sessions = _build_session_list(db, user_id)
        if sessions:
            await ws_manager.send_with_seq(user_id, "session_list", {"sessions": sessions})
    finally:
        db.close()

    # 3. 自动加入已有会话房间
    db = _get_db_session()
    try:
        convs = (
            db.query(Conversation)
            .join(ConversationParticipant)
            .filter(ConversationParticipant.user_id == user_id)
            .all()
        )
        for conv in convs:
            ws_manager.join_conversation(user_id, conv.id)
    finally:
        db.close()

    return {"type": "auth_result", "success": True, "user_id": user_id}


async def _handle_send(websocket: WebSocket, user_id: int, data: dict):
    """
    处理 send 消息：
    {type: "send", clientMsgId: "<uuid>", payload: {conversation_id?, receiver_id?, content, ...}}
    """
    from app.models.models import Message, Conversation

    client_msg_id = data.get("clientMsgId", "")
    payload = data.get("payload", {})

    # 1. 幂等检查
    if client_msg_id and ws_manager.check_and_record_dedup(user_id, client_msg_id):
        # 重复请求，直接返回已有确认（简单处理：返回错误提示）
        await ws_manager.send_raw(user_id, {
            "type": "ack",
            "clientMsgId": client_msg_id,
            "serverSeq": 0,
            "duplicate": True
        })
        return

    receiver_id = payload.get("receiver_id")
    conversation_id = payload.get("conversation_id")
    content = payload.get("content", "").strip()
    media_url = payload.get("media_url")
    related_id = payload.get("related_id")

    # 验证至少有一个目标
    if not receiver_id and not conversation_id:
        await ws_manager.send_error(user_id, client_msg_id, "receiver_id or conversation_id required")
        return

    db = _get_db_session()
    try:
        # 获取或创建会话
        if conversation_id:
            conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
            if not conv:
                await ws_manager.send_error(user_id, client_msg_id, "Conversation not found")
                return
        elif receiver_id:
            # 权限检查
            if not _can_send_to_user(db, user_id, receiver_id):
                await ws_manager.send_error(user_id, client_msg_id, "Cannot send message to this user")
                return
            conv = _get_conversation(db, user_id, receiver_id)

        # 创建消息
        msg = Message(
            conversation_id=conv.id,
            sender_id=user_id,
            content=content,
            message_type=payload.get("message_type", "text"),
            media_url=media_url,
            related_id=related_id,
            is_read=False,
        )
        db.add(msg)
        conv.last_message_at = datetime.utcnow()
        db.commit()
        db.refresh(msg)

        # 构建消息数据
        msg_data = {
            "id": msg.id,
            "conversation_id": conv.id,
            "sender_id": user_id,
            "content": msg.content,
            "message_type": msg.message_type,
            "media_url": msg.media_url,
            "related_id": msg.related_id,
            "is_read": False,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
        }

        # 2. 返回 ACK（带序号）
        # ACK 不走 send_with_seq，用 send_raw + 手动分配序号
        ack_seq = ws_manager.get_current_seq(user_id)
        await ws_manager.send_raw(user_id, {
            "type": "ack",
            "clientMsgId": client_msg_id,
            "serverSeq": ack_seq,
            "message_id": msg.id,
        })

        # 3. 推送给目标用户（走序号系统）
        participant_ids = _get_conversation_participant_ids(db, conv.id)
        for pid in participant_ids:
            if pid != user_id:
                await ws_manager.send_with_seq(pid, "new_message", {
                    "conversation_id": conv.id,
                    "data": msg_data,
                })

    except Exception as e:
        logger.error(f"[WS SEND] error for user_id={user_id}: {e}")
        db.rollback()
        await ws_manager.send_error(user_id, client_msg_id, "Message send failed")
    finally:
        db.close()


async def _handle_sync(websocket: WebSocket, user_id: int, data: dict):
    """
    处理 sync 请求：客户端发来 lastReceivedSeq，服务端返回 seq > lastReceivedSeq 的消息。
    {type: "sync", lastReceivedSeq: N}
    """
    last_seq = data.get("lastReceivedSeq", 0)
    messages = ws_manager.get_messages_after_seq(user_id, last_seq)

    await ws_manager.send_raw(user_id, {
        "type": "sync_result",
        "messages": messages,
        "count": len(messages),
    })


async def _handle_join(websocket: WebSocket, user_id: int, data: dict):
    """加入会话房间"""
    conv_id = data.get("conversation_id")
    if not conv_id:
        return

    db = _get_db_session()
    try:
        from app.models.models import ConversationParticipant
        p = (
            db.query(ConversationParticipant)
            .filter(ConversationParticipant.conversation_id == conv_id, ConversationParticipant.user_id == user_id)
            .first()
        )
        if not p:
            return
    finally:
        db.close()

    ws_manager.join_conversation(user_id, conv_id)


async def _handle_leave(websocket: WebSocket, user_id: int, data: dict):
    """离开会话房间"""
    conv_id = data.get("conversation_id")
    if conv_id:
        ws_manager.leave_conversation(user_id, conv_id)


async def _handle_typing(websocket: WebSocket, user_id: int, data: dict, is_typing: bool):
    """发送 typing / stop_typing 广播"""
    conv_id = data.get("conversation_id")
    if not conv_id:
        return

    event_type = "typing" if is_typing else "stop_typing"
    await ws_manager.broadcast_to_conversation(
        conv_id,
        {"type": "message", "seq": 0, "payload": {"event": event_type, "conversation_id": conv_id, "user_id": user_id}},
        exclude=user_id,
    )


async def _handle_ack_receive(websocket: WebSocket, user_id: int, data: dict):
    """
    客户端确认收到消息（可选，流量控制）。
    {type: "ack_receive", seq: N}
    """
    # 目前仅记录日志，后续可扩展为流控
    seq = data.get("seq")
    logger.debug(f"[WS ACK_RECEIVE] user_id={user_id} seq={seq}")


async def _handle_conversation_read(websocket: WebSocket, user_id: int, payload: dict):
    """会话标记已读（payload 通过 send 消息传入）"""
    from app.models.models import Message
    conv_id = payload.get("conversation_id")
    if not conv_id:
        return

    db = _get_db_session()
    try:
        result = (
            db.query(Message)
            .filter(Message.conversation_id == conv_id, Message.sender_id != user_id, Message.is_read == False)
            .update({"is_read": True})
        )
        db.commit()

        if result > 0:
            participant_ids = _get_conversation_participant_ids(db, conv_id)
            for pid in participant_ids:
                if pid != user_id:
                    await ws_manager.send_with_seq(pid, "conversation_read", {
                        "conversation_id": conv_id,
                        "read_by": user_id,
                    })
    finally:
        db.close()


async def _handle_notifications_read(websocket: WebSocket, user_id: int, payload: dict):
    """通知标记已读（payload 通过 send 消息传入）"""
    from app.models.models import Notification
    notif_ids = payload.get("notification_ids", [])
    if not notif_ids:
        return

    db = _get_db_session()
    try:
        db.query(Notification).filter(
            Notification.id.in_(notif_ids), Notification.user_id == user_id
        ).update({"is_read": True}, synchronize_session=False)
        db.commit()
    finally:
        db.close()


# ================================================================
# WebSocket 端点
# ================================================================

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    user_id: Optional[int] = None

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")
            logger.info(f"[WS RECV] user_id={user_id} type={msg_type} keys={list(data.keys())}")

            # ── 认证 ────────────────────────────────────────
            if msg_type == "auth":
                if user_id is not None:
                    await ws_manager.send_raw(user_id, {"type": "error", "message": "Already authenticated"})
                    continue

                token = data.get("token", "")
                uid = _verify_token(token)
                if uid is None:
                    await websocket.close(code=4001, reason="Invalid or expired token")
                    return

                # 检查用户是否存在
                db = _get_db_session()
                try:
                    from app.models.models import User
                    if not db.query(User).filter(User.id == uid).first():
                        await websocket.close(code=4001, reason="User not found")
                        return
                finally:
                    db.close()

                user_id = uid
                result = await _handle_auth(websocket, user_id)
                await ws_manager.send_raw(user_id, result)
                logger.info(f"[WS SEND] user_id={user_id} type=auth_result success=True online_count={len(ws_manager.connections)}")
                continue

            # ── 未认证状态下的所有其他消息 → 拒绝 ──────────
            if user_id is None:
                try:
                    await websocket.send_json({"type": "error", "message": "Not authenticated. Send auth first."})
                except Exception:
                    pass
                continue

            # ── 心跳 ────────────────────────────────────────
            if msg_type == "ping":
                await ws_manager.send_raw(user_id, {"type": "pong"})
                logger.info(f"[WS SEND] user_id={user_id} type=pong")
                continue

            # ── sync（断线补发） ──────────────────────────
            if msg_type == "sync":
                await _handle_sync(websocket, user_id, data)
                continue

            # ── send（发消息） ──────────────────────────────
            if msg_type == "send":
                payload = data.get("payload", {})
                payload_event = payload.get("event", "")

                # 特殊 payload event 路由
                if payload_event == "conversation_read":
                    await _handle_conversation_read(websocket, user_id, payload)
                elif payload_event == "notifications_read":
                    await _handle_notifications_read(websocket, user_id, payload)
                else:
                    await _handle_send(websocket, user_id, data)
                continue

            # ── ack_receive ──────────────────────────────────
            if msg_type == "ack_receive":
                await _handle_ack_receive(websocket, user_id, data)
                continue

            # ── 会话房间 ────────────────────────────────────
            if msg_type == "join":
                await _handle_join(websocket, user_id, data)
                continue

            if msg_type == "leave":
                await _handle_leave(websocket, user_id, data)
                continue

            # ── typing ──────────────────────────────────────
            if msg_type == "typing":
                await _handle_typing(websocket, user_id, data, is_typing=True)
                continue

            if msg_type == "stop_typing":
                await _handle_typing(websocket, user_id, data, is_typing=False)
                continue

            # ── 未知类型 ────────────────────────────────────
            await ws_manager.send_raw(user_id, {"type": "error", "message": f"Unknown message type: {msg_type}"})

    except WebSocketDisconnect:
        if user_id is not None:
            await ws_manager.disconnect(user_id)
            logger.info(f"[WS DISCONNECT] user_id={user_id} reason=client_disconnect online_count={len(ws_manager.connections)}")
    except Exception as e:
        logger.error(f"[WS ERROR] user_id={user_id} error={e}", exc_info=True)
        if user_id is not None:
            await ws_manager.disconnect(user_id)
            logger.info(f"[WS DISCONNECT] user_id={user_id} reason=error online_count={len(ws_manager.connections)}")
