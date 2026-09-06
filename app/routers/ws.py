"""
WebSocket 端点 — 即时通讯协议

协议规范: 统一 envelope {type, request_id?, payload}
- auth 消息鉴权（message-based，不再 URL-based）
- send_message 发送聊天消息
- send_event 发送状态事件（已读等）
- 所有需持久化的推送统一走 message 帧（带 seq）

连接后先收到 auth 消息鉴权，通过后自动推送 session_list + auth_result。
"""

from datetime import datetime, timedelta
import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.ws_manager import ws_manager
from app.services.message_type_service import (
    normalize_user_message_payload,
    redact_unavailable_post_card,
    redact_unavailable_post_cards_batch,
)
from app.services.notification_query_service import (
    visible_notification_query,
    visible_unread_count,
)
from app.services.quote_service import (
    inject_quote_preview,
    validate_quote,
)
from app.services.block_service import excluded_user_ids, has_block_between, visible_user_predicate
from app.services.chat_read_state_service import (
    can_access_conversation,
    get_active_conversation_participant_ids,
    get_community_unread_counts,
    mark_community_conversation_read,
)
from app.services.moderation_route_helpers import (
    message_text_payload_for_moderation,
    moderate_route_fields,
)
from app.services.moderation_service import moderation_service

logger = logging.getLogger(__name__)

router = APIRouter()

AUTH_TIMEOUT = 15  # 连接后 15s 内必须发 auth，否则断开


# ================================================================
# 工具函数
# ================================================================

def _get_db_session() -> Session:
    return SessionLocal()


def _should_send_message_push(db: Session, user_id: int) -> bool:
    """Return whether an optional mobile chat alert may be attempted."""
    return True


def _get_message_push_targets(sender_id: int, participant_ids: list[int]) -> list[int]:
    """Select non-sender recipients for optional mobile chat alerts."""
    db = _get_db_session()
    try:
        return [
            pid for pid in participant_ids
            if pid != sender_id and _should_send_message_push(db, pid)
        ]
    finally:
        db.close()


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
    from app.models.models import Conversation, ConversationParticipant
    conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if conversation:
        return get_active_conversation_participant_ids(db, conversation)
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

    if has_block_between(db, from_user_id, to_user_id):
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


def _get_dedup_state(user_id: int, client_msg_id: str) -> dict | None:
    if not client_msg_id:
        return None
    db = _get_db_session()
    try:
        from app.models.models import WSAckDedup
        row = db.query(WSAckDedup).filter(
            WSAckDedup.user_id == user_id,
            WSAckDedup.client_msg_id == client_msg_id,
        ).first()
        if not row:
            return None
        if row.processed_at and row.processed_at < datetime.utcnow() - timedelta(hours=24):
            return None
        return {"user_id": row.user_id, "message_id": row.message_id}
    finally:
        db.close()


def _can_send_to_participants(
    from_user_id: int,
    participant_ids: list[int],
    db: Session | None = None,
) -> bool:
    """Return whether a direct sender is isolated from any participant."""
    owns_session = db is None
    db = db or _get_db_session()
    try:
        blocked_ids = excluded_user_ids(db, from_user_id)
        return not any(
            participant_id != from_user_id and participant_id in blocked_ids
            for participant_id in participant_ids
        )
    finally:
        if owns_session:
            db.close()


def _validate_send_message_payload(payload: dict) -> dict | None:
    """Validate send_message payload. Returns {'code', 'error'} when invalid."""
    conversation_id = payload.get("conversation_id")
    receiver_id = payload.get("receiver_id")
    message_type = (payload.get("message_type") or "text").strip().lower()
    content = (payload.get("content") or "").strip()
    client_msg_id = payload.get("client_msg_id")

    if client_msg_id is not None and (not isinstance(client_msg_id, str) or len(client_msg_id) > 36):
        return {"code": 400, "error": "Invalid client_msg_id"}
    if not conversation_id and not receiver_id:
        return {"code": 400, "error": "conversation_id or receiver_id required"}
    if message_type == "text" and not content:
        return {"code": 400, "error": "Content cannot be empty"}
    if message_type == "post":
        return None
    try:
        normalize_user_message_payload(payload, db=None)
    except HTTPException as exc:
        return {"code": exc.status_code, "error": exc.detail}
    return None


def _send_error_status(error: str) -> int:
    if error == "Conversation not found":
        return 404
    if error in {"Not a conversation participant", "Cannot send message to this user"}:
        return 403
    if error == "Content cannot be empty":
        return 400
    return 500


def _build_session_list(db: Session, user_id: int) -> list[dict]:
    from sqlalchemy import case, func
    from app.models.models import Conversation, ConversationParticipant, Message, User
    from app.models.community import CommunityMember
    from app.services.quote_service import inject_quote_preview_batch

    other_user_expr = case(
        (Conversation.user1_id == user_id, Conversation.user2_id),
        else_=Conversation.user1_id,
    )
    blocked_ids = excluded_user_ids(db, user_id)
    query = (
        db.query(Conversation)
        .outerjoin(ConversationParticipant)
        .outerjoin(CommunityMember, CommunityMember.community_id == Conversation.community_id)
        .filter(
            (
                (Conversation.type != 'community')
                & (ConversationParticipant.user_id == user_id)
            )
            | (
                (Conversation.type == 'community')
                & (CommunityMember.user_id == user_id)
                & (CommunityMember.status == 'active')
            )
        )
    )
    if blocked_ids:
        query = query.filter(
            (Conversation.type == 'community') | ~other_user_expr.in_(blocked_ids)
        )
    conversations = query.order_by(Conversation.last_message_at.desc()).limit(50).all()

    if not conversations:
        return []

    conv_ids = [c.id for c in conversations]

    unread_counts = dict(
        db.query(Message.conversation_id, func.count(Message.id))
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(
            Message.conversation_id.in_(conv_ids),
            Conversation.type != 'community',
            Message.is_read == False,
            Message.sender_id != user_id,
        )
        .group_by(Message.conversation_id)
        .all()
    )
    unread_counts.update(get_community_unread_counts(db, user_id, conv_ids))

    last_messages = []
    for conv_id in conv_ids:
        msg = db.query(Message).filter(
            Message.conversation_id == conv_id,
            visible_user_predicate(user_id, Message.sender_id),
        ).order_by(Message.created_at.desc()).first()
        if msg:
            last_messages.append(msg.to_dict())
    inject_quote_preview_batch(db, last_messages, viewer_user_id=user_id)
    redact_unavailable_post_cards_batch(db, last_messages, user_id)
    last_message_map = {msg["conversation_id"]: msg for msg in last_messages}

    all_partner_ids = set()
    for conv in conversations:
        if conv.type == 'community':
            continue
        partner_id = conv.user1_id if conv.user2_id == user_id else conv.user2_id
        if partner_id is not None:
            all_partner_ids.add(partner_id)

    partners = {}
    if all_partner_ids:
        for u in db.query(User).filter(User.id.in_(all_partner_ids)).all():
            partners[u.id] = u.to_dict()

    session_list = []
    for conv in conversations:
        if conv.type == 'community':
            session_list.append({
                'id': conv.id,
                'conversation_id': conv.id,
                'type': conv.type,
                'community_id': conv.community_id,
                'community_name': conv.community.name if conv.community else None,
                'community_avatar': conv.community.avatar_url if conv.community else None,
                'last_message': last_message_map.get(conv.id),
                'unread_count': unread_counts.get(conv.id, 0),
                'created_at': conv.created_at.isoformat() if conv.created_at else None,
                'updated_at': conv.updated_at.isoformat() if conv.updated_at else None,
            })
            continue

        partner_id = conv.user1_id if conv.user2_id == user_id else conv.user2_id
        if partner_id not in partners:
            continue
        session_list.append({
            'id': conv.id,
            'conversation_id': conv.id,
            'type': conv.type,
            'partner_id': partner_id,
            'partner': partners.get(partner_id),
            'last_message': last_message_map.get(conv.id),
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

    was_raw_ws_offline = not ws_manager.is_connected(user_id)
    was_product_online = await ws_manager.is_product_online_async(user_id)
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
            blocked_ids = excluded_user_ids(db, user_id)
            return [
                friend_id
                for friend_id in (
                    f.sender_id if f.receiver_id == user_id else f.receiver_id
                    for f in friends
                )
                if friend_id not in blocked_ids
            ]
        finally:
            db.close()

    friend_ids = await loop.run_in_executor(None, _get_friend_ids)

    # ── 会话列表 ──
    # Authentication boundaries must revalidate against the database; cached
    # sessions may predate a block created by either participant.
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
    online_friend_ids = []
    for fid in friend_ids:
        if await ws_manager.is_product_online_async(fid):
            online_friend_ids.append(fid)
    if online_friend_ids:
        await ws_manager.send_with_seq(user_id, "online_friends", {"user_ids": online_friend_ids})
        logger.info(f"[WS AUTH] uid={user_id} online_friends={online_friend_ids}")

    # ── 自动加入会话房间 ──
    def _load_convs():
        db = _get_db_session()
        try:
            from app.models.community import CommunityMember

            conversations = (
                db.query(Conversation)
                .outerjoin(ConversationParticipant)
                .outerjoin(CommunityMember, CommunityMember.community_id == Conversation.community_id)
                .filter(
                    (ConversationParticipant.user_id == user_id)
                    | (
                        (Conversation.type == 'community')
                        & (CommunityMember.user_id == user_id)
                        & (CommunityMember.status == 'active')
                    )
                )
                .all()
            )
            blocked_ids = excluded_user_ids(db, user_id)
            return [
                conversation.id
                for conversation in conversations
                if conversation.type == 'community'
                or (
                    conversation.user1_id
                    if conversation.user2_id == user_id
                    else conversation.user2_id
                ) not in blocked_ids
            ]
        finally:
            db.close()
    conv_ids = await loop.run_in_executor(None, _load_convs)
    ws_manager.set_cached_conv_ids(user_id, conv_ids)

    for cid in conv_ids:
        ws_manager.join_conversation(user_id, cid)

    if not was_product_online:
        presence_generation = ws_manager.bump_presence_generation(user_id)
        await ws_manager.notify_community_presence(user_id, True, presence_generation)
        await ws_manager._notify_friends_online(user_id, presence_generation)

    # ── 通知在线好友该用户已上线 ──
    # 产品级在线状态没有发生变化时，不重复广播 friend_online。

    return _make_response("auth_result", request_id, success=True, user_id=user_id), conn_id


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

    validation_error = _validate_send_message_payload(payload)
    if validation_error:
        await _send_error(user_id, request_id, validation_error["code"], validation_error["error"])
        return

    try:
        dedup_state = await asyncio.get_event_loop().run_in_executor(
            None,
            _get_dedup_state,
            user_id,
            client_msg_id,
        )
    except Exception as e:
        logger.warning(
            "[WS DEDUP] lookup failed uid=%s error_type=%s",
            user_id,
            type(e).__name__,
        )
        await _send_failed_ack(
            user_id,
            request_id,
            client_msg_id,
            status=503,
            code="MODERATION_UNAVAILABLE",
            retryable=True,
            message="内容审核服务暂不可用，请稍后重试",
        )
        return
    if dedup_state:
        if dedup_state["user_id"] != user_id:
            await _send_error(user_id, request_id, 403, "Cannot send message to this user")
            return
        dedup_msg_id = dedup_state["message_id"]
        if dedup_msg_id is None:
            await _send_failed_ack(
                user_id,
                request_id,
                client_msg_id,
                status=503,
                code="MODERATION_UNAVAILABLE",
                retryable=True,
                message="内容审核服务暂不可用，请稍后重试",
            )
            return
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

    def _authorize_destination():
        db = _get_db_session()
        try:
            if conversation_id:
                from app.models.models import Conversation
                conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
                if not conv:
                    return {"error": "Conversation not found"}
                if not can_access_conversation(db, conv, user_id):
                    return {"error": "Not a conversation participant"}
                participant_ids = get_active_conversation_participant_ids(db, conv)
                if conv.type != 'community' and not _can_send_to_participants(user_id, participant_ids, db=db):
                    return {"error": "Cannot send message to this user"}
            elif receiver_id and not _can_send_to_user(db, user_id, receiver_id):
                return {"error": "Cannot send message to this user"}
            return None
        finally:
            db.close()

    auth_error = await asyncio.get_event_loop().run_in_executor(None, _authorize_destination)
    if auth_error:
        await _send_error(user_id, request_id, _send_error_status(auth_error["error"]), auth_error["error"])
        return
    try:
        moderation_payload = message_text_payload_for_moderation(payload)
        if moderation_payload:
            moderate_route_fields(
                moderation_service,
                "WS send_message",
                moderation_payload,
                actor_user_id=user_id,
                is_public=False,
            )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        message = detail.get("message") or "内容审核失败"
        await _send_failed_ack(
            user_id,
            request_id,
            client_msg_id,
            status=exc.status_code,
            code=detail.get("code") or "MODERATION_UNAVAILABLE",
            retryable=bool(detail.get("retryable")),
            message=message,
        )
        return

    # 1. 持久化（与 client_msg_id 幂等记录共用事务）
    def _persist():
        db = _get_db_session()
        try:
            if conversation_id:
                conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
                if not conv:
                    return {"error": "Conversation not found"}
                if not can_access_conversation(db, conv, user_id):
                    return {"error": "Not a conversation participant"}
                participant_ids = get_active_conversation_participant_ids(db, conv)
                if conv.type != 'community' and not _can_send_to_participants(user_id, participant_ids, db=db):
                    return {"error": "Cannot send message to this user"}
            elif receiver_id:
                if not _can_send_to_user(db, user_id, receiver_id):
                    return {"error": "Cannot send message to this user"}
                conv = _get_conversation(db, user_id, receiver_id)

            participant_ids = get_active_conversation_participant_ids(db, conv)
            dedup_entry = None
            if client_msg_id:
                try:
                    from app.models.models import WSAckDedup
                    cutoff = datetime.utcnow() - timedelta(hours=24)
                    db.query(WSAckDedup).filter(
                        WSAckDedup.processed_at < cutoff,
                    ).delete(synchronize_session=False)
                    dedup_entry = db.query(WSAckDedup).filter(
                        WSAckDedup.user_id == user_id,
                        WSAckDedup.client_msg_id == client_msg_id,
                    ).first()
                    if dedup_entry:
                        if dedup_entry.message_id is None:
                            return {"dedup_unavailable": True}
                        return {"duplicate_message_id": dedup_entry.message_id}
                    dedup_entry = WSAckDedup(
                        user_id=user_id,
                        client_msg_id=client_msg_id,
                        processed_at=datetime.utcnow(),
                    )
                    db.add(dedup_entry)
                    db.flush()
                except IntegrityError as e:
                    logger.warning(
                        "[WS DEDUP] transactional reservation raced uid=%s error_type=%s",
                        user_id,
                        type(e).__name__,
                    )
                    db.rollback()
                    dedup_state = _get_dedup_state(user_id, client_msg_id)
                    if dedup_state and dedup_state["message_id"] is not None:
                        return {"duplicate_message_id": dedup_state["message_id"]}
                    return {"dedup_unavailable": True}
                except SQLAlchemyError as e:
                    logger.warning(
                        "[WS DEDUP] transactional reservation failed uid=%s error_type=%s",
                        user_id,
                        type(e).__name__,
                    )
                    db.rollback()
                    return {"dedup_unavailable": True}

            normalized = normalize_user_message_payload(
                payload,
                db,
                viewer_user_id=user_id,
                recipient_user_ids=participant_ids,
                destination_community_id=(
                    conv.community_id if conv.type == "community" else None
                ),
            )
            content = normalized["content"]
            media_url = normalized["media_url"]
            related_id = normalized["related_id"]
            message_type = normalized["message_type"]

            # 校验并生成引用预览（后端实时生成，不入库 quote_preview）
            quoted = validate_quote(db, conv.id, quote_message_id)
            from app.services.quote_service import build_quote_preview
            quote_preview = build_quote_preview(quoted)

            msg = Message(
                conversation_id=conv.id,
                sender_id=user_id,
                content=content,
                message_type=message_type,
                media_url=media_url,
                related_id=related_id,
                client_msg_id=client_msg_id,
                quote_message_id=quote_message_id if quoted else None,
                is_read=False,
            )
            db.add(msg)
            conv.last_message_at = datetime.utcnow()
            db.flush()
            if dedup_entry is not None:
                dedup_entry.message_id = msg.id
            db.commit()
            db.refresh(msg)

            participant_ids = get_active_conversation_participant_ids(db, conv)
            if conv.type == 'community':
                blocked_ids = excluded_user_ids(db, user_id)
                participant_ids = [
                    participant_id
                    for participant_id in participant_ids
                    if participant_id == user_id or participant_id not in blocked_ids
                ]
                unread_counts = {
                    pid: get_community_unread_counts(db, pid, [conv.id]).get(conv.id, 0)
                    for pid in participant_ids
                }
            else:
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

            msg_dict = inject_quote_preview(
                db,
                msg.to_dict(),
                viewer_user_id=user_id,
            )
            redact_unavailable_post_card(db, msg_dict, user_id)
            participant_messages = {user_id: msg_dict}
            for participant_id in participant_ids:
                if participant_id == user_id:
                    continue
                participant_message = inject_quote_preview(
                    db,
                    msg.to_dict(),
                    viewer_user_id=participant_id,
                )
                redact_unavailable_post_card(db, participant_message, participant_id)
                participant_messages[participant_id] = participant_message

            return {
                "conv_id": conv.id,
                "msg": msg_dict,
                "participant_ids": participant_ids,
                "participant_messages": participant_messages,
                "unread_counts": unread_counts,
            }
        except HTTPException as e:
            db.rollback()
            return {"error": e.detail, "code": e.status_code}
        except Exception as e:
            logger.error(
                "[WS SEND] uid=%s persist_error_type=%s",
                user_id,
                type(e).__name__,
            )
            db.rollback()
            return {"error": "Message send failed"}
        finally:
            db.close()

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _persist)

    if isinstance(result, dict) and "error" in result:
        await _send_error(user_id, request_id, result.get("code", _send_error_status(result["error"])), result["error"])
        return
    if isinstance(result, dict) and result.get("dedup_unavailable"):
        await _send_failed_ack(
            user_id,
            request_id,
            client_msg_id,
            status=503,
            code="MODERATION_UNAVAILABLE",
            retryable=True,
            message="内容审核服务暂不可用，请稍后重试",
        )
        return
    if isinstance(result, dict) and "duplicate_message_id" in result:
        dedup_msg_id = result["duplicate_message_id"]
        await ws_manager.send_raw(user_id, _make_response("ack", request_id,
            client_msg_id=client_msg_id, server_seq=0, message_id=dedup_msg_id,
            status=200, msg="duplicate"))
        await ws_manager.send_raw(user_id, {
            "type": "ack",
            "clientMsgId": client_msg_id,
            "client_msg_id": client_msg_id,
            "message_id": dedup_msg_id,
            "server_seq": 0,
        })
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
    # 4. 推送接收方（异步入队）
    push_items = []
    for pid in result["participant_ids"]:
        if pid != user_id:
            push_items.append((pid, "new_message", {
                **result.get("participant_messages", {}).get(pid, result["msg"]),
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

    # Chat messages belong to the conversation stream, not the interaction feed.
    # Keep an optional mobile alert without creating a Notification row.
    push_targets = _get_message_push_targets(user_id, result["participant_ids"])
    if push_targets:
        from app.services.notification_service import NotificationService
        content = result["msg"].get("content") or ""
        for pid in push_targets:
            NotificationService.push_message(
                pid,
                user_id,
                result["msg"]["id"],
                content,
                result["conv_id"],
            )

    # 5. 失效缓存
    ws_manager.invalidate_participant_caches(result["participant_ids"])


async def _handle_send_event(websocket: WebSocket, user_id: int, data: dict):
    """处理 send_event：已读、通知已读等状态事件"""
    request_id = data.get("request_id", "")
    payload = _get_payload(data)
    if request_id and not payload.get("request_id"):
        payload = {**payload, "request_id": request_id}
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
            conv = db.query(Conversation).filter(Conversation.id == msg.conversation_id).first()
            if not can_access_conversation(db, conv, user_id):
                return {"error": "Message not found", "code": 404}
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
            blocked_ids = excluded_user_ids(db, user_id)
            participant_ids = [
                participant_id
                for participant_id in participant_ids
                if participant_id == user_id or participant_id not in blocked_ids
            ]

            return {
                "msg_id": msg.id,
                "conv_id": msg.conversation_id,
                "is_recalled": True,
                "recalled_at": msg.recalled_at.isoformat() if msg.recalled_at else None,
                "participant_ids": participant_ids,
            }
        except Exception as e:
            logger.error(
                "[WS RECALL] uid=%s error_type=%s",
                user_id,
                type(e).__name__,
            )
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
        "sender_id": user_id,
        "is_recalled": True,
        "recalled_at": result["recalled_at"],
    }
    for pid in result["participant_ids"]:
        await ws_manager.send_with_seq(pid, "message_recalled", recall_data)

    # 失效缓存
    ws_manager.invalidate_participant_caches(result["participant_ids"])


async def _handle_conversation_read(websocket: WebSocket, user_id: int, payload: dict):
    """会话标记已读"""
    from app.models.models import Message, Conversation, ConversationParticipant

    conv_id = payload.get("conversation_id")
    if not conv_id:
        return

    def _mark():
        db = _get_db_session()
        try:
            conversation = db.query(Conversation).filter(Conversation.id == conv_id).first()
            if not conversation or not can_access_conversation(db, conversation, user_id):
                return {"error": "Conversation not found"}

            participant = (
                db.query(ConversationParticipant)
                .filter(
                    ConversationParticipant.conversation_id == conv_id,
                    ConversationParticipant.user_id == user_id,
                )
                .first()
            )
            if not participant:
                is_direct_member = (
                    conversation.type != 'community'
                    and user_id in (conversation.user1_id, conversation.user2_id)
                )
                if not is_direct_member:
                    return {"error": "Not a conversation participant"}
                participant = ConversationParticipant(
                    conversation_id=conversation.id,
                    user_id=user_id,
                )
                db.add(participant)
                db.flush()

            if conversation.type == 'community':
                result = mark_community_conversation_read(db, conv_id, user_id)
            else:
                result = (
                    db.query(Message)
                    .filter(Message.conversation_id == conv_id, Message.sender_id != user_id, Message.is_read == False)
                    .update({"is_read": True})
                )
            db.commit()
            participant_ids = _get_conversation_participant_ids(db, conv_id)
            blocked_ids = excluded_user_ids(db, user_id)
            participant_ids = [
                participant_id
                for participant_id in participant_ids
                if participant_id == user_id or participant_id not in blocked_ids
            ]
            return participant_ids if result > 0 else []
        finally:
            db.close()

    loop = asyncio.get_event_loop()
    participant_ids = await loop.run_in_executor(None, _mark)

    if isinstance(participant_ids, dict) and "error" in participant_ids:
        await _send_error(user_id, payload.get("request_id", ""), 403, participant_ids["error"])
        return

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
            query = visible_notification_query(db, user_id)
            if notif_ids:
                query = query.filter(Notification.id.in_(notif_ids))
            elif not mark_all:
                return None
            query.update({"is_read": True}, synchronize_session=False)
            db.commit()
            return visible_unread_count(db, user_id)
        finally:
            db.close()

    loop = asyncio.get_event_loop()
    unread_count = await loop.run_in_executor(None, _mark)
    if unread_count is not None:
        try:
            await ws_manager.send_with_seq(user_id, "notifications_read", {
                "notification_ids": notif_ids,
                "unread_count": unread_count,
            })
        except Exception as e:
            logger.warning(
                "Failed to deliver notifications_read event uid=%s error_type=%s",
                user_id,
                type(e).__name__,
            )


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
            from app.models.models import Conversation

            conversation = db.query(Conversation).filter(Conversation.id == conv_id).first()
            return can_access_conversation(db, conversation, user_id)
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

    def _can_access():
        db = _get_db_session()
        try:
            from app.models.models import Conversation

            conversation = db.query(Conversation).filter(Conversation.id == conv_id).first()
            return can_access_conversation(db, conversation, user_id)
        finally:
            db.close()

    if not await asyncio.get_event_loop().run_in_executor(None, _can_access):
        return

    event_type = "typing" if is_typing else "stop_typing"
    await ws_manager.broadcast_to_conversation(
        conv_id,
        {"type": "message", "seq": 0, "payload": {
            "event": event_type,
            "data": {"conversation_id": conv_id, "user_id": user_id}
        }},
        exclude=user_id,
        actor_user_id=user_id,
    )


async def _send_failed_ack(
    user_id: int,
    request_id: str,
    client_msg_id: str,
    *,
    status: int,
    code: str,
    retryable: bool,
    message: str,
):
    """发送终态失败 ACK（仅在 async 上下文中调用）"""
    await ws_manager.send_raw(user_id, {
        "type": "ack",
        "request_id": request_id,
        "client_msg_id": client_msg_id,
        "clientMsgId": client_msg_id,
        "status": status,
        "code": code,
        "retryable": retryable,
        "msg": message,
        "message": message,
    })


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
    logger.debug("[WS] incoming client=%s", websocket.client)
    await websocket.accept()
    user_id = None
    conn_id = None

    # 优先从 URL query 参数取 token（兜底：若客户端先发 connect 再接消息 auth，避免时序竞争）
    token_from_url = websocket.query_params.get("access_token", "")

    try:
        # ── 阶段 1: 等待 auth ──
        if token_from_url:
            token = token_from_url
            request_id = ""
            logger.info(f"[WS] token from URL query param")
        else:
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
        from app.services.moderation_errors import AppContractError
        try:
            user = verify_token(token)
        except AppContractError as e:
            await websocket.send_json(_make_response("auth_result", request_id,
                success=False, code=e.error_code.value, msg=e.public_message))
            await websocket.close(code=4001, reason=e.error_code.value)
            return
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
            logger.debug(
                "[WS RECV] uid=%s type=%s has_payload=%s",
                user_id,
                msg_type,
                isinstance(data.get("payload"), dict),
            )

            if msg_type == "auth":
                logger.debug(f"[WS AUTH] duplicate auth ignored uid={user_id}")
                continue

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
        logger.error(
            "[WS ERROR] uid=%s cid=%s error_type=%s",
            user_id,
            conn_id[:8] if conn_id else "?",
            type(e).__name__,
        )
        if user_id is not None and conn_id:
            await ws_manager.disconnect(user_id, conn_id)
