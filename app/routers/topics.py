"""
话题路由 - FastAPI 重构版
"""
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_content_manager
from app.models.models import User, Topic, topic_followers
from app.services.topic_service import TopicService

router = APIRouter()


@router.get("/")
def get_topics(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    q: str = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取所有话题列表"""
    results = TopicService.get_all_topics(db, page=page, per_page=per_page, search_query=q, current_user_id=user.id)
    return results


@router.get("/trending")
def get_trending_topics(
    limit: int = Query(10, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取热门话题"""
    trending = TopicService.get_trending_topics(db, limit=limit)
    topic_ids = [topic["id"] for topic in trending]
    followed_topic_ids = set()
    if topic_ids:
        followed_rows = (
            db.query(topic_followers.c.topic_id)
            .filter(
                topic_followers.c.user_id == user.id,
                topic_followers.c.topic_id.in_(topic_ids),
            )
            .all()
        )
        followed_topic_ids = {row[0] for row in followed_rows}

    topics_with_follow = []
    for topic in trending:
        topic_data = dict(topic)
        topic_data["is_following"] = topic["id"] in followed_topic_ids
        topics_with_follow.append(topic_data)
    return {"topics": topics_with_follow, "total": len(trending)}


@router.get("/followed")
def get_followed_topics(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取用户关注的话题列表"""
    results = TopicService.get_user_followed_topics(db, user_id=user.id, page=page, per_page=per_page)
    return results


@router.get("/my-referenced")
def get_my_referenced_topics(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取我引用过的话题（我发布的帖子中引用的话题）"""
    from sqlalchemy import text
    offset = (page - 1) * per_page

    total = db.execute(
        text('''SELECT COUNT(DISTINCT t.id) FROM topics t
            JOIN post_topics pt ON t.id = pt.topic_id
            JOIN posts p ON pt.post_id = p.id
            WHERE p.user_id = :user_id'''),
        {'user_id': user.id}
    ).scalar() or 0

    result = db.execute(
        text('''SELECT DISTINCT t.id, MAX(p.created_at) as last_ref_at FROM topics t
            JOIN post_topics pt ON t.id = pt.topic_id
            JOIN posts p ON pt.post_id = p.id
            WHERE p.user_id = :user_id
            GROUP BY t.id
            ORDER BY last_ref_at DESC
            LIMIT :limit OFFSET :offset'''),
        {'user_id': user.id, 'limit': per_page, 'offset': offset}
    )
    rows = result.fetchall()
    topic_ids = [row[0] for row in rows]
    topics = db.query(Topic).filter(Topic.id.in_(topic_ids)).all() if topic_ids else []
    topic_map = {t.id: t for t in topics}
    ordered = [topic_map[tid] for tid in topic_ids if tid in topic_map]

    return {
        'topics': [t.to_dict() for t in ordered],
        'total': total,
        'pages': (total + per_page - 1) // per_page if total > 0 else 0,
        'current_page': page,
        'per_page': per_page,
    }


@router.get("/suggest")
def suggest_topics(
    prefix: str = Query(""),
    limit: int = Query(5, ge=1, le=20),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """话题自动补全建议"""
    if not prefix.strip():
        return {"suggestions": []}

    topics = (
        db.query(Topic)
        .filter(Topic.name.ilike(f"{prefix}%"))
        .order_by(Topic.follower_count.desc())
        .limit(limit)
        .all()
    )

    suggestions = [
        {"id": t.id, "name": t.name, "post_count": t.post_count, "follower_count": t.follower_count}
        for t in topics
    ]
    return {"suggestions": suggestions, "total": len(suggestions)}


@router.get("/{topic_id}")
def get_topic(
    topic_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取单个话题详情"""
    topic = TopicService.get_topic_by_id(db, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    is_following = TopicService.is_user_following_topic(db, user.id, topic_id)
    topic_data = topic.to_dict()
    topic_data["is_following"] = is_following
    return topic_data


@router.get("/name/{topic_name}")
def get_topic_by_name(
    topic_name: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """根据名称获取话题"""
    topic = TopicService.get_topic_by_name(db, topic_name)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    is_following = TopicService.is_user_following_topic(db, user.id, topic.id)
    topic_data = topic.to_dict()
    topic_data["is_following"] = is_following
    return topic_data


@router.get("/{topic_id}/posts")
def get_topic_posts(
    topic_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取话题下的帖子"""
    results = TopicService.get_topic_posts(db, topic_id=topic_id, page=page, per_page=per_page)
    return results


@router.post("")
def create_topic(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建新话题"""
    name = payload.get("name", "").strip()
    description = payload.get("description", "")
    icon_url = payload.get("icon_url", "")
    color = payload.get("color", "#3b82f6")

    if not name:
        raise HTTPException(status_code=400, detail="Topic name is required")

    existing = TopicService.get_topic_by_name(db, name)
    if existing:
        return {"message": "Topic already exists", "topic": existing.to_dict()}

    topic = TopicService.create_topic(db, name=name, description=description, icon_url=icon_url, color=color)
    if not topic:
        raise HTTPException(status_code=500, detail="Failed to create topic")

    return {"message": "Topic created successfully", "topic": topic.to_dict()}


@router.post("/{topic_id}/follow")
def follow_topic(
    topic_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """关注话题"""
    topic = TopicService.get_topic_by_id(db, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")

    success = TopicService.follow_topic(db, user.id, topic_id)
    if success:
        return {"message": "Successfully followed topic", "topic": topic.to_dict()}
    else:
        raise HTTPException(status_code=500, detail="Failed to follow topic")


@router.post("/{topic_id}/unfollow")
def unfollow_topic(
    topic_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """取消关注话题"""
    topic = TopicService.get_topic_by_id(db, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")

    success = TopicService.unfollow_topic(db, user.id, topic_id)
    if success:
        return {"message": "Successfully unfollowed topic"}
    else:
        raise HTTPException(status_code=500, detail="Failed to unfollow topic")


@router.put("/{topic_id}")
def update_topic(
    topic_id: int,
    payload: dict = Body(...),
    user: User = Depends(require_content_manager),
    db: Session = Depends(get_db),
):
    """更新话题信息（管理员/组织者功能）"""
    topic = db.query(Topic).filter(Topic.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")

    if "description" in payload:
        topic.description = payload["description"]
    if "icon_url" in payload:
        topic.icon_url = payload["icon_url"]
    if "color" in payload:
        topic.color = payload["color"]

    try:
        db.commit()
        return {"message": "Topic updated successfully", "topic": topic.to_dict()}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{topic_id}")
def delete_topic(
    topic_id: int,
    user: User = Depends(require_content_manager),
    db: Session = Depends(get_db),
):
    """删除话题（管理员/组织者功能）"""
    topic = db.query(Topic).filter(Topic.id == topic_id).first()
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")

    try:
        from sqlalchemy import text
        db.execute(text("DELETE FROM post_topics WHERE topic_id = :topic_id"), {"topic_id": topic_id})
        db.execute(text("DELETE FROM topic_followers WHERE topic_id = :topic_id"), {"topic_id": topic_id})
        db.delete(topic)
        db.commit()
        return {"message": "Topic deleted successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

