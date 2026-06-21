"""
互动路由 - FastAPI 重构版
"""
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import User, Post, Comment, Like
from app.services.notification_service import NotificationService
from app.services.mention_service import MentionService

router = APIRouter()


def _enrich_comment_dict(comment: Comment, db: Session, current_user_id: int) -> dict:
    """给评论 dict 附加 is_liked（reply_count/like_count/reply_to_user 优先读取模型字段）"""
    d = comment.to_dict(current_user_id=current_user_id)
    d['is_liked'] = db.query(Like).filter(
        Like.user_id == current_user_id, Like.comment_id == comment.id
    ).first() is not None
    return d


def _batch_enrich_comments(db: Session, comments: list, current_user_id: int) -> list:
    """批量 enrichment：1 次 IN 查询 is_liked，reply_count/like_count/reply_to_user 直接读模型字段"""
    if not comments:
        return []

    comment_ids = [c.id for c in comments]

    # 批量查询 is_liked：1 次 SQL
    liked_set: set = set()
    if current_user_id and comment_ids:
        liked_set = {
            row[0] for row in
            db.query(Like.comment_id)
            .filter(Like.comment_id.in_(comment_ids), Like.user_id == current_user_id)
            .all()
        }

    result = []
    for c in comments:
        d = c.to_dict(current_user_id=current_user_id)
        d['is_liked'] = c.id in liked_set
        result.append(d)
    return result


# ==================== 点赞功能 ====================


@router.post("/posts/{post_id}/like")
def like_post(
    post_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """点赞帖子"""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    existing = db.query(Like).filter(
        Like.user_id == user.id,
        Like.post_id == post_id,
        Like.comment_id.is_(None),
    ).first()
    if existing:
        return {"message": "Post already liked", "like_count": post.get_like_count()}

    like = Like(user_id=user.id, post_id=post_id)
    try:
        db.add(like)
        db.commit()
        NotificationService.notify_like(post.user_id, user.id, post_id, db=db)
        return {"message": "Post liked successfully", "liked": True, "like_count": post.get_like_count()}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/posts/{post_id}/like")
def unlike_post(
    post_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """取消点赞"""
    like = db.query(Like).filter(
        Like.user_id == user.id,
        Like.post_id == post_id,
        Like.comment_id.is_(None),
    ).first()
    if not like:
        raise HTTPException(status_code=404, detail="You have not liked this post")

    try:
        db.delete(like)
        db.commit()
        post = db.query(Post).filter(Post.id == post_id).first()
        return {"message": "Like removed successfully", "liked": False, "like_count": post.get_like_count()}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/posts/{post_id}/likes")
def get_post_likes(
    post_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取帖子的点赞列表"""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    likes = db.query(Like).filter(
        Like.post_id == post_id,
        Like.comment_id.is_(None),
    ).all()
    return {"likes": [l.to_dict() for l in likes], "total": len(likes)}


# ==================== 评论点赞 ====================


@router.post("/comments/{comment_id}/like")
def like_comment(
    comment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """点赞评论"""
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    existing = db.query(Like).filter(
        Like.user_id == user.id, Like.comment_id == comment_id
    ).first()
    if existing:
        return {"message": "Comment already liked", "comment_id": comment_id, "like_count": comment.like_count}

    like = Like(user_id=user.id, comment_id=comment_id)
    try:
        db.add(like)
        comment.like_count += 1
        db.commit()
        return {"message": "Comment liked successfully", "comment_id": comment_id, "liked": True, "like_count": comment.like_count}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/comments/{comment_id}/like")
def unlike_comment(
    comment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """取消评论点赞"""
    like = db.query(Like).filter(
        Like.user_id == user.id, Like.comment_id == comment_id
    ).first()
    if not like:
        raise HTTPException(status_code=404, detail="You have not liked this comment")

    try:
        db.delete(like)
        comment = db.query(Comment).filter(Comment.id == comment_id).first()
        if comment and comment.like_count > 0:
            comment.like_count -= 1
        db.commit()
        return {"message": "Like removed successfully", "comment_id": comment_id, "liked": False, "like_count": comment.like_count if comment else 0}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 评论功能 ====================


@router.post("/posts/{post_id}/comments")
def create_comment(
    post_id: int,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建评论"""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # 屏蔽检查：存在双向屏蔽关系则禁止评论
    if user.id != post.user_id:
        from app.ws_manager import ws_manager
        if user.id in ws_manager.get_blocked_user_ids(post.user_id) or \
           post.user_id in ws_manager.get_blocked_user_ids(user.id):
            raise HTTPException(status_code=403, detail="Cannot comment on this post")

    content = payload.get("content")
    if not content:
        raise HTTPException(status_code=400, detail="Content is required")

    from app.services.content_moderation import ContentModeration
    moderation_result = ContentModeration.check_content(content)
    if not moderation_result["approved"]:
        ContentModeration.log_moderation(
            user_id=user.id, content_type="comment", original_text=content, reasons=moderation_result["reasons"]
        )
        raise HTTPException(status_code=400, detail={"error": "Content rejected by moderation", "reasons": moderation_result["reasons"]})

    parent_id = payload.get("parent_id")
    reply_to_user_id = payload.get("reply_to_user_id")

    if parent_id:
        parent_comment = db.query(Comment).filter(Comment.id == parent_id).first()
        if not parent_comment:
            raise HTTPException(status_code=404, detail="Parent comment not found")
        if not reply_to_user_id:
            reply_to_user_id = parent_comment.user_id

    comment = Comment(
        content=content,
        user_id=user.id,
        post_id=post_id,
        parent_id=parent_id,
        reply_to_user_id=reply_to_user_id,
    )

    try:
        db.add(comment)
        # 如果是回复，递增父评论的 reply_count
        if parent_id:
            parent_comment.reply_count = (parent_comment.reply_count or 0) + 1
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    # 通知和@提及（失败不影响评论创建，传入 db 复用 Session）
    try:
        NotificationService.notify_comment(post.user_id, user.id, post_id, content, db=db)
        if reply_to_user_id and reply_to_user_id != user.id:
            NotificationService.notify_comment(reply_to_user_id, user.id, post_id, content, db=db)
        MentionService.process_mentions(content=content, author_id=user.id, post_id=post_id, comment_id=comment.id)
    except Exception as e:
        print(f"[WARNING] 评论通知/@提及处理失败: {e}")

    return {
        "message": "Comment created successfully",
        "comment": _enrich_comment_dict(comment, db, user.id),
        "comment_count": post.get_comment_count(),
    }


@router.get("/posts/{post_id}/comments")
def get_comments(
    post_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    parent_id: int = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取帖子的评论列表

    不传 parent_id：返回顶级评论（分页），每条内嵌第一页回复（默认10条/页）。
    传入 parent_id：返回该父评论的子回复（分页）。
    """
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    # 获取当前用户屏蔽列表，过滤评论
    from app.ws_manager import ws_manager
    blocked_ids = ws_manager.get_blocked_user_ids(user.id)

    if parent_id is not None:
        # ========== 获取指定父评论的回复（分页） ==========
        parent_comment = db.query(Comment).filter(Comment.id == parent_id).first()
        if not parent_comment:
            raise HTTPException(status_code=404, detail="Parent comment not found")

        replies_query = (
            db.query(Comment)
            .filter(Comment.parent_id == parent_id)
            .order_by(Comment.created_at.asc())
        )
        if blocked_ids:
            replies_query = replies_query.filter(~Comment.user_id.in_(blocked_ids))
        total = replies_query.count()
        replies = replies_query.offset((page - 1) * per_page).limit(per_page).all()
        has_more = (page * per_page) < total

        return {
            "comments": _batch_enrich_comments(db, replies, user.id),
            "has_more": has_more,
        }

    # ========== 获取顶级评论（分页），每条内嵌第一页回复 ==========
    REPLY_PAGE_SIZE = 10

    comments_query = (
        db.query(Comment)
        .filter(Comment.post_id == post_id, Comment.parent_id == None)
        .order_by(Comment.created_at.desc())
    )
    if blocked_ids:
        comments_query = comments_query.filter(~Comment.user_id.in_(blocked_ids))
    total = comments_query.count()
    comments = comments_query.offset((page - 1) * per_page).limit(per_page).all()

    # 批量查询所有主评论的回复
    parent_ids = [c.id for c in comments]
    replies_by_parent: dict = {}
    if parent_ids:
        all_replies = (
            db.query(Comment)
            .filter(Comment.parent_id.in_(parent_ids))
            .order_by(Comment.parent_id, Comment.created_at.asc())
            .all()
        )
        # 过滤已屏蔽用户的回复
        if blocked_ids:
            all_replies = [r for r in all_replies if r.user_id not in blocked_ids]
        for reply in all_replies:
            replies_by_parent.setdefault(reply.parent_id, []).append(reply)

    # 收集所有需要 enrichment 的评论对象（主评论 + 回复）
    all_comment_objects = list(comments)
    for replies in replies_by_parent.values():
        all_comment_objects.extend(replies)

    # 批量 enrichment
    enriched_map = {}
    for c in all_comment_objects:
        enriched_map[c.id] = c.to_dict(current_user_id=user.id)

    # 批量设置 is_liked
    if all_comment_objects:
        comment_ids = [c.id for c in all_comment_objects]
        liked_set = set(
            row[0] for row in db.query(Like.comment_id).filter(
                Like.comment_id.in_(comment_ids), Like.user_id == user.id
            ).all()
        )
        for cid, d in enriched_map.items():
            d['is_liked'] = cid in liked_set

    # 组装结果：每条主评论内嵌第一页回复
    result_comments = []
    for c in comments:
        d = enriched_map[c.id]
        replies = replies_by_parent.get(c.id, [])
        reply_page = replies[:REPLY_PAGE_SIZE]
        d['replies'] = [enriched_map[r.id] for r in reply_page]
        d['replies_has_more'] = len(replies) > REPLY_PAGE_SIZE
        d['replies_page'] = 1
        result_comments.append(d)

    return {
        "total": total,
        "comments": result_comments,
    }


@router.get("/comments/{comment_id}")
def get_comment_detail(
    comment_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取单个评论详情（含自身信息 + 回复列表）"""
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    # 查询该评论的回复
    replies_query = (
        db.query(Comment)
        .filter(Comment.parent_id == comment_id)
        .order_by(Comment.created_at.asc())
    )
    total = replies_query.count()
    replies = replies_query.offset((page - 1) * per_page).limit(per_page).all()
    pages = (total + per_page - 1) // per_page if total > 0 else 0

    # 评论 + 回复合并批量 enrich
    all_comments = [comment] + replies
    enriched = _batch_enrich_comments(db, all_comments, user.id)

    return {
        "comment": enriched[0],
        "replies": enriched[1:],
        "reply_total": total,
        "reply_pages": pages,
        "reply_current_page": page,
        "reply_per_page": per_page,
    }


@router.put("/comments/{comment_id}")
def update_comment(
    comment_id: int,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新评论"""
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    if comment.user_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    content = payload.get("content")
    if not content:
        raise HTTPException(status_code=400, detail="Content is required")

    comment.content = content
    try:
        db.commit()
        return {"message": "Comment updated successfully", "comment": _enrich_comment_dict(comment, db, user.id)}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/comments/{comment_id}")
def delete_comment(
    comment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除评论"""
    comment = db.query(Comment).filter(Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    if comment.user_id != user.id and comment.post.user_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    try:
        # 如果是回复，递减父评论的 reply_count
        if comment.parent_id:
            parent = db.query(Comment).filter(Comment.id == comment.parent_id).first()
            if parent and parent.reply_count and parent.reply_count > 0:
                parent.reply_count -= 1
        db.delete(comment)
        db.commit()
        post = db.query(Post).filter(Post.id == comment.post_id).first()
        return {"message": "Comment deleted successfully", "comment_count": post.get_comment_count() if post else 0}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
