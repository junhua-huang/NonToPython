"""
屏蔽路由 - FastAPI 重构版
"""
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import User, Block, Conversation, Friendship
from app.ws_manager import ws_manager

router = APIRouter()


def _internal_error() -> HTTPException:
    return HTTPException(status_code=500, detail="Internal server error")


@router.post("")
def block_user(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """屏蔽用户"""
    target_id = payload.get("user_id")
    if not target_id:
        raise HTTPException(status_code=400, detail="User ID is required")
    if target_id == user.id:
        raise HTTPException(status_code=400, detail="Cannot block yourself")

    target = db.query(User).filter(User.id == target_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    existing = db.query(Block).filter(
        Block.blocker_id == user.id, Block.blocked_id == target_id
    ).first()
    block = existing or Block(blocker_id=user.id, blocked_id=target_id)
    try:
        if existing is None:
            db.add(block)
        db.query(Friendship).filter(
            ((Friendship.sender_id == user.id) & (Friendship.receiver_id == target_id))
            | ((Friendship.sender_id == target_id) & (Friendship.receiver_id == user.id))
        ).delete(synchronize_session=False)
        db.commit()
    except IntegrityError:
        # A concurrent identical request may win the unique constraint. Treat it
        # as idempotent, then still enforce relationship cleanup transactionally.
        db.rollback()
        block = db.query(Block).filter(
            Block.blocker_id == user.id, Block.blocked_id == target_id
        ).first()
        if block is None:
            raise HTTPException(status_code=500, detail="Failed to create block")
        try:
            db.query(Friendship).filter(
                ((Friendship.sender_id == user.id) & (Friendship.receiver_id == target_id))
                | ((Friendship.sender_id == target_id) & (Friendship.receiver_id == user.id))
            ).delete(synchronize_session=False)
            db.commit()
        except Exception as cleanup_error:
            db.rollback()
            raise _internal_error()
    except Exception as e:
        db.rollback()
        raise _internal_error()

    direct_conversation_ids = [
        row.id
        for row in db.query(Conversation.id).filter(
            Conversation.type != 'community',
            ((Conversation.user1_id == user.id) & (Conversation.user2_id == target_id))
            | ((Conversation.user1_id == target_id) & (Conversation.user2_id == user.id)),
        ).all()
    ]
    for affected_user_id in (user.id, target_id):
        ws_manager.invalidate_blocked_cache(affected_user_id)
        ws_manager.invalidate_user_caches(affected_user_id)
        for conversation_id in direct_conversation_ids:
            ws_manager.leave_conversation(affected_user_id, conversation_id)
    return {
        "message": "User already blocked" if existing else "User blocked successfully",
        "block": block.to_dict(),
    }


@router.delete("/{user_id}")
def unblock_user(
    user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """取消屏蔽"""
    block = db.query(Block).filter(
        Block.blocker_id == user.id, Block.blocked_id == user_id
    ).first()
    if not block:
        return {"message": "User already unblocked"}

    try:
        db.delete(block)
        db.commit()
        for affected_user_id in (user.id, user_id):
            ws_manager.invalidate_blocked_cache(affected_user_id)
            ws_manager.invalidate_user_caches(affected_user_id)
        return {"message": "User unblocked successfully"}
    except Exception as e:
        db.rollback()
        raise _internal_error()


@router.get("/")
def get_blocked_users(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取屏蔽列表"""
    blocks_query = db.query(Block).filter(Block.blocker_id == user.id).order_by(Block.created_at.desc())
    total = blocks_query.count()
    blocks = blocks_query.offset((page - 1) * per_page).limit(per_page).all()
    pages = (total + per_page - 1) // per_page if total > 0 else 0

    return {
        "blocks": [b.to_dict() for b in blocks],
        "total": total,
        "pages": pages,
        "current_page": page,
        "per_page": per_page,
    }


@router.get("/check/{user_id}")
def check_block_status(
    user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """检查是否屏蔽了某用户"""
    block = db.query(Block).filter(
        Block.blocker_id == user.id, Block.blocked_id == user_id
    ).first()
    return {"is_blocked": block is not None}