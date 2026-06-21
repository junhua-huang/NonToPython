"""
搜索路由 - FastAPI 重构版
"""
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from sqlalchemy.orm import Session
from sqlalchemy import case, or_, func, text, bindparam
import json
from datetime import datetime, timedelta

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import (
    User, Post, Friendship, SearchHistory, Topic,
    Like, Comment, post_topics,
    ComicEvent, ComicCity, ComicEventImage, ComicEventTagRel, ComicTag, ComicEventFollow,
)
from app.services.search_service import SearchService
from app.routers.comic import _recalc_status_text

router = APIRouter()


def _is_short_query(q: str) -> bool:
    return len((q or "").strip()) < 2


# ============================================================
# 特殊关键词处理
# ============================================================

def _handle_special_query(db: Session, query: str, page: int, per_page: int, user_id: int):
    """检测特殊搜索关键词，返回对应数据；不匹配则返回 None"""

    if query in ("热门帖子", "热门贴子"):
        return _hot_posts(db, page, per_page)

    if query in ("话题趋势", "热门话题"):
        return _trending_topics(db, page, per_page)

    if query in ("近期漫展", "最近漫展"):
        return _comic_events(db, page, per_page, user_id)

    return None


def _hot_posts(db: Session, page: int, per_page: int):
    """7天内热度最高的帖子 Top 10
    热度 = 浏览量 + 点赞数×3 + 评论数×2
    """
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    rows = db.execute(text("""
        SELECT p.id, p.content, p.images, p.video_url, p.post_type,
               p.user_id, p.created_at, p.updated_at, p.visibility,
               p.is_public, p.view_count,
               COALESCE(l.like_count, 0) AS like_count,
               COALESCE(c.comment_count, 0) AS comment_count,
               (p.view_count + COALESCE(l.like_count, 0) * 3 + COALESCE(c.comment_count, 0) * 2) AS hot_score
        FROM posts p
        LEFT JOIN (
            SELECT post_id, COUNT(*) AS like_count FROM likes GROUP BY post_id
        ) l ON p.id = l.post_id
        LEFT JOIN (
            SELECT post_id, COUNT(*) AS comment_count FROM comments GROUP BY post_id
        ) c ON p.id = c.post_id
        WHERE p.created_at >= :since AND p.is_public = 1
        ORDER BY hot_score DESC
        LIMIT :limit
    """), {"since": seven_days_ago, "limit": per_page}).fetchall()

    # 批量取用户信息
    user_ids = {r.user_id for r in rows}
    users_map = {}
    if user_ids:
        users_map = {u.id: u.to_dict() for u in db.query(User).filter(User.id.in_(user_ids)).all()}

    posts = []
    for r in rows:
        images = r.images
        if isinstance(images, str):
            try:
                images = json.loads(images)
            except (json.JSONDecodeError, TypeError):
                images = []
        posts.append({
            "id": r.id,
            "content": r.content,
            "images": images,
            "video_url": r.video_url,
            "post_type": r.post_type,
            "user_id": r.user_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "visibility": r.visibility,
            "is_public": r.is_public,
            "view_count": r.view_count,
            "like_count": r.like_count,
            "comment_count": r.comment_count,
            "hot_score": int(r.hot_score) if r.hot_score else 0,
            "author": users_map.get(r.user_id),
        })

    return {
        "query": "热门帖子",
        "type": "hot_posts",
        "period": "7天",
        "posts": posts,
        "total": len(posts),
        "current_page": page,
        "per_page": per_page,
    }


def _trending_topics(db: Session, page: int, per_page: int):
    """7天内热门话题 Top 10
    按话题在近7天帖子中出现次数排序
    """
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    rows = db.execute(text("""
        SELECT t.id, t.name, t.description, t.icon_url, t.color,
               t.post_count, t.follower_count, t.is_trending,
               t.created_at, t.updated_at,
               COUNT(pt.post_id) AS recent_post_count
        FROM topics t
        JOIN post_topics pt ON t.id = pt.topic_id
        JOIN posts p ON pt.post_id = p.id
        WHERE p.created_at >= :since AND p.is_public = 1
        GROUP BY t.id, t.name, t.description, t.icon_url, t.color,
                 t.post_count, t.follower_count, t.is_trending,
                 t.created_at, t.updated_at
        ORDER BY recent_post_count DESC
        LIMIT :limit
    """), {"since": seven_days_ago, "limit": per_page}).fetchall()

    topics = []
    for r in rows:
        topics.append({
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "icon_url": r.icon_url,
            "color": r.color,
            "post_count": r.post_count,
            "follower_count": r.follower_count,
            "is_trending": r.is_trending,
            "recent_post_count": r.recent_post_count,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        })

    return {
        "query": "话题趋势",
        "type": "trending_topics",
        "period": "7天",
        "topics": topics,
        "total": len(topics),
        "current_page": page,
        "per_page": per_page,
    }


def _comic_events(db: Session, page: int, per_page: int, user_id: int):
    """近期3个月漫展列表（含进行中 + 即将开始）"""
    now = datetime.utcnow()
    three_months_later = now + timedelta(days=90)

    offset = (page - 1) * per_page
    events = (
        db.query(ComicEvent)
        .filter(
            ComicEvent.end_date >= now,
            ComicEvent.start_date <= three_months_later,
        )
        .order_by(ComicEvent.start_date.asc())
        .offset(offset)
        .limit(per_page)
        .all()
    )

    # 批量取关联数据
    event_ids = [e.id for e in events]
    city_map = {}
    images_map: dict = {}
    tags_map: dict = {}
    follow_set: set = set()
    follow_count_map: dict = {}
    creator_map: dict = {}

    if event_ids:
        cities = db.query(ComicCity).filter(ComicCity.id.in_({e.city_id for e in events})).all()
        city_map = {c.id: c.name for c in cities}

        imgs = db.query(ComicEventImage).filter(
            ComicEventImage.event_id.in_(event_ids)
        ).order_by(ComicEventImage.is_cover.desc(), ComicEventImage.sort_order.asc()).all()
        for img in imgs:
            images_map.setdefault(img.event_id, []).append({
                "id": img.id, "imageUrl": img.image_url,
                "isCover": bool(img.is_cover), "sortOrder": img.sort_order,
            })

        tag_rels = db.query(ComicEventTagRel).filter(
            ComicEventTagRel.event_id.in_(event_ids)
        ).all()
        tag_ids = {tr.tag_id for tr in tag_rels}
        tag_name_map = {}
        if tag_ids:
            tag_name_map = {t.id: t.name for t in db.query(ComicTag).filter(ComicTag.id.in_(tag_ids)).all()}
        for tr in tag_rels:
            tags_map.setdefault(tr.event_id, []).append(tag_name_map.get(tr.tag_id, ""))

        # 关注数
        follow_counts = db.execute(text(
            "SELECT event_id, COUNT(*) FROM comic_event_follows "
            "WHERE event_id IN :eids GROUP BY event_id"
        ).bindparams(bindparam('eids', expanding=True)), {"eids": event_ids}).fetchall()
        for fc in follow_counts:
            follow_count_map[fc[0]] = fc[1]

        # 创建者
        creator_ids = {e.creator_id for e in events}
        creators = db.query(User).filter(User.id.in_(creator_ids)).all()
        creator_map = {u.id: {"username": u.username, "avatarUrl": u.avatar_url} for u in creators}

        if user_id:
            follows = db.query(ComicEventFollow).filter(
                ComicEventFollow.event_id.in_(event_ids),
                ComicEventFollow.user_id == user_id,
            ).all()
            follow_set = {f.event_id for f in follows}

    status_text = {0: "即将开始", 1: "进行中", 2: "已结束"}
    result = []
    for e in events:
        creator = creator_map.get(e.creator_id, {})
        # 实时重算状态：数据库里的 status 是创建时算的，不会随时间变化，
        # 必须按当前日期重新计算，否则已结束的漫展会一直显示"即将开始"。
        real_status, real_status_text = _recalc_status_text(e.start_date, e.end_date)
        result.append({
            "id": e.id,
            "name": e.name,
            "cityName": city_map.get(e.city_id, ""),
            "venue": e.venue or "",
            "startDate": e.start_date.isoformat() if e.start_date else None,
            "endDate": e.end_date.isoformat() if e.end_date else None,
            "status": real_status,
            "statusText": real_status_text,
            "ticketInfo": e.ticket_info or "",
            "tags": tags_map.get(e.id, []),
            "images": images_map.get(e.id, []),
            "isFollowed": e.id in follow_set,
            "followCount": follow_count_map.get(e.id, 0),
            "creatorName": creator.get("username", ""),
            "creatorAvatar": creator.get("avatarUrl", ""),
            "createdAt": e.created_at.isoformat() if e.created_at else None,
        })

    return {
        "query": "近期漫展",
        "type": "comic_events",
        "period": "3个月",
        "events": result,
        "total": len(result),
        "current_page": page,
        "per_page": per_page,
    }


# ============================================================
# 路由定义
# ============================================================


@router.get("/users")
def search_users(
    q: str = Query(""),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """搜索用户"""
    if _is_short_query(q):
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
    if _is_short_query(q):
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
    """全局综合搜索（含特殊关键词：热门帖子 / 话题趋势 / 近期漫展）"""
    per_page = min(per_page, 10)
    if _is_short_query(q):
        return {
            "query": "", "users": [], "posts": [], "events": [],
            "user_total": 0, "post_total": 0, "event_total": 0, "total_results": 0,
            "current_page": page, "per_page": per_page,
        }

    query = q.strip()
    special = _handle_special_query(db, query, page, per_page, user.id)
    if special is not None:
        return special

    results = SearchService.global_search(db, query=query, page=page, per_page=per_page, current_user_id=user.id)
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
    if _is_short_query(q):
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
