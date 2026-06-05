"""
管理员路由 - FastAPI 重构版
"""
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, admin_required
from app.models.models import User, SensitiveWord

router = APIRouter()


@router.get("/sensitive-words")
def get_sensitive_words(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    user: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    """获取敏感词列表"""
    words_query = db.query(SensitiveWord).order_by(SensitiveWord.created_at.desc())
    total = words_query.count()
    words = words_query.offset((page - 1) * per_page).limit(per_page).all()
    pages = (total + per_page - 1) // per_page if total > 0 else 0

    return {
        "words": [w.to_dict() for w in words],
        "total": total,
        "pages": pages,
        "current_page": page,
        "per_page": per_page,
    }


@router.post("/sensitive-words")
def add_sensitive_word(
    payload: dict = Body(...),
    user: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    """添加敏感词"""
    word = payload.get("word", "").strip()
    if not word:
        raise HTTPException(status_code=400, detail="Word is required")

    existing = db.query(SensitiveWord).filter(SensitiveWord.word == word).first()
    if existing:
        return {"message": "Word already exists", "word": existing.to_dict()}

    sensitive = SensitiveWord(word=word)
    try:
        db.add(sensitive)
        db.commit()
        return {"message": "Sensitive word added", "word": sensitive.to_dict()}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sensitive-words/{word_id}")
def delete_sensitive_word(
    word_id: int,
    user: User = Depends(admin_required),
    db: Session = Depends(get_db),
):
    """删除敏感词"""
    word = db.query(SensitiveWord).filter(SensitiveWord.id == word_id).first()
    if not word:
        raise HTTPException(status_code=404, detail="Sensitive word not found")

    try:
        db.delete(word)
        db.commit()
        return {"message": "Sensitive word deleted"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))