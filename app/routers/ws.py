"""
WebSocket 路由
ws://host:5000/ws?token=<jwt_token>
"""
import logging
from datetime import datetime
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import Config
from app.database import SessionLocal
from app.models.models import User, Message, Conversation, ConversationParticipant
from app.ws_manager import ws_manager

logger = logging.getLogger(__name__)
router = APIRouter()


def _verify_token(token: str) -> int | None:
    """验证 JWT token，返回 user_id 或 None"""
    try:
        payload = jwt.decode(token, Config.JWT_SECRET_KEY, algorithms=["HS256"])
        user_id = payload.get("sub")
        if user_id is None:
            return None
        return int(user_id)
    except (JWTError, ValueError, TypeError):
        return None


def _user_exists(user_id: int) -> bool:
    db: Session = SessionLocal()
    try:
        return db.query(User).filter(User.id == user_id).first() is not None
    finally:
        db.close()


@router.websocket("/")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(...)):
    """WebSocket 端点：验证 JWT 后建立长连接"""
    user_id = _verify_token(token)
    if user_id is None or not _user_exists(user_id):
        await websocket.close(code=4001, reason="Invalid or expired token")
        logger.warning(f"WS rejected: invalid token")
        return

    await websocket.accept()
    await ws_manager.connect(user_id, websocket)
    await websocket.send_json({"type": "connected", "user_id": user_id})

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg_type == "join":
                conv_id = data.get("conversation_id")
                if conv_id:
                    ws_manager.join_conversation(user_id, int(conv_id))
            elif msg_type == "leave":
                conv_id = data.get("conversation_id")
                if conv_id:
                    ws_manager.leave_conversation(user_id, int(conv_id))
            elif msg_type == "typing" or msg_type == "stop_typing":
                conv_id = data.get("conversation_id")
                if conv_id:
                    await ws_manager.broadcast_to_conversation(
                        int(conv_id),
                        {"type": msg_type, "conversation_id": int(conv_id), "sender_id": user_id},
                        exclude=user_id,
                    )
            elif msg_type == "send_message":
                await _handle_send_message(user_id, data)
            elif msg_type == "conversation_read":
                pass
            elif msg_type == "notifications_read":
                pass
            else:
                logger.debug(f"WS received unknown message from user_id={user_id}: {data}")
    except WebSocketDisconnect:
        logger.info(f"WS client disconnected: user_id={user_id}")
    except Exception as e:
        logger.warning(f"WS error for user_id={user_id}: {e}")
    finally:
        await ws_manager.disconnect(user_id)


async def _handle_send_message(sender_id: int, data: dict):
    """处理通过 WebSocket 发送的消息"""
    conv_id = data.get("conversation_id")
    content = data.get("content", "").strip()
    if not conv_id or not content:
        return

    conv_id = int(conv_id)
    message_type = data.get("message_type", "text")
    media_url = data.get("media_url")
    related_id = data.get("related_id")

    db: Session = SessionLocal()
    try:
        # 验证会话存在且发送者是参与者
        conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
        if not conv:
            return
        if conv.user1_id != sender_id and conv.user2_id != sender_id:
            return

        # 确保 ConversationParticipant 记录存在（向后兼容历史数据）
        existing = (
            db.query(ConversationParticipant)
            .filter(
                ConversationParticipant.conversation_id == conv_id,
                ConversationParticipant.user_id == sender_id,
            )
            .first()
        )
        if not existing:
            db.add(ConversationParticipant(conversation_id=conv_id, user_id=sender_id))

        # 创建消息 + 更新会话最后消息时间 → 同一个事务
        msg = Message(
            conversation_id=conv_id,
            sender_id=sender_id,
            content=content,
            message_type=message_type,
            media_url=media_url,
            related_id=related_id,
            created_at=datetime.utcnow(),
        )
        db.add(msg)
        conv.last_message_at = msg.created_at
        db.commit()
        db.refresh(msg)

        # 推送：使用 msg.to_dict() 扁平格式，同时按 receiver_id 直发（覆盖未 join 房间的在线用户）
        msg_dict = msg.to_dict()
        receiver_id = conv.user2_id if conv.user1_id == sender_id else conv.user1_id

        # 推送给接收方（含未读数）
        receiver_unread = (
            db.query(Message)
            .filter(
                Message.conversation_id == conv_id,
                Message.is_read == False,
                Message.sender_id != receiver_id,
            )
            .count()
        )
        await ws_manager.send(receiver_id, {
            "type": "new_message",
            **msg_dict,
            "unread_count": receiver_unread,
        })
        # 同步给发送方（用于其他设备同步）
        sender_unread = (
            db.query(Message)
            .filter(
                Message.conversation_id == conv_id,
                Message.is_read == False,
                Message.sender_id != sender_id,
            )
            .count()
        )
        await ws_manager.send(sender_id, {
            "type": "new_message",
            **msg_dict,
            "unread_count": sender_unread,
        })
    except Exception as e:
        logger.error(f"WS send_message failed: {e}")
        db.rollback()
        # 向发送方推送错误，让前端感知失败并回滚乐观消息
        try:
            await ws_manager.send(sender_id, {
                "type": "send_error",
                "conversation_id": conv_id,
                "error": str(e),
            })
        except Exception:
            pass
    finally:
        db.close()
