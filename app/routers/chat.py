"""
聊天路由 - FastAPI 重构版
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import User, Conversation, Message, ConversationParticipant, Friendship, _utc_z
from app.models.community import CommunityMember
from app.ws_manager import ws_manager
from app.services.message_type_service import (
    normalize_user_message_payload,
    redact_unavailable_post_card,
    redact_unavailable_post_cards_batch,
)
from app.services.presence_service import is_user_product_online
from app.services.block_service import excluded_user_ids, has_block_between, visible_user_predicate
from app.services.quote_service import (
    build_quote_preview,
    inject_quote_preview,
    inject_quote_preview_batch,
    validate_quote,
)
from app.services.chat_read_state_service import (
    can_access_conversation,
    get_active_conversation_participant_ids,
    get_community_participant,
    get_community_unread_counts,
    get_total_unread_count,
    mark_community_conversation_read,
)
from app.services.moderation_route_helpers import (
    message_text_payload_for_moderation,
    moderate_route_fields,
)
from app.services.moderation_service import moderation_service

logger = logging.getLogger(__name__)
router = APIRouter()


def _should_create_message_notification(db: Session, user_id: int) -> bool:
    """消息始终创建通知中心记录；移动推送由设备 app_state 决定。"""
    return True


def _serialize_messages_for_viewer(
    db: Session,
    messages: list[dict],
    viewer_user_id: int,
) -> list[dict]:
    inject_quote_preview_batch(db, messages, viewer_user_id=viewer_user_id)
    return redact_unavailable_post_cards_batch(db, messages, viewer_user_id)


def _serialize_message_for_viewer(
    db: Session,
    message: dict,
    viewer_user_id: int,
) -> dict:
    inject_quote_preview(db, message, viewer_user_id=viewer_user_id)
    return redact_unavailable_post_card(db, message, viewer_user_id)


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
    blocked_ids = excluded_user_ids(db, user.id)
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
        .filter(
            Message.conversation_id.in_(conv_ids),
            visible_user_predicate(user.id, Message.sender_id),
        )
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

    # 获取未读计数：私聊沿用 Message.is_read，群聊按成员 read cursor 计算。
    unread_counts = (
        db.query(Message.conversation_id, func.count(Message.id).label("cnt"))
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(
            Message.conversation_id.in_(conv_ids),
            Conversation.type != 'community',
            Message.is_read == False,
            Message.sender_id != user.id,
        )
        .group_by(Message.conversation_id)
        .all()
    )
    unread_map = {row[0]: row[1] for row in unread_counts}
    unread_map.update(get_community_unread_counts(db, user.id, conv_ids))

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

    # 批量回填 last_message 的 quote_preview，保证全局口径一致
    last_msg_dicts = [m.to_dict() for m in last_messages]
    _serialize_messages_for_viewer(db, last_msg_dicts, user.id)
    last_msg_dict_map = {d["conversation_id"]: d for d in last_msg_dicts}

    result = []
    for conv in conversations:
        last_message_dict = last_msg_dict_map.get(conv.id)
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
                "last_message": last_message_dict,
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
                "is_online": is_user_product_online(
                    db,
                    other_user.id,
                    raw_ws_online=ws_manager.is_connected(other_user.id),
                ),
            })

        result.append({
            "id": conv.id,
            "conversation_id": conv.id,
            "user1_id": conv.user1_id,
            "user2_id": conv.user2_id,
            "type": conv.type,
            "participants": participants,
            "other_user": other_user.to_dict() if other_user else None,
            "last_message": last_message_dict,
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
    from sqlalchemy import func, and_, case

    blocked_ids = excluded_user_ids(db, user.id)
    other_user_expr = case(
        (Conversation.user1_id == user.id, Conversation.user2_id),
        else_=Conversation.user1_id,
    )
    conversations_query = db.query(Conversation).filter(
        (Conversation.user1_id == user.id) | (Conversation.user2_id == user.id)
    )
    if blocked_ids:
        conversations_query = conversations_query.filter(~other_user_expr.in_(blocked_ids))
    conversations = (
        conversations_query
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
        .filter(
            Message.conversation_id.in_(conv_ids),
            visible_user_predicate(user.id, Message.sender_id),
        )
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

    # 批量回填 last_message 的 quote_preview，保证全局口径一致
    last_msg_dicts = [m.to_dict() for m in last_messages]
    _serialize_messages_for_viewer(db, last_msg_dicts, user.id)
    last_msg_dict_map = {d["conversation_id"]: d for d in last_msg_dicts}

    result = []
    for conv in conversations:
        other_id = conv.user2_id if conv.user1_id == user.id else conv.user1_id
        other_user = users_map.get(other_id)
        last_message_dict = last_msg_dict_map.get(conv.id)

        result.append({
            "id": conv.id,
            "conversation_id": conv.id,
            "user1_id": conv.user1_id,
            "user2_id": conv.user2_id,
            "type": "single",
            "other_user": other_user.to_dict() if other_user else None,
            "last_message": last_message_dict,
            "unread_count": unread_map.get(conv.id, 0),
            "is_online": (
                is_user_product_online(
                    db,
                    other_user.id,
                    raw_ws_online=ws_manager.is_connected(other_user.id),
                )
                if other_user
                else False
            ),
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
    if not other_user or has_block_between(db, user.id, user_id):
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
        "messages": _serialize_messages_for_viewer(
            db, [msg.to_dict() for msg in messages], user.id,
        ),
        'is_online': is_user_product_online(
            db,
            user_id,
            raw_ws_online=ws_manager.is_connected(user_id),
        ),
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
    if not can_access_conversation(db, conversation, user.id):
        raise HTTPException(status_code=403, detail="Unauthorized")

    messages_query = (
        db.query(Message)
        .filter(
            Message.conversation_id == conversation_id,
            visible_user_predicate(user.id, Message.sender_id),
        )
        .order_by(Message.created_at.desc())
    )
    total = messages_query.count()
    messages = messages_query.offset((page - 1) * per_page).limit(per_page).all()
    pages = (total + per_page - 1) // per_page if total > 0 else 0

    return {
        "messages": _serialize_messages_for_viewer(
            db, [msg.to_dict() for msg in messages], user.id,
        ),
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
        if not can_access_conversation(db, conv, user.id):
            raise HTTPException(status_code=403, detail=f"Unauthorized for conversation {cid}")

    # 单次窗口查询：先应用双向屏蔽，再对每个会话独立 LIMIT。
    from sqlalchemy import func
    from collections import OrderedDict

    ranked_messages = db.query(
        Message.id.label("id"),
        Message.conversation_id.label("conversation_id"),
        Message.sender_id.label("sender_id"),
        Message.content.label("content"),
        Message.message_type.label("message_type"),
        Message.media_url.label("media_url"),
        Message.is_read.label("is_read"),
        Message.related_id.label("related_id"),
        Message.client_msg_id.label("client_msg_id"),
        Message.quote_message_id.label("quote_message_id"),
        Message.quote_preview.label("quote_preview"),
        Message.is_recalled.label("is_recalled"),
        Message.created_at.label("created_at"),
        func.row_number().over(
            partition_by=Message.conversation_id,
            order_by=Message.created_at.desc(),
        ).label("row_num"),
    ).filter(
        Message.conversation_id.in_(conv_id_list),
        visible_user_predicate(user.id, Message.sender_id),
    ).subquery()

    all_rows = db.query(ranked_messages).filter(
        ranked_messages.c.row_num <= per_page
    ).order_by(
        ranked_messages.c.conversation_id,
        ranked_messages.c.created_at.desc(),
    ).all()

    messages_by_conv: dict = OrderedDict((cid, []) for cid in conv_id_list)
    for row in all_rows:
        messages_by_conv[row.conversation_id].append(row)

    # 组装响应（按 conv_id_list 原始顺序，消息按时间升序）
    result_conversations = []
    for cid in conv_id_list:
        msgs = messages_by_conv[cid]
        msgs.reverse()  # DESC → ASC
        message_dicts = [
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
                "client_msg_id": msg.client_msg_id,
                "quote_message_id": msg.quote_message_id,
                "quote_preview": msg.quote_preview,
                "is_recalled": msg.is_recalled,
                "created_at": _utc_z(msg.created_at),
                "updated_at": _utc_z(msg.created_at),
            }
            for msg in msgs
        ]
        inject_quote_preview_batch(db, message_dicts, viewer_user_id=user.id)
        redact_unavailable_post_cards_batch(db, message_dicts, user.id)
        result_conversations.append({
            "conversation_id": cid,
            "messages": message_dicts,
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
    if not can_access_conversation(db, conversation, user.id):
        raise HTTPException(status_code=403, detail="Unauthorized")

    messages_query = (
        db.query(Message)
        .filter(
            Message.conversation_id == conversation_id,
            visible_user_predicate(user.id, Message.sender_id),
        )
        .order_by(Message.created_at.desc())
    )
    total = messages_query.count()
    offset = (page - 1) * limit
    messages = messages_query.offset(offset).limit(limit + 1).all()

    has_more = len(messages) > limit
    if has_more:
        messages = messages[:limit]

    return {
        "messages": _serialize_messages_for_viewer(
            db, [msg.to_dict() for msg in messages], user.id,
        ),
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
    if not can_access_conversation(db, conversation, user.id):
        raise HTTPException(status_code=403, detail="Unauthorized")

    participant_ids = get_active_conversation_participant_ids(db, conversation)
    if conversation.type == 'community':
        blocked_ids = excluded_user_ids(db, user.id)
        participant_ids = [
            participant_id
            for participant_id in participant_ids
            if participant_id == user.id or participant_id not in blocked_ids
        ]

    normalized = normalize_user_message_payload(
        payload,
        db,
        viewer_user_id=user.id,
        recipient_user_ids=participant_ids,
        destination_community_id=(
            conversation.community_id if conversation.type == "community" else None
        ),
    )
    content = normalized["content"]
    message_type = normalized["message_type"]
    media_url = normalized["media_url"]
    related_id = normalized["related_id"]
    moderation_payload = message_text_payload_for_moderation(payload)
    if moderation_payload:
        moderate_route_fields(
            moderation_service,
            "POST /api/chat/conversations/{conversation_id}/messages",
            moderation_payload,
            actor_user_id=user.id,
            is_public=False,
        )

    # 校验并生成引用预览（实时生成，不入库 quote_preview）
    quote_message_id = payload.get("quote_message_id")
    quoted = validate_quote(db, conversation_id, quote_message_id)

    message = Message(
        conversation_id=conversation_id,
        sender_id=user.id,
        content=content,
        message_type=message_type,
        media_url=media_url,
        related_id=related_id,
        quote_message_id=quote_message_id if quoted else None,
    )

    try:
        db.add(message)
        # 更新会话最后消息时间
        conversation.last_message_at = datetime.utcnow()
        db.commit()
        db.refresh(message)
        logger.info(f"Message sent in conversation {conversation_id} by user {user.id}")

        # 预先序列化（含 quote_preview 实时回填）
        message_dict = _serialize_message_for_viewer(db, message.to_dict(), user.id)

        # --- WebSocket 实时推送 ---
        if conversation.type == 'community':
            unread_counts = {
                pid: get_community_unread_counts(db, pid, [conversation_id]).get(conversation_id, 0)
                for pid in participant_ids
            }
        else:
            unread_counts = {}
            for pid in participant_ids:
                unread_counts[pid] = (
                    db.query(Message)
                    .filter(
                        Message.conversation_id == conversation_id,
                        Message.is_read == False,
                        Message.sender_id != pid,
                    )
                    .count()
                )

        for pid in participant_ids:
            delivered_message = (
                message_dict
                if pid == user.id
                else _serialize_message_for_viewer(db, message.to_dict(), pid)
            )
            await ws_manager.send_with_seq(pid, "new_message", {
                "conversation_id": conversation_id,
                "data": delivered_message,
                "unread_count": unread_counts.get(pid, 0),
            })

        # --- 通知中心记录 ---
        # 消息始终创建通知中心记录；移动推送由设备 app_state 决定。
        from app.services.notification_service import NotificationService
        preview = content[:50] + '...' if len(content) > 50 else content
        for pid in participant_ids:
            if pid != user.id and _should_create_message_notification(db, pid):
                NotificationService.notify_message(pid, user.id, preview, conversation_id)

        # 失效缓存（新消息导致 last_message / unread_count 变化）
        ws_manager.invalidate_participant_caches(participant_ids)

        return {"message": "Message sent", "data": message_dict}
    except Exception as e:
        db.rollback()
        logger.error(
            "Send chat message failed conversation_id=%s user_id=%s error_type=%s",
            conversation_id,
            user.id,
            type(e).__name__,
        )
        raise HTTPException(status_code=500, detail="An error occurred while sending the message")
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

    is_direct_participant = conversation.user1_id == user.id or conversation.user2_id == user.id
    if conversation.type != 'community' and not can_access_conversation(db, conversation, user.id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    community_participant = None
    if conversation.type == 'community':
        community_participant = get_community_participant(db, conversation_id, user.id)
    if not is_direct_participant and not community_participant:
        raise HTTPException(status_code=403, detail="Unauthorized")

    if conversation.type == 'community':
        updated_count = mark_community_conversation_read(db, conversation_id, user.id)
    else:
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
        # 计算该用户剩余的全局未读数量（私聊 legacy + 群聊成员 read cursor）
        total_unread = get_total_unread_count(db, user.id)
        await ws_manager.send_with_seq(user.id, "conversation_read", {
            "conversation_id": conversation_id,
            "unread_count": total_unread,
        })

        # 失效缓存（已读导致 unread_count 变化）
        ws_manager.invalidate_session_cache(user.id)

        return {"message": "All messages marked as read", "marked_count": updated_count}
    except Exception as e:
        db.rollback()
        logger.error(
            "Mark conversation read failed conversation_id=%s user_id=%s error_type=%s",
            conversation_id,
            user.id,
            type(e).__name__,
        )
        raise HTTPException(status_code=500, detail="An error occurred while marking the conversation as read")


@router.get("/users/online")
def get_online_users_list(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取产品级在线好友列表。"""
    blocked_ids = excluded_user_ids(db, user.id)
    friendships_query = db.query(Friendship).filter(
        Friendship.status == 'accepted',
        ((Friendship.sender_id == user.id) & (Friendship.receiver_id != user.id))
        | ((Friendship.receiver_id == user.id) & (Friendship.sender_id != user.id)),
    )
    if blocked_ids:
        friendships_query = friendships_query.filter(
            ~Friendship.sender_id.in_(blocked_ids),
            ~Friendship.receiver_id.in_(blocked_ids),
        )
    friendships = friendships_query.all()
    friend_ids = {
        f.sender_id if f.receiver_id == user.id else f.receiver_id
        for f in friendships
    }
    if not friend_ids:
        return {"online_users": [], "total": 0}

    raw_online_ids = set(ws_manager.get_online_user_ids()) & friend_ids
    candidate_ids = friend_ids | raw_online_ids
    users = db.query(User).filter(User.id.in_(candidate_ids)).all()
    online_users = []
    for candidate in users:
        if is_user_product_online(
            db,
            candidate.id,
            raw_ws_online=ws_manager.is_connected(candidate.id),
        ):
            data = candidate.to_dict()
            data["is_online"] = True
            online_users.append(data)

    return {"online_users": online_users, "total": len(online_users)}


@router.get("/users/{user_id}/status")
def get_user_status(
    user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取用户在线状态（仅好友可查）"""
    target = db.query(User).filter(User.id == user_id).first()
    if not target or has_block_between(db, user.id, user_id):
        raise HTTPException(status_code=404, detail="User not found")

    # 隐私保护：非好友不允许查看在线状态
    is_friend = db.query(Friendship).filter(
        Friendship.status == 'accepted',
        ((Friendship.sender_id == user.id) & (Friendship.receiver_id == user_id))
        | ((Friendship.sender_id == user_id) & (Friendship.receiver_id == user.id))
    ).first()
    if not is_friend:
        raise HTTPException(status_code=403, detail="Only friends can view online status")

    return {
        "user_id": user_id,
        "is_online": is_user_product_online(
            db,
            user_id,
            raw_ws_online=ws_manager.is_connected(user_id),
        ),
        "user": target.to_dict(),
    }


@router.get("/unread-count")
def get_unread_count(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取未读消息总数（私聊 legacy + 群聊成员 read cursor）"""
    return {"total_unread": get_total_unread_count(db, user.id)}


# ============================================================
# 消息撤回
# ============================================================

@router.get("/conversations/{conversation_id}/messages/around")
def get_messages_around(
    conversation_id: int,
    target_id: int = Query(..., ge=1),
    before: int = Query(20, ge=1, le=50),
    after: int = Query(20, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取某条消息在会话内的上下文窗口（用于"点击引用 → 定位原消息"）。

    返回：
      - messages: 升序，含 target 共 before+after+1 条
      - has_more_before / has_more_after: 是否还有更早/更晚的消息
    """
    conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if not can_access_conversation(db, conversation, user.id):
        raise HTTPException(status_code=403, detail="Unauthorized")

    target = db.query(Message).filter(
        Message.id == target_id,
        Message.conversation_id == conversation_id,
        visible_user_predicate(user.id, Message.sender_id),
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="Target message not found")

    # before：target 之前（更早）的消息，按 created_at 降序取后反转为升序
    before_msgs = (
        db.query(Message)
        .filter(
            Message.conversation_id == conversation_id,
            Message.created_at < target.created_at,
            visible_user_predicate(user.id, Message.sender_id),
        )
        .order_by(Message.created_at.desc())
        .limit(before + 1)
        .all()
    )
    has_more_before = len(before_msgs) > before
    before_msgs = list(reversed(before_msgs[:before]))

    # after：target 之后（更晚）的消息，按 created_at 升序
    after_msgs = (
        db.query(Message)
        .filter(
            Message.conversation_id == conversation_id,
            Message.created_at > target.created_at,
            visible_user_predicate(user.id, Message.sender_id),
        )
        .order_by(Message.created_at.asc())
        .limit(after + 1)
        .all()
    )
    has_more_after = len(after_msgs) > after
    after_msgs = after_msgs[:after]

    window = before_msgs + [target] + after_msgs
    return {
        "messages": _serialize_messages_for_viewer(
            db, [m.to_dict() for m in window], user.id,
        ),
        "target_id": target_id,
        "has_more_before": has_more_before,
        "has_more_after": has_more_after,
    }


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
    conversation = db.query(Conversation).filter(Conversation.id == msg.conversation_id).first()
    if not can_access_conversation(db, conversation, user.id):
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

    # 通过 WS 推送撤回事件给可投递会话参与者
    conversation = db.query(Conversation).filter(Conversation.id == msg.conversation_id).first()
    participant_ids = get_active_conversation_participant_ids(db, conversation) if conversation else []
    blocked_ids = excluded_user_ids(db, user.id)
    participant_ids = [
        participant_id
        for participant_id in participant_ids
        if participant_id == user.id or participant_id not in blocked_ids
    ]
    recall_data = {
        "message_id": msg.id,
        "conversation_id": msg.conversation_id,
        "sender_id": user.id,
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

