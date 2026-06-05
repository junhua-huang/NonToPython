"""
屏蔽路由 - FastAPI 重构版
"""
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import User, Block

router = APIRouter()


@router.post("/")
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
    if existing:
        return {"message": "User already blocked", "block": existing.to_dict()}

    block = Block(blocker_id=user.id, blocked_id=target_id)
    try:
        db.add(block)
        db.commit()
        return {"message": "User blocked successfully", "block": block.to_dict()}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


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
        raise HTTPException(status_code=404, detail="User is not blocked")

    try:
        db.delete(block)
        db.commit()
        return {"message": "User unblocked successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


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