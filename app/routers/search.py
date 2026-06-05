"""
搜索路由 - FastAPI 重构版
"""
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from sqlalchemy.orm import Session
from sqlalchemy import case, or_
from datetime import datetime, timedelta

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import User, Post, Friendship, SearchHistory, Topic
from app.services.search_service import SearchService

router = APIRouter()


@router.get("/users")
def search_users(
    q: str = Query(""),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """搜索用户"""
    if not q.strip():
        return {"users": [], "total": 0, "pages": 0, "current_page": page, "per_page": per_page}

    results = SearchService.search_users(db, query=q.strip(), page=page, per_page=per_page, current_user_id=user.id)
    return results


@router.get("/posts")
def search_posts(
    q: str = Query(""),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user_id: int = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """搜索帖子"""
    if not q.strip():
        return {"posts": [], "total": 0, "pages": 0, "current_page": page, "per_page": per_page}

    results = SearchService.search_posts(db, query=q.strip(), page=page, per_page=per_page, user_id=user_id, is_public=True)
    return results


@router.get("/global")
def global_search(
    q: str = Query(""),
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """全局综合搜索"""
    if not q.strip():
        return {
            "query": "", "users": [], "posts": [],
            "user_total": 0, "post_total": 0, "total_results": 0,
            "current_page": page, "per_page": per_page,
        }
    results = SearchService.global_search(db, query=q.strip(), page=page, per_page=per_page, current_user_id=user.id)
    return results


@router.get("/hashtag/{hashtag}")
def search_by_hashtag(
    hashtag: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """按标签搜索帖子"""
    results = SearchService.search_posts_by_hashtag(db, hashtag=hashtag, page=page, per_page=per_page)
    return results


@router.get("/trending-hashtags")
def get_trending_hashtags(
    limit: int = Query(10, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取热门标签"""
    hashtags = SearchService.get_trending_hashtags(db, limit=limit)
    return {"hashtags": hashtags, "total": len(hashtags)}


@router.get("/suggest/users")
def suggest_users(
    prefix: str = Query(""),
    limit: int = Query(5, ge=1, le=20),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """用户自动补全建议"""
    if not prefix.strip():
        return {"suggestions": []}
    suggestions = SearchService.suggest_users(db, prefix=prefix.strip(), limit=limit, current_user_id=user.id)
    return {"suggestions": suggestions, "total": len(suggestions)}


@router.get("/history")
def get_search_history(
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取搜索历史"""
    histories = (
        db.query(SearchHistory)
        .filter(SearchHistory.user_id == user.id)
        .order_by(SearchHistory.created_at.desc())
        .limit(limit)
        .all()
    )
    return {"history": [h.to_dict() for h in histories], "total": len(histories)}


@router.post("/history")
def save_search_history(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """保存搜索历史"""
    query = payload.get("query", "").strip()
    search_type = payload.get("type", "global")
    if not query:
        raise HTTPException(status_code=400, detail="Query is required")

    try:
        five_minutes_ago = datetime.utcnow() - timedelta(minutes=5)
        existing = db.query(SearchHistory).filter(
            SearchHistory.user_id == user.id,
            SearchHistory.query == query,
            SearchHistory.created_at >= five_minutes_ago,
        ).first()

        if not existing:
            history = SearchHistory(user_id=user.id, query=query, search_type=search_type)
            db.add(history)

            old_records = (
                db.query(SearchHistory)
                .filter(SearchHistory.user_id == user.id)
                .order_by(SearchHistory.created_at.desc())
                .offset(100)
                .all()
            )
            for record in old_records:
                db.delete(record)

            db.commit()

        return {"message": "Search history saved", "query": query, "type": search_type}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to save search history: {str(e)}")


@router.delete("/history")
def clear_search_history(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """清空搜索历史"""
    try:
        db.query(SearchHistory).filter(SearchHistory.user_id == user.id).delete()
        db.commit()
        return {"message": "Search history cleared"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to clear search history: {str(e)}")


@router.get("/topics")
def search_topics(
    q: str = Query(""),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """搜索话题（按名称模糊匹配）"""
    if not q.strip():
        return {"topics": [], "total": 0, "pages": 0, "current_page": page, "per_page": per_page}

    query = q.strip()
    topics_query = (
        db.query(Topic)
        .filter(Topic.name.ilike(f"%{query}%"))
        .order_by(Topic.post_count.desc(), Topic.name.asc())
    )
    total = topics_query.count()
    topics = topics_query.offset((page - 1) * per_page).limit(per_page).all()
    pages = (total + per_page - 1) // per_page if total > 0 else 0

    return {
        "topics": [t.to_dict() for t in topics],
        "total": total,
        "pages": pages,
        "current_page": page,
        "per_page": per_page,
    }


@router.get("/mention-suggestions")
def get_mention_suggestions(
    prefix: str = Query(""),
    limit: int = Query(5, ge=1, le=20),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取@提及用户建议"""
    if not prefix.strip():
        return {"suggestions": []}

    from sqlalchemy import or_

    friend_ids_query = (
        db.query(
            case(
                (Friendship.sender_id == user.id, Friendship.receiver_id),
                else_=Friendship.sender_id,
            )
        )
        .filter(
            ((Friendship.sender_id == user.id) | (Friendship.receiver_id == user.id))
            & (Friendship.status == "accepted")
        )
        .subquery()
    )

    users = (
        db.query(User)
        .filter(
            or_(User.username.ilike(f"{prefix}%")),
            User.is_active == True,
            User.allow_search == True,
            User.id != user.id,
        )
        .order_by(
            User.id.in_(friend_ids_query).desc(),
            User.username.ilike(f"{prefix}%").desc(),
            User.username.asc(),
        )
        .limit(limit)
        .all()
    )

    friendships = db.query(Friendship).filter(
        ((Friendship.sender_id == user.id) | (Friendship.receiver_id == user.id))
        & (Friendship.status == "accepted")
    ).all()
    friend_ids = set()
    for f in friendships:
        friend_ids.add(f.receiver_id if f.sender_id == user.id else f.sender_id)

    suggestions = [
        {"id": u.id, "username": u.username, "full_name": u.username, "avatar_url": u.avatar_url, "is_friend": u.id in friend_ids}
        for u in users
    ]
    return {"suggestions": suggestions, "total": len(suggestions)}
