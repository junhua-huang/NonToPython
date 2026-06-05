"""
WebSocket 事件处理 - FastAPI + python-socketio 版本
"""
import logging
import time
from collections import defaultdict
from sqlalchemy.orm import Session

import socketio
from fastapi import FastAPI
from app.database import SessionLocal
from app.models.models import Conversation, Message, User, Notification, Post, Comment
from app.core.config import Config
from app.services.notification_service import NotificationService
import jwt
from datetime import datetime

logger = logging.getLogger(__name__)

# ============================================================
# python-socketio AsyncServer 实例
# ============================================================
sio = socketio.AsyncServer(
    async_mode='asgi',
    cors_allowed_origins=[],
    logger=True,
    engineio_logger=False,
)

# 将 socketio 挂载为 ASGI 应用，由 main.py 挂载到 FastAPI
socketio_app = socketio.ASGIApp(sio, socketio_path='socket.io')

# ============================================================
# 在线用户管理
# ============================================================
# 存储在线用户 {user_id: sid}
online_users: dict = {}
# 匿名连接限制
MAX_ANONYMOUS_CONNECTIONS = 20
ANONYMOUS_RATE_LIMIT_WINDOW = 60   # 秒
ANONYMOUS_RATE_LIMIT_MAX = 10      # 窗口内最大连接数
anonymous_connection_count = 0
anonymous_rate_limits = defaultdict(list)  # {ip: [timestamps]}


def authenticate_socket(token: str):
    """验证 Socket 连接的 Token（使用 PyJWT，与 auth.py 一致）"""
    try:
        payload = jwt.decode(token, Config.JWT_SECRET_KEY, algorithms=["HS256"])
        user_id = payload.get('sub')
        if user_id is None:
            return None
        # 确保返回整数
        if isinstance(user_id, str):
            user_id = int(user_id)
        logger.info(f"Token decoded - User ID: {user_id} (type: {type(user_id).__name__})")
        return user_id
    except Exception as e:
        logger.error(f"Token validation error: {e}")
        return None


def get_db_session() -> Session:
    """获取数据库会话（每次请求独立）"""
    return SessionLocal()


# ============================================================
# Socket.IO 事件
# ============================================================

@sio.event
async def connect(sid, environ, auth):
    """处理客户端连接"""
    logger.debug(f"Connect event triggered - sid: {sid}")

    token = None
    # auth 字典来自客户端 connect() 时的 auth 参数
    if isinstance(auth, dict):
        token = auth.get('token')
    # 也支持从 query string 获取
    if not token and 'QUERY_STRING' in environ:
        from urllib.parse import parse_qs
        qs = parse_qs(environ['QUERY_STRING'])
        token_list = qs.get('token', [])
        if token_list:
            token = token_list[0]

    logger.debug(f"Connect attempt - Token present: {token is not None}")
    if token:
        logger.info(f"Token first 50 chars: {token[:50]}...")

    user_id = None

    if not token:
        # 匿名连接频率限制
        remote_addr = environ.get('REMOTE_ADDR', 'unknown')
        now = time.time()
        anonymous_rate_limits[remote_addr] = [
            t for t in anonymous_rate_limits[remote_addr]
            if now - t < ANONYMOUS_RATE_LIMIT_WINDOW
        ]
        if len(anonymous_rate_limits[remote_addr]) >= ANONYMOUS_RATE_LIMIT_MAX:
            logger.warning(f"Anonymous rate limit exceeded for {remote_addr}")
            return False
        anonymous_rate_limits[remote_addr].append(now)

        global anonymous_connection_count
        if anonymous_connection_count >= MAX_ANONYMOUS_CONNECTIONS:
            logger.warning(f"Max anonymous connections ({MAX_ANONYMOUS_CONNECTIONS}) reached, rejecting")
            return False
        anonymous_connection_count += 1

        logger.info(f"Anonymous connection ({anonymous_connection_count}/{MAX_ANONYMOUS_CONNECTIONS}) from {remote_addr}")
        online_users[f'anonymous_{sid}'] = sid
        await sio.enter_room(sid, f'anonymous_{sid}')
        await sio.emit('connected', {
            'message': 'Connected as anonymous user',
            'user_id': None
        }, to=sid)
        return True

    # 验证 token
    try:
        user_id = authenticate_socket(token)
        logger.info(f"Token validation result - User ID: {user_id}")
    except Exception as e:
        logger.error(f"Token validation exception: {e}")
        user_id = None

    if not user_id:
        logger.warning("Invalid token, rejecting connection")
        return False

    # 记录在线用户
    online_users[user_id] = sid

    # 获取用户详细信息
    db = get_db_session()
    try:
        user = db.query(User).filter(User.id == user_id).first()
    finally:
        db.close()

    username = user.username if user else 'Unknown'
    email = user.email if user else 'Unknown'

    remote_addr = environ.get('REMOTE_ADDR', 'Unknown')

    logger.info(
        f"WebSocket Connected - User: {user_id} ({username}), "
        f"SID: {sid}, Remote: {remote_addr}, Online: {len(online_users)}"
    )

    # 加入个人房间
    await sio.enter_room(sid, f'user_{user_id}')

    # 通知其他用户该用户上线
    await sio.emit('user_status', {
        'user_id': user_id,
        'status': 'online'
    }, skip_sid=sid)

    await sio.emit('connected', {
        'message': 'Connected successfully',
        'user_id': user_id
    }, to=sid)


@sio.event
async def disconnect(sid):
    """处理客户端断开连接"""
    try:
        # 查找并移除离线用户
        offline_user_id = None
        for uid, user_sid in list(online_users.items()):
            if user_sid == sid:
                offline_user_id = uid
                del online_users[uid]
                break

        if offline_user_id:
            remote_addr = 'unknown'
            logger.info(
                f"WebSocket Disconnected - User: {offline_user_id}, "
                f"SID: {sid}, Remote: {remote_addr}, Online left: {len(online_users)}"
            )

            try:
                await sio.leave_room(sid, f'user_{offline_user_id}')
            except Exception as e:
                logger.warning(f"Leave room failed: {e}")

            # 通知其他用户该用户离线
            try:
                await sio.emit('user_status', {
                    'user_id': offline_user_id,
                    'status': 'offline'
                }, skip_sid=sid)
            except Exception as e:
                logger.debug(f"Broadcast offline failed (ignorable): {e}")
    except Exception as e:
        logger.debug(f"Error handling disconnect (ignorable): {e}")


@sio.event
async def send_message(sid, data):
    """处理发送消息"""
    try:
        token = data.get('token')
        if not token:
            await sio.emit('error', {'message': 'Authentication required'}, to=sid)
            return

        sender_id = authenticate_socket(token)
        if not sender_id:
            await sio.emit('error', {'message': 'Invalid token'}, to=sid)
            return

        receiver_id = data.get('receiver_id')
        content = data.get('content', '').strip()
        message_type = data.get('message_type', 'text')
        media_url = data.get('media_url')
        related_id = data.get('related_id')

        if not receiver_id or not content:
            await sio.emit('error', {'message': 'Receiver ID and content are required'}, to=sid)
            return

        if sender_id == receiver_id:
            await sio.emit('error', {'message': 'Cannot send message to yourself'}, to=sid)
            return

        db = get_db_session()
        try:
            receiver = db.query(User).filter(User.id == receiver_id).first()
            if not receiver:
                await sio.emit('error', {'message': 'Receiver not found'}, to=sid)
                return

            # 验证帖子/评论卡片的相关对象
            if message_type == 'post' and related_id:
                post = db.query(Post).filter(Post.id == related_id).first()
                if not post:
                    await sio.emit('error', {'message': 'Post not found'}, to=sid)
                    return
            elif message_type == 'comment' and related_id:
                comment = db.query(Comment).filter(Comment.id == related_id).first()
                if not comment:
                    await sio.emit('error', {'message': 'Comment not found'}, to=sid)
                    return

            # 获取或创建会话
            conversation = get_or_create_conversation(db, sender_id, receiver_id)

            # 创建消息
            message = Message(
                conversation_id=conversation.id,
                sender_id=sender_id,
                content=content,
                message_type=message_type,
                media_url=media_url,
                related_id=related_id,
                is_read=False,
            )
            db.add(message)
            db.commit()

            # 更新会话的最后消息时间
            conversation.last_message_at = datetime.utcnow()
            conversation.updated_at = datetime.utcnow()
            db.commit()

            message_data = message.to_dict()

            # 发送新消息通知
            NotificationService.notify_message(
                receiver_id,
                sender_id,
                content,
                conversation.id
            )

            # 发送给接收者
            receiver_sid = online_users.get(receiver_id)
            if receiver_sid:
                await sio.emit('receive_message', message_data, to=receiver_sid)

            # 发送给自己（确认）
            await sio.emit('message_sent', message_data, to=sid)

            logger.info(
                f"New Message - Sender: {sender_id}, Receiver: {receiver_id}, "
                f"Type: {message_type}, Conv: {conversation.id}, MsgID: {message.id}"
            )
        finally:
            db.close()

    except Exception as e:
        logger.error(f"Error sending message: {e}")
        await sio.emit('error', {'message': f'Failed to send message: {str(e)}'}, to=sid)


@sio.event
async def mark_as_read(sid, data):
    """标记消息为已读"""
    try:
        token = data.get('token')
        if not token:
            return

        user_id = authenticate_socket(token)
        if not user_id:
            return

        message_id = data.get('message_id')
        db = get_db_session()
        try:
            message = db.query(Message).filter(Message.id == message_id).first()
            if message and (message.conversation.user1_id == user_id or message.conversation.user2_id == user_id):
                message.is_read = True
                db.commit()

                # 通知发送者消息已读
                sender_sid = online_users.get(message.sender_id)
                if sender_sid:
                    await sio.emit('message_read', {
                        'message_id': message_id,
                        'conversation_id': message.conversation_id
                    }, to=sender_sid)
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Error marking message as read: {e}")


@sio.event
async def typing(sid, data):
    """处理正在输入状态"""
    try:
        token = data.get('token')
        if not token:
            return

        sender_id = authenticate_socket(token)
        if not sender_id:
            return

        receiver_id = data.get('receiver_id')
        is_typing = data.get('is_typing', False)

        receiver_sid = online_users.get(receiver_id)
        if receiver_sid:
            await sio.emit('user_typing', {
                'user_id': sender_id,
                'is_typing': is_typing
            }, to=receiver_sid)
    except Exception as e:
        logger.error(f"Error handling typing: {e}")


@sio.event
async def mark_notification_read(sid, data):
    """标记通知为已读（通过 WebSocket）"""
    try:
        token = data.get('token')
        if not token:
            return

        user_id = authenticate_socket(token)
        if not user_id:
            return

        notification_id = data.get('notification_id')
        db = get_db_session()
        try:
            notification = db.query(Notification).filter(Notification.id == notification_id).first()
            if notification and notification.user_id == user_id:
                notification.is_read = True
                db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Error marking notification as read: {e}")


# ============================================================
# 辅助函数
# ============================================================

def get_or_create_conversation(db: Session, user1_id: int, user2_id: int):
    """获取或创建两人之间的会话"""
    if user1_id > user2_id:
        user1_id, user2_id = user2_id, user1_id

    conversation = db.query(Conversation).filter(
        Conversation.user1_id == user1_id,
        Conversation.user2_id == user2_id,
    ).first()

    if not conversation:
        conversation = Conversation(
            user1_id=user1_id,
            user2_id=user2_id,
        )
        db.add(conversation)
        db.flush()

    return conversation


def is_user_online(user_id):
    """检查用户是否在线"""
    return user_id in online_users


def get_online_users_list():
    """获取所有在线用户 ID 列表"""
    return [uid for uid in online_users.keys() if isinstance(uid, int)]


# ============================================================
# FastAPI 挂载函数
# ============================================================

def mount_socketio(app: FastAPI, path: str = '/ws'):
    """将 Socket.IO ASGI 应用挂载到 FastAPI"""
    app.mount(path, socketio_app)
