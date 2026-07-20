"""
好友路由 - FastAPI 重构版
"""
import asyncio
import random
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import User, Friendship, Conversation, ConversationParticipant, Message
from app.services.notification_service import NotificationService
from app.services.block_service import excluded_user_ids, has_block_between, visible_user_predicate
from app.ws_manager import ws_manager

router = APIRouter()


@router.post("/request")
def send_friend_request(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """发送好友请求"""
    current_user_id = user.id
    receiver_id = payload.get("receiver_id")
    if not receiver_id:
        raise HTTPException(status_code=400, detail="Receiver ID is required")

    if current_user_id == receiver_id:
        raise HTTPException(status_code=400, detail="Cannot add yourself as a friend")

    receiver = db.query(User).filter(User.id == receiver_id).first()
    if not receiver or has_block_between(db, current_user_id, receiver_id):
        raise HTTPException(status_code=404, detail="User not found")

    if receiver.allow_friend_requests == "none":
        raise HTTPException(status_code=403, detail="This user does not accept friend requests")
    if receiver.allow_friend_requests == "friends_of_friends":
        sender_friends = set(
            f.receiver_id if f.sender_id == current_user_id else f.sender_id
            for f in db.query(Friendship).filter(
                ((Friendship.sender_id == current_user_id) | (Friendship.receiver_id == current_user_id)),
                Friendship.status == "accepted",
            ).all()
        )
        receiver_friends = set(
            f.receiver_id if f.sender_id == receiver_id else f.sender_id
            for f in db.query(Friendship).filter(
                ((Friendship.sender_id == receiver_id) | (Friendship.receiver_id == receiver_id)),
                Friendship.status == "accepted",
            ).all()
        )
        if not sender_friends.intersection(receiver_friends):
            raise HTTPException(status_code=403, detail="Friend requests are limited to friends of friends")

    existing = db.query(Friendship).filter(
        ((Friendship.sender_id == current_user_id) & (Friendship.receiver_id == receiver_id))
        | ((Friendship.sender_id == receiver_id) & (Friendship.receiver_id == current_user_id))
    ).first()
    if existing:
        if existing.status == "accepted":
            return {"message": "You are already friends", "status": existing.status, "friendship": existing.to_dict()}

        # 对方已发来 pending → 双向确认，直接通过为好友
        if existing.sender_id != current_user_id and existing.status == "pending":
            existing.status = "accepted"
            existing.updated_at = datetime.utcnow()

            # 创建 1v1 会话 + 发送 Hi
            uid1, uid2 = min(existing.sender_id, existing.receiver_id), max(existing.sender_id, existing.receiver_id)
            conversation = db.query(Conversation).filter(
                Conversation.user1_id == uid1, Conversation.user2_id == uid2
            ).first()
            if not conversation:
                conversation = Conversation(user1_id=uid1, user2_id=uid2)
                db.add(conversation)
                db.flush()
                db.add_all([
                    ConversationParticipant(conversation_id=conversation.id, user_id=uid1),
                    ConversationParticipant(conversation_id=conversation.id, user_id=uid2),
                ])

            now = datetime.utcnow()
            hi_sender = Message(conversation_id=conversation.id, sender_id=existing.sender_id, content="Hi", created_at=now)
            hi_receiver = Message(conversation_id=conversation.id, sender_id=existing.receiver_id, content="Hi", created_at=now)
            db.add_all([hi_sender, hi_receiver])
            conversation.last_message_at = now

            db.commit()
            db.refresh(conversation)
            db.refresh(hi_sender)
            db.refresh(hi_receiver)

            NotificationService.notify_friend_accepted(existing.sender_id, existing.receiver_id)

            _schedule_hi_push(
                existing.sender_id, existing.receiver_id,
                conversation.id, conversation.to_dict(),
                hi_sender.to_dict(), hi_receiver.to_dict()
            )

            return {"message": "Friend request accepted (mutual)", "status": existing.status, "friendship": existing.to_dict()}

        # 我已发过（pending 或 rejected）→ 重置为 pending，重新发通知
        existing.status = "pending"
        existing.updated_at = datetime.utcnow()
        db.commit()
        NotificationService.notify_friend_request(receiver_id, current_user_id)
        return {"message": "Friend request re-sent", "status": existing.status, "friendship": existing.to_dict()}

    friendship = Friendship(sender_id=current_user_id, receiver_id=receiver_id, status="pending")
    try:
        db.add(friendship)
        db.commit()
        NotificationService.notify_friend_request(receiver_id, current_user_id)
        return {"message": "Friend request sent successfully", "friendship": friendship.to_dict()}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/request/{request_id}/accept")
def accept_friend_request(
    request_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """接受好友请求"""
    friendship = db.query(Friendship).filter(Friendship.id == request_id).first()
    if not friendship or has_block_between(db, friendship.sender_id, friendship.receiver_id):
        raise HTTPException(status_code=404, detail="Friend request not found")
    if friendship.receiver_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")
    if friendship.status != "pending":
        raise HTTPException(status_code=400, detail="Request is not pending")

    friendship.status = "accepted"
    try:
        # --- 创建或获取双方 1v1 会话 ---
        uid1, uid2 = min(friendship.sender_id, friendship.receiver_id), max(friendship.sender_id, friendship.receiver_id)
        conversation = db.query(Conversation).filter(
            Conversation.user1_id == uid1, Conversation.user2_id == uid2
        ).first()
        if not conversation:
            conversation = Conversation(user1_id=uid1, user2_id=uid2)
            db.add(conversation)
            db.flush()
            db.add_all([
                ConversationParticipant(conversation_id=conversation.id, user_id=uid1),
                ConversationParticipant(conversation_id=conversation.id, user_id=uid2),
            ])

        # --- 双方各自向会话发送一条 "Hi" ---
        now = datetime.utcnow()
        hi_sender = Message(conversation_id=conversation.id, sender_id=friendship.sender_id, content="Hi", created_at=now)
        hi_receiver = Message(conversation_id=conversation.id, sender_id=friendship.receiver_id, content="Hi", created_at=now)
        db.add_all([hi_sender, hi_receiver])
        conversation.last_message_at = now

        # 整个操作（更新 friendship + 创建会话/参与者 + 写入消息）在同一个事务中完成
        db.commit()
        db.refresh(conversation)
        db.refresh(hi_sender)
        db.refresh(hi_receiver)

        # --- 通知 ---
        NotificationService.notify_friend_accepted(friendship.sender_id, user.id)

        # --- WebSocket 推送 Hi 给双方 ---
        _schedule_hi_push(
            friendship.sender_id, friendship.receiver_id,
            conversation.id, conversation.to_dict(),
            hi_sender.to_dict(), hi_receiver.to_dict()
        )

        return {"message": "Friend request accepted", "friendship": friendship.to_dict()}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/request/{request_id}/reject")
def reject_friend_request(
    request_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """拒绝好友请求"""
    friendship = db.query(Friendship).filter(Friendship.id == request_id).first()
    if not friendship or has_block_between(db, friendship.sender_id, friendship.receiver_id):
        raise HTTPException(status_code=404, detail="Friend request not found")
    if friendship.receiver_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")
    if friendship.status != "pending":
        raise HTTPException(status_code=400, detail="Request is not pending")

    friendship.status = "rejected"
    try:
        db.commit()
        return {"message": "Friend request rejected", "friendship": friendship.to_dict()}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/request/{request_id}")
def cancel_friend_request(
    request_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """取消好友请求"""
    friendship = db.query(Friendship).filter(Friendship.id == request_id).first()
    if not friendship or has_block_between(db, friendship.sender_id, friendship.receiver_id):
        raise HTTPException(status_code=404, detail="Friend request not found")
    if friendship.sender_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")
    if friendship.status != "pending":
        raise HTTPException(status_code=400, detail="Request is not pending")

    try:
        db.delete(friendship)
        db.commit()
        return {"message": "Friend request cancelled"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/")
def get_friends(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取好友列表"""
    blocked_ids = excluded_user_ids(db, user.id)
    friendships = db.query(Friendship).filter(
        ((Friendship.sender_id == user.id) | (Friendship.receiver_id == user.id))
        & (Friendship.status == "accepted")
    )
    if blocked_ids:
        friendships = friendships.filter(
            ~Friendship.sender_id.in_(blocked_ids),
            ~Friendship.receiver_id.in_(blocked_ids),
        )
    friendships = friendships.all()

    friends = []
    for f in friendships:
        friends.append(f.receiver.to_dict() if f.sender_id == user.id else f.sender.to_dict())

    return {"friends": friends, "total": len(friends)}


@router.get("/requests/pending")
def get_pending_requests(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取待处理的好友请求"""
    blocked_ids = excluded_user_ids(db, user.id)
    received_query = db.query(Friendship).filter(
        Friendship.receiver_id == user.id, Friendship.status == "pending"
    )
    sent_query = db.query(Friendship).filter(
        Friendship.sender_id == user.id, Friendship.status == "pending"
    )
    if blocked_ids:
        received_query = received_query.filter(~Friendship.sender_id.in_(blocked_ids))
        sent_query = sent_query.filter(~Friendship.receiver_id.in_(blocked_ids))
    received = received_query.all()
    sent = sent_query.all()

    return {
        "received": [r.to_dict() for r in received],
        "sent": [s.to_dict() for s in sent],
        "received_count": len(received),
        "sent_count": len(sent),
    }


@router.get("/requests/sent")
def get_sent_requests(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取我发起的好友申请列表（含对方用户信息）"""
    sent = db.query(Friendship).filter(
        Friendship.sender_id == user.id,
        Friendship.status == "pending",
        visible_user_predicate(user.id, Friendship.receiver_id),
    ).order_by(Friendship.created_at.desc()).all()

    results = []
    for f in sent:
        item = f.to_dict()
        item["user"] = f.receiver.to_dict() if f.receiver else None
        results.append(item)

    return {
        "requests": results,
        "total": len(results),
    }


@router.get("/requests/received")
def get_received_requests(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取向我发起的好友申请列表（含对方用户信息）"""
    received = db.query(Friendship).filter(
        Friendship.receiver_id == user.id,
        Friendship.status == "pending",
        visible_user_predicate(user.id, Friendship.sender_id),
    ).order_by(Friendship.created_at.desc()).all()

    results = []
    for f in received:
        item = f.to_dict()
        item["user"] = f.sender.to_dict() if f.sender else None
        results.append(item)

    return {
        "requests": results,
        "total": len(results),
    }


@router.delete("/{user_id}")
def remove_friend(
    user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除好友"""
    friendship = db.query(Friendship).filter(
        ((Friendship.sender_id == user.id) & (Friendship.receiver_id == user_id))
        | ((Friendship.sender_id == user_id) & (Friendship.receiver_id == user.id))
        & (Friendship.status == "accepted")
    ).first()
    if not friendship:
        raise HTTPException(status_code=404, detail="Friendship not found")

    try:
        db.delete(friendship)
        db.commit()
        return {"message": "Friend removed successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{user_id}")
def check_friendship_status(
    user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """检查与某用户的好友关系状态"""
    if has_block_between(db, user.id, user_id):
        return {"status": "none"}
    friendship = db.query(Friendship).filter(
        ((Friendship.sender_id == user.id) & (Friendship.receiver_id == user_id))
        | ((Friendship.sender_id == user_id) & (Friendship.receiver_id == user.id))
    ).first()
    if not friendship:
        return {"status": "none"}
    return {"status": friendship.status, "friendship": friendship.to_dict()}


@router.get("/count/{user_id}")
def get_user_friend_count(
    user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取指定用户的好友数量"""
    target = db.query(User).filter(User.id == user_id).first()
    if not target or has_block_between(db, user.id, user_id):
        raise HTTPException(status_code=404, detail="User not found")

    count = db.query(Friendship).filter(
        ((Friendship.sender_id == user_id) | (Friendship.receiver_id == user_id))
        & (Friendship.status == "accepted")
    ).count()
    return {"user_id": user_id, "friend_count": count}


@router.get("/recommendations")
def get_friend_recommendations(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取好友推荐：随机推荐 10 个非好友用户"""
    friendships = db.query(Friendship).filter(
        (Friendship.sender_id == user.id) | (Friendship.receiver_id == user.id)
    ).all()

    related_ids = set()
    for f in friendships:
        related_ids.add(f.receiver_id if f.sender_id == user.id else f.sender_id)
    related_ids.add(user.id)

    candidates = db.query(User).filter(
        User.id.notin_(related_ids),
        User.is_active == True,
        visible_user_predicate(user.id, User.id),
    ).all()

    if not candidates:
        return {"recommendations": [], "total": 0}

    sample_size = min(10, len(candidates))
    recommended = random.sample(candidates, sample_size)

    return {
        "recommendations": [
            {"id": u.id, "username": u.username, "avatar_url": u.avatar_url}
            for u in recommended
        ],
        "total": len(recommended),
    }


def _schedule_hi_push(sender_id: int, receiver_id: int, conv_id: int, conv_dict: dict, hi_sender_dict: dict, hi_receiver_dict: dict):
    """调度异步 WebSocket 推送：向双方推送新会话 + Hi 消息"""
    async def _push():
        conv_for_sender = {**conv_dict, "other_user_id": receiver_id}
        await ws_manager.send_raw(sender_id, {
            "type": "friend_accepted_chat",
            "conversation": conv_for_sender,
            "message": hi_receiver_dict,
        })
        conv_for_receiver = {**conv_dict, "other_user_id": sender_id}
        await ws_manager.send_raw(receiver_id, {
            "type": "friend_accepted_chat",
            "conversation": conv_for_receiver,
            "message": hi_sender_dict,
        })

    ws_manager.schedule_push(_push())
