"""
推荐路由 - FastAPI 重构版
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import User
from app.services.recommendation_service import RecommendationService

router = APIRouter()


@router.get("/feed")
def get_personalized_feed(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取个性化推荐动态"""
    page = min(page, 50)
    per_page = min(per_page, 20)
    result = RecommendationService.get_personalized_feed(
        db,
        user_id=user.id,
        page=page,
        per_page=per_page,
        cursor=cursor,
    )
    return result


@router.get("/trending")
def get_trending_posts(
    limit: int = Query(10, ge=1, le=50),
    hours: int = Query(24, ge=1, le=720),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取热门帖子"""
    result = RecommendationService.get_trending_posts(db, limit=limit, hours=hours)
    return result


@router.get("/users/suggest")
def suggest_users(
    limit: int = Query(10, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """推荐可能认识的人"""
    result = RecommendationService.get_suggested_users(db, current_user_id=user.id, limit=limit)
    return result


@router.get("/friends/recommend")
def recommend_friends(
    limit: int = Query(10, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """智能推荐好友"""
    result = RecommendationService.get_friend_recommendations(db, current_user_id=user.id, limit=limit)
    return result


@router.get("/posts/{post_id}/related")
def get_related_posts(
    post_id: int,
    limit: int = Query(5, ge=1, le=20),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取相关帖子推荐"""
    result = RecommendationService.get_related_posts(db, post_id=post_id, limit=limit)
    return result