"""
聊天路由 - FastAPI 重构版
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import User, Conversation, Message, ConversationParticipant, Friendship
from app.models.community import CommunityMember
from app.ws_manager import ws_manager
from app.services.message_type_service import normalize_user_message_payload

logger = logging.getLogger(__name__)
router = APIRouter()


def _should_create_message_notification(db: Session, user_id: int) -> bool:
    """后台/离线用户需要通知中心记录和系统推送入口。"""
    if not ws_manager.is_connected(user_id):
        return True
    from app.services.push_service import PushService
    return PushService.has_push_target(db, user_id)


# ============================================================
# 会话首屏列表 — WS 也推此结构
# ============================================================

@router.get("/sessions")
def get_sessions(
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """返回当前用户所有会话列表（首屏数据，与 WS session_list 同构）"""
    from sqlalchemy import func, and_, case

    offset = (page - 1) * per_page
    other_user_expr = case(
        (Conversation.user1_id == user.id, Conversation.user2_id),
        else_=Conversation.user1_id,
    )
    blocked_ids = ws_manager.get_blocked_user_ids(user.id)
    direct_membership = (Conversation.user1_id == user.id) | (Conversation.user2_id == user.id)
    community_membership = (
        (Conversation.type == 'community')
        & (CommunityMember.user_id == user.id)
        & (CommunityMember.status == 'active')
    )
    base_query = db.query(Conversation).outerjoin(
        CommunityMember,
        CommunityMember.community_id == Conversation.community_id,
    ).filter(direct_membership | community_membership)
    if blocked_ids:
        base_query = base_query.filter(
            (Conversation.type == 'community') | ~other_user_expr.in_(blocked_ids)
        )
    base_query = base_query.order_by(
        func.coalesce(Conversation.last_message_at, Conversation.created_at).desc()
    )
    total = base_query.count()
    conversations = base_query.offset(offset).limit(per_page).all()

    if not conversations:
        return {"sessions": [], "total": total, "page": page, "per_page": per_page}

    conv_ids = [c.id for c in conversations]

    # 获取每个会话的最后一条消息
    max_time_subq = (
        db.query(
            Message.conversation_id,
            func.max(Message.created_at).label("max_time"),
        )
        .filter(Message.conversation_id.in_(conv_ids))
        .group_by(Message.conversation_id)
        .subquery()
    )

    last_messages = (
        db.query(Message)
        .join(
            max_time_subq,
            and_(
                Message.conversation_id == max_time_subq.c.conversation_id,
                Message.created_at == max_time_subq.c.max_time,
            ),
        )
        .all()
    )
    last_msg_map = {m.conversation_id: m for m in last_messages}

    # 获取未读计数
    unread_counts = (
        db.query(Message.conversation_id, func.count(Message.id).label("cnt"))
        .filter(
            Message.conversation_id.in_(conv_ids),
            Message.is_read == False,
            Message.sender_id != user.id,
        )
        .group_by(Message.conversation_id)
        .all()
    )
    unread_map = {row[0]: row[1] for row in unread_counts}

    # 批量取所有涉及的用户信息
    all_user_ids = set()
    for c in conversations:
        if c.user1_id:
            all_user_ids.add(c.user1_id)
        if c.user2_id:
            all_user_ids.add(c.user2_id)
    all_user_ids.discard(user.id)

    users_map = {}
    if all_user_ids:
        users = db.query(User).filter(User.id.in_(all_user_ids)).all()
        users_map = {u.id: u for u in users}

    result = []
    for conv in conversations:
        last_message = last_msg_map.get(conv.id)
        updated_at = (
            conv.last_message_at.isoformat()
            if conv.last_message_at
            else conv.created_at.isoformat()
        )
        if conv.type == 'community':
            result.append({
                "id": conv.id,
                "conversation_id": conv.id,
                "user1_id": conv.user1_id,
                "user2_id": conv.user2_id,
                "type": conv.type,
                "community_id": conv.community_id,
                "community_name": conv.community.name if conv.community else None,
                "community_avatar": conv.community.avatar_url if conv.community else None,
                "participants": [],
                "other_user": None,
                "last_message": last_message.to_dict() if last_message else None,
                "unread_count": unread_map.get(conv.id, 0),
                "updated_at": updated_at,
                "last_message_at": updated_at,
            })
            continue

        other_id = conv.user2_id if conv.user1_id == user.id else conv.user1_id
        other_user = users_map.get(other_id)
        participants = []
        if other_user:
            participants.append({
                "user_id": other_user.id,
                "username": other_user.username,
                "avatar_url": other_user.avatar_url,
                "is_online": ws_manager.is_connected(other_user.id),
            })

        result.append({
            "id": conv.id,
            "conversation_id": conv.id,
            "user1_id": conv.user1_id,
            "user2_id": conv.user2_id,
            "type": conv.type,
            "participants": participants,
            "other_user": other_user.to_dict() if other_user else None,
            "last_message": last_message.to_dict() if last_message else None,
            "unread_count": unread_map.get(conv.id, 0),
            "updated_at": updated_at,
            "last_message_at": updated_at,
        })

    return {"sessions": result, "total": total, "page": page, "per_page": per_page}


# ============================================================
# 会话列表（旧版兼容，与 /sessions 分立）
# ============================================================


@router.get("/conversations")
def get_conversations(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取当前用户的所有会话列表（扁平结构，与 /sessions 同构）"""
    from sqlalchemy import func, and_

    conversations = (
        db.query(Conversation)
        .filter(
            (Conversation.user1_id == user.id) | (Conversation.user2_id == user.id)
        )
        .order_by(func.coalesce(Conversation.last_message_at, Conversation.created_at).desc())
        .all()
    )

    if not conversations:
        return {"conversations": [], "total": 0}

    conv_ids = [c.id for c in conversations]

    max_time_subq = (
        db.query(
            Message.conversation_id,
            func.max(Message.created_at).label("max_time"),
        )
        .filter(Message.conversation_id.in_(conv_ids))
        .group_by(Message.conversation_id)
        .subquery()
    )

    last_messages = (
        db.query(Message)
        .join(
            max_time_subq,
            and_(
                Message.conversation_id == max_time_subq.c.conversation_id,
                Message.created_at == max_time_subq.c.max_time,
            ),
        )
        .all()
    )
    last_msg_map = {m.conversation_id: m for m in last_messages}

    unread_counts = (
        db.query(Message.conversation_id, func.count(Message.id).label("cnt"))
        .filter(
            Message.conversation_id.in_(conv_ids),
            Message.is_read == False,
            Message.sender_id != user.id,
        )
        .group_by(Message.conversation_id)
        .all()
    )
    unread_map = {row[0]: row[1] for row in unread_counts}

    # 批量取所有涉及的用户信息
    all_user_ids = set()
    for c in conversations:
        if c.user1_id:
            all_user_ids.add(c.user1_id)
        if c.user2_id:
            all_user_ids.add(c.user2_id)
    all_user_ids.discard(user.id)

    users_map = {}
    if all_user_ids:
        users = db.query(User).filter(User.id.in_(all_user_ids)).all()
        users_map = {u.id: u for u in users}

    result = []
    for conv in conversations:
        other_id = conv.user2_id if conv.user1_id == user.id else conv.user1_id
        other_user = users_map.get(other_id)
        last_message = last_msg_map.get(conv.id)

        result.append({
            "id": conv.id,
            "conversation_id": conv.id,
            "user1_id": conv.user1_id,
            "user2_id": conv.user2_id,
            "type": "single",
            "other_user": other_user.to_dict() if other_user else None,
            "last_message": last_message.to_dict() if last_message else None,
            "unread_count": unread_map.get(conv.id, 0),
            "is_online": ws_manager.is_connected(other_user.id) if other_user else False,
            "last_message_at": (
                conv.last_message_at.isoformat()
                if conv.last_message_at
                else conv.created_at.isoformat()
            ),
        })

    return {"conversations": result, "total": len(result)}


@router.get("/conversations/{user_id}")
def get_conversation_with_user(
    user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取与特定用户的会话（如不存在则自动创建）"""
    other_user = db.query(User).filter(User.id == user_id).first()
    if not other_user:
        raise HTTPException(status_code=404, detail="User not found")

    uid1, uid2 = min(user.id, user_id), max(user.id, user_id)
    conversation = db.query(Conversation).filter(
        Conversation.user1_id == uid1, Conversation.user2_id == uid2
    ).first()

    if not conversation:
        # 自动创建会话 + 参与者（同一个事务）
        conversation = Conversation(user1_id=uid1, user2_id=uid2)
        db.add(conversation)
        db.flush()  # 获取 conversation.id
        participant1 = ConversationParticipant(conversation_id=conversation.id, user_id=uid1)
        participant2 = ConversationParticipant(conversation_id=conversation.id, user_id=uid2)
        db.add_all([participant1, participant2])
        db.commit()
        db.refresh(conversation)

    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.asc())
        .limit(50)
        .all()
    )

    return {
        "conversation": conversation.to_dict(),
        "other_user": other_user.to_dict(),
        "messages": [msg.to_dict() for msg in messages],
        'is_online': ws_manager.is_connected(user_id),
    }


@router.get("/conversations/{conversation_id}/messages")
def get_messages(
    conversation_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取会话的消息历史（分页）"""
    conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.user1_id != user.id and conversation.user2_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    messages_query = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
    )
    total = messages_query.count()
    messages = messages_query.offset((page - 1) * per_page).limit(per_page).all()
    pages = (total + per_page - 1) // per_page if total > 0 else 0

    return {
        "messages": [msg.to_dict() for msg in messages],
        "total": total,
        "pages": pages,
        "current_page": page,
        "per_page": per_page,
    }


@router.get("/messages/batch")
def get_messages_batch(
    conv_ids: str = Query(..., description="逗号分隔的会话 ID 列表，最多 20 个"),
    per_page: int = Query(30, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """批量获取多个会话的最近消息

    用于首页加载时一次性预加载所有活跃会话的消息，
    替代逐个请求 /api/chat/messages/{convId} 的 N+1 问题。
    """
    # 解析会话 ID 列表
    try:
        conv_id_list = [int(x.strip()) for x in conv_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conv_ids format")

    if not conv_id_list:
        raise HTTPException(status_code=400, detail="conv_ids is required")
    # 去重保序，并限制单次批量预取规模。
    conv_id_list = list(dict.fromkeys(conv_id_list))
    if len(conv_id_list) > 20:
        raise HTTPException(status_code=400, detail="Maximum 20 conversation IDs allowed")

    # 批量查询会话，验证用户权限
    conversations = db.query(Conversation).filter(
        Conversation.id.in_(conv_id_list)
    ).all()
    conv_map = {c.id: c for c in conversations}

    for cid in conv_id_list:
        conv = conv_map.get(cid)
        if not conv:
            raise HTTPException(status_code=404, detail=f"Conversation {cid} not found")
        if conv.user1_id != user.id and conv.user2_id != user.id:
            raise HTTPException(status_code=403, detail=f"Unauthorized for conversation {cid}")

    # 合并为单次 UNION ALL 查询：每个会话独立 LIMIT，一次往返
    from sqlalchemy import text
    from collections import OrderedDict

    # 构建原生 UNION ALL SQL，规避 sqlalchemy union_all() 的 subquery 类型兼容问题
    union_parts = []
    for cid in conv_id_list:
        union_parts.append(
            "(SELECT id, conversation_id, sender_id, content, message_type, "
            "media_url, is_read, related_id, created_at "
            f"FROM messages WHERE conversation_id = {cid} "
            "ORDER BY created_at DESC LIMIT :limit)"
        )
    union_sql = " UNION ALL ".join(union_parts)
    union_sql += " ORDER BY conversation_id, created_at DESC"

    messages_by_conv: dict = OrderedDict()
    for cid in conv_id_list:
        messages_by_conv[cid] = []

    if union_parts:
        all_rows = db.execute(text(union_sql), {"limit": per_page}).fetchall()

        for row in all_rows:
            bucket = messages_by_conv.get(row.conversation_id)
            if bucket is not None and len(bucket) < per_page:
                bucket.append(row)

    # 组装响应（按 conv_id_list 原始顺序，消息按时间升序）
    result_conversations = []
    for cid in conv_id_list:
        msgs = messages_by_conv[cid]
        msgs.reverse()  # DESC → ASC
        result_conversations.append({
            "conversation_id": cid,
            "messages": [
                {
                    "id": msg.id,
                    "conversation_id": msg.conversation_id,
                    "sender_id": msg.sender_id,
                    "content": msg.content,
                    "message_type": msg.message_type,
                    "media_url": msg.media_url,
                    "file_url": msg.media_url,
                    "file_name": None,
                    "file_size": None,
                    "is_read": msg.is_read,
                    "related_id": msg.related_id,
                    "created_at": msg.created_at.isoformat() if msg.created_at else None,
                    "updated_at": msg.created_at.isoformat() if msg.created_at else None,
                }
                for msg in msgs
            ],
        })

    return {
        "success": True,
        "data": {
            "conversations": result_conversations,
        },
    }



# ============================================================
# 消息分页端点（新版，与 WS 消息列表同构）
# ============================================================
@router.get("/messages/{conversation_id}")
def get_messages_v2(
    conversation_id: int,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """分页获取消息（新版，返回 has_more 标志）"""
    conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.user1_id != user.id and conversation.user2_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    messages_query = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
    )
    total = messages_query.count()
    offset = (page - 1) * limit
    messages = messages_query.offset(offset).limit(limit + 1).all()

    has_more = len(messages) > limit
    if has_more:
        messages = messages[:limit]

    return {
        "messages": [msg.to_dict() for msg in messages],
        "total": total,
        "has_more": has_more,
        "current_page": page,
        "limit": limit,
    }


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: int,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """HTTP 发送消息（WebSocket 降级备选）
    接收 {content, message_type, media_url, related_id}
    """
    conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.user1_id != user.id and conversation.user2_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    # 屏蔽检查（使用缓存）
    receiver_id = conversation.user2_id if conversation.user1_id == user.id else conversation.user1_id
    blocked_by_receiver = ws_manager.get_blocked_user_ids(receiver_id)
    blocked_by_sender = ws_manager.get_blocked_user_ids(user.id)
    if user.id in blocked_by_receiver or receiver_id in blocked_by_sender:
        raise HTTPException(status_code=403, detail="Cannot send message to this user")

    normalized = normalize_user_message_payload(payload, db)
    content = normalized["content"]
    message_type = normalized["message_type"]
    media_url = normalized["media_url"]
    related_id = normalized["related_id"]

    message = Message(
        conversation_id=conversation_id,
        sender_id=user.id,
        content=content,
        message_type=message_type,
        media_url=media_url,
        related_id=related_id,
    )

    try:
        db.add(message)
        # 更新会话最后消息时间
        conversation.last_message_at = datetime.utcnow()
        db.commit()
        db.refresh(message)
        logger.info(f"Message sent in conversation {conversation_id} by user {user.id}")

        # --- WebSocket 实时推送 ---
        # 推送给接收方：未读消息总数
        receiver_id = conversation.user2_id if conversation.user1_id == user.id else conversation.user1_id
        receiver_unread = (
            db.query(Message)
            .filter(
                Message.conversation_id == conversation_id,
                Message.is_read == False,
                Message.sender_id != receiver_id,
            )
            .count()
        )
        await ws_manager.send_with_seq(receiver_id, "new_message", {
            "conversation_id": conversation_id,
            "data": message.to_dict(),
            "unread_count": receiver_unread,
        })
        # 也推送给发送者：同步自己的未读（通常为0）
        sender_unread = (
            db.query(Message)
            .filter(
                Message.conversation_id == conversation_id,
                Message.is_read == False,
                Message.sender_id != user.id,
            )
            .count()
        )
        await ws_manager.send_with_seq(user.id, "new_message", {
            "conversation_id": conversation_id,
            "data": message.to_dict(),
            "unread_count": sender_unread,
        })

        # --- 后台/离线通知中心 ---
        # 后台用户即使 WS 尚未断开，也需要系统通知入口。
        if _should_create_message_notification(db, receiver_id):
            from app.services.notification_service import NotificationService
            preview = content[:50] + '...' if len(content) > 50 else content
            NotificationService.notify_message(receiver_id, user.id, preview, conversation_id)

        # 失效缓存（新消息导致 last_message / unread_count 变化）
        ws_manager.invalidate_participant_caches([user.id, receiver_id])

        return {"message": "Message sent", "data": message.to_dict()}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
@router.post("/conversations/{conversation_id}/mark-read")
async def mark_conversation_as_read(
    conversation_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """标记会话中的所有消息为已读"""
    conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.user1_id != user.id and conversation.user2_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    updated_count = (
        db.query(Message)
        .filter(
            Message.conversation_id == conversation_id,
            Message.is_read == False,
            Message.sender_id != user.id,
        )
        .update({Message.is_read: True}, synchronize_session=False)
    )

    try:
        db.commit()

        # --- WebSocket 实时推送 ---
        # 计算该用户剩余的全局未读数量
        all_conv_ids = [c.id for c in db.query(Conversation).filter(
            (Conversation.user1_id == user.id) | (Conversation.user2_id == user.id)
        ).all()]
        total_unread = (
            db.query(Message)
            .filter(
                Message.conversation_id.in_(all_conv_ids),
                Message.is_read == False,
                Message.sender_id != user.id,
            )
            .count()
        )
        await ws_manager.send_with_seq(user.id, "conversation_read", {
            "conversation_id": conversation_id,
            "unread_count": total_unread,
        })

        # 失效缓存（已读导致 unread_count 变化）
        ws_manager.invalidate_session_cache(user.id)

        return {"message": "All messages marked as read", "marked_count": updated_count}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/users/online")
def get_online_users_list(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取在线用户列表"""
    online_ids = ws_manager.get_online_user_ids()
    if not online_ids:
        return {"online_users": [], "total": 0}

    users = db.query(User).filter(User.id.in_(online_ids)).all()
    return {"online_users": [u.to_dict() for u in users], "total": len(users)}


@router.get("/users/{user_id}/status")
def get_user_status(
    user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取用户在线状态（仅好友可查）"""
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # 隐私保护：非好友不允许查看在线状态
    is_friend = db.query(Friendship).filter(
        Friendship.status == 'accepted',
        ((Friendship.sender_id == user.id) & (Friendship.receiver_id == user_id))
        | ((Friendship.sender_id == user_id) & (Friendship.receiver_id == user.id))
    ).first()
    if not is_friend:
        raise HTTPException(status_code=403, detail="Only friends can view online status")

    return {"user_id": user_id, "is_online": ws_manager.is_connected(user_id), "user": target.to_dict()}


@router.get("/unread-count")
def get_unread_count(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取未读消息总数（单条 SQL 聚合）"""
    from sqlalchemy import func

    conversations = db.query(Conversation.id).filter(
        (Conversation.user1_id == user.id) | (Conversation.user2_id == user.id)
    ).all()

    if not conversations:
        return {"total_unread": 0}

    conv_ids = [c.id for c in conversations]
    total_unread = (
        db.query(func.count(Message.id))
        .filter(
            Message.conversation_id.in_(conv_ids),
            Message.is_read == False,
            Message.sender_id != user.id,
        )
        .scalar()
    )

    return {"total_unread": total_unread}


# ============================================================
# 消息撤回
# ============================================================

@router.post("/messages/{message_id}/recall")
def recall_message(
    message_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """撤回一条消息（2分钟内，仅限发送者）"""
    from datetime import timedelta

    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.sender_id != user.id:
        raise HTTPException(status_code=403, detail="Cannot recall other's message")
    if msg.is_recalled:
        raise HTTPException(status_code=400, detail="Message already recalled")

    # 2分钟时间限制
    if msg.created_at and datetime.utcnow() - msg.created_at > timedelta(minutes=2):
        raise HTTPException(status_code=400, detail="Recall time limit exceeded (2 min)")

    msg.is_recalled = True
    msg.recalled_at = datetime.utcnow()
    db.commit()
    db.refresh(msg)

    # 通过 WS 推送撤回事件给会话参与者
    participant_ids = [
        r.user_id
        for r in db.query(ConversationParticipant)
        .filter(ConversationParticipant.conversation_id == msg.conversation_id)
        .all()
    ]
    recall_data = {
        "message_id": msg.id,
        "conversation_id": msg.conversation_id,
        "is_recalled": True,
        "recalled_at": msg.recalled_at.isoformat() if msg.recalled_at else None,
    }
    for pid in participant_ids:
        ws_manager.enqueue_push(pid, "message_recalled", recall_data)

    ws_manager.invalidate_participant_caches(participant_ids)

    return {
        "success": True,
        "message_id": msg.id,
        "is_recalled": True,
        "recalled_at": msg.recalled_at.isoformat() if msg.recalled_at else None,
    }

