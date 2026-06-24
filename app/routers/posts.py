"""
帖子路由 - FastAPI 重构版
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Body, Query
from sqlalchemy.orm import Session
from typing import Optional
import logging

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import BUSINESS_IDENTITY_ROLES, User, Post, Like, Comment, Friendship, PostView, Notification, Role, UserRole, post_visibility
from sqlalchemy import or_, select
from app.core.config import Config
from app.utils import FileUploader
from app.services.topic_service import TopicService
from app.services.mention_service import MentionService

logger = logging.getLogger(__name__)
router = APIRouter()


def resolve_display_role_type(user: User, requested_role: Optional[str], db: Session) -> Optional[str]:
    """只允许使用当前用户已认证业务身份；无效值不阻断发帖。"""
    role_name = requested_role.strip().lower() if requested_role else ""
    if not role_name or role_name not in BUSINESS_IDENTITY_ROLES:
        return None

    row = (
        db.query(UserRole)
        .join(Role, UserRole.role_id == Role.id)
        .filter(UserRole.user_id == user.id, Role.name == role_name)
        .first()
    )
    return role_name if row else None


@router.post("")
async def create_post(
    image: Optional[UploadFile] = File(None),
    video: Optional[UploadFile] = File(None),
    content: Optional[str] = Form(None),
    image_urls: Optional[str] = Form(None),
    video_url_input: Optional[str] = Form(None, alias="video_url"),
    content_category: Optional[str] = Form(None),
    display_role_type: Optional[str] = Form(None),
    visibility: str = Form("public"),
    visible_user_ids: Optional[str] = Form(None),
    community_id: Optional[int] = Form(None),
    community_only: Optional[bool] = Form(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建新帖子（支持文件上传 + 多图 URL）"""
    import json as _json
    current_user_id = user.id
    final_image_url = None
    final_video_url = video_url_input
    images_json = None
    post_type = "text"

    # 处理多图 URL（JSON 数组字符串）
    if image_urls:
        try:
            images_list = _json.loads(image_urls)
            if isinstance(images_list, list) and len(images_list) > 0:
                images_json = _json.dumps(images_list)
                final_image_url = images_list[0]
                post_type = "image"
        except (_json.JSONDecodeError, TypeError):
            pass

    # 处理图片上传
    if image and image.filename:
        contents = await image.read()
        if len(contents) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Image too large for optimization (max 10MB)")

        from app.services.image_optimizer import ImageOptimizer
        from io import BytesIO
        from fastapi.datastructures import UploadFile as FastAPIUploadFile

        optimization_result = ImageOptimizer.optimize_image(contents)
        if optimization_result["success"]:
            optimized_file = FastAPIUploadFile(
                file=BytesIO(optimization_result["data"]),
                filename=image.filename,
                headers={"content-type": "image/jpeg"},
            )
            result = FileUploader.save_file(optimized_file, file_type="image", subfolder=f"posts/{current_user_id}")
            if result["success"]:
                final_image_url = result["url"]
                images_json = _json.dumps([final_image_url])
                post_type = "image"
            else:
                raise HTTPException(status_code=400, detail=f"Image upload failed: {result['error']}")
        else:
            raise HTTPException(status_code=400, detail=f"Image optimization failed: {optimization_result['error']}")

    # 处理视频上传
    if video and video.filename:
        result = FileUploader.save_file(video, file_type="video", subfolder=f"posts/{current_user_id}")
        if result["success"]:
            final_video_url = result["url"]
            post_type = "video"
        else:
            raise HTTPException(status_code=400, detail=f"Video upload failed: {result['error']}")

    if not content and not final_image_url and not final_video_url:
        raise HTTPException(status_code=400, detail="Content or media file is required")

    # 内容审核
    if content:
        from app.services.content_moderation import ContentModeration
        moderation_result = ContentModeration.check_content(content)
        if not moderation_result["approved"]:
            ContentModeration.log_moderation(
                user_id=current_user_id,
                content_type="post",
                original_text=content,
                reasons=moderation_result["reasons"],
            )
            raise HTTPException(status_code=400, detail={
                "error": "Content rejected by moderation",
                "reasons": moderation_result["reasons"],
            })
        content = moderation_result["filtered_text"]

    # 社群发帖校验
    if community_id:
        from app.models.community import Community, CommunityMember
        c = db.query(Community).filter(Community.id == community_id, Community.status == 'active').first()
        if not c:
            raise HTTPException(status_code=404, detail="社群不存在")
        m = db.query(CommunityMember).filter(
            CommunityMember.community_id == community_id,
            CommunityMember.user_id == current_user_id,
            CommunityMember.status == 'active',
        ).first()
        if not m:
            raise HTTPException(status_code=403, detail="你不是该社群成员，无法发帖")

    post = Post(
        content=content,
        images=images_json,
        video_url=final_video_url,
        post_type=post_type,
        user_id=current_user_id,
        content_category=content_category.strip() if content_category else None,
        display_role_type=resolve_display_role_type(user, display_role_type, db),
        visibility=visibility,
        is_public=(visibility == "public"),
        community_id=community_id,
        community_only=community_only or False,
    )

    try:
        db.add(post)
        db.flush()

        if visibility == "custom" and visible_user_ids:
            uids = [int(uid.strip()) for uid in visible_user_ids.split(",") if uid.strip()]
            for uid in uids:
                stmt = post_visibility.insert().values(post_id=post.id, user_id=uid)
                db.execute(stmt)

        db.commit()

        if post.content:
            TopicService.auto_link_topics(db, post.id, post.content)
            MentionService.process_mentions(content=post.content, author_id=current_user_id, post_id=post.id)

        return {"message": "Post created successfully", "post": post.to_dict(current_user_id=None, is_liked=False)}
    except Exception as e:
        db.rollback()
        logger.error(f"Create post failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while creating the post")


@router.get("/")
def get_posts(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    community_id: Optional[int] = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取帖子列表（动态）。community_id 非空时返回社群帖子流。"""
    current_user_id = user.id

    # 社群帖子流：只看该社群帖子
    if community_id:
        from app.models.community import Community
        c = db.query(Community).filter(Community.id == community_id).first()
        if not c or c.status != 'active':
            raise HTTPException(status_code=404, detail="社群不存在")

        posts_query = (
            db.query(Post)
            .filter(Post.community_id == community_id, Post.hidden_by_admin == False)
            .order_by(Post.created_at.desc())
        )
        offset = (page - 1) * per_page
    else:
        # 主信息流
        friendships = db.query(Friendship).filter(
            ((Friendship.sender_id == current_user_id) | (Friendship.receiver_id == current_user_id))
            & (Friendship.status == "accepted")
        ).all()
        friend_ids = [
            f.sender_id if f.receiver_id == current_user_id else f.receiver_id for f in friendships
        ]
        friend_ids.append(current_user_id)

        # 过滤已屏蔽用户的帖子
        from app.ws_manager import ws_manager
        blocked_ids = ws_manager.get_blocked_user_ids(current_user_id)

        posts_query = (
            db.query(Post)
            .filter(
                or_(
                    Post.visibility == "public",
                    Post.user_id.in_(friend_ids),
                )
            )
            .filter(or_(Post.community_only == False, Post.community_only == None))  # 主信息流排除仅社群可见帖
            .order_by(Post.created_at.desc())
        )

        if blocked_ids:
            posts_query = posts_query.filter(~Post.user_id.in_(blocked_ids))

        offset = (page - 1) * per_page

    posts = posts_query.offset(offset).limit(per_page + 1).all()
    has_more = len(posts) > per_page
    if has_more:
        posts = posts[:per_page]

    from app.services.recommendation_service import RecommendationService
    batch_data = RecommendationService._batch_load_post_data(db, posts, current_user_id)

    return {
        "posts": RecommendationService._serialize_posts(posts, batch_data),
        "has_more": has_more,
        "current_page": page,
        "per_page": per_page,
    }


@router.get("/user/{user_id}")
def get_user_posts(
    user_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取指定用户的帖子"""
    # 屏蔽检查：当前用户屏蔽了目标用户，则无法查看
    if user.id != user_id:
        from app.ws_manager import ws_manager
        if user_id in ws_manager.get_blocked_user_ids(user.id):
            return {"posts": [], "has_more": False, "current_page": page, "per_page": per_page}

    posts_query = (
        db.query(Post)
        .filter(Post.user_id == user_id, Post.is_public == True)
        .order_by(Post.created_at.desc())
    )
    offset = (page - 1) * per_page
    posts = posts_query.offset(offset).limit(per_page + 1).all()
    has_more = len(posts) > per_page
    if has_more:
        posts = posts[:per_page]

    from app.services.recommendation_service import RecommendationService
    batch_data = RecommendationService._batch_load_post_data(db, posts, user.id)

    return {
        "posts": RecommendationService._serialize_posts(posts, batch_data),
        "has_more": has_more,
        "current_page": page,
        "per_page": per_page,
    }


@router.get("/{post_id}")
def get_post(post_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """获取单个帖子详情"""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    is_liked = db.query(Like).filter(Like.user_id == user.id, Like.post_id == post_id).first() is not None
    return {"post": post.to_dict(current_user_id=None, is_liked=is_liked)}


@router.put("/{post_id}")
def update_post(
    post_id: int,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新帖子"""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.user_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    if "content" in payload:
        post.content = payload["content"]
    if "video_url" in payload:
        post.video_url = payload["video_url"]
    if "is_public" in payload:
        post.is_public = payload["is_public"]

    try:
        db.commit()
        if "content" in payload and payload["content"]:
            TopicService.auto_link_topics(db, post.id, payload["content"])
            MentionService.process_mentions(payload["content"], post.user_id, post_id=post.id)
        is_liked = db.query(Like).filter(Like.user_id == user.id, Like.post_id == post_id).first() is not None
        return {"message": "Post updated successfully", "post": post.to_dict(current_user_id=None, is_liked=is_liked)}
    except Exception as e:
        db.rollback()
        logger.error(f"Update post failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while updating the post")


@router.delete("/{post_id}")
def delete_post(post_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """删除帖子"""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.user_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    try:
        # Delete related data in correct order to avoid FK constraint violations
        # 1. Delete likes on comments of this post (FK: likes.comment_id → comments.id)
        comment_ids_subq = select(Comment.id).where(Comment.post_id == post_id)
        db.query(Like).filter(Like.comment_id.in_(comment_ids_subq)).delete(synchronize_session=False)
        # 2. Delete likes on the post itself (FK: likes.post_id → posts.id)
        db.query(Like).filter(Like.post_id == post_id).delete(synchronize_session=False)
        # 3. Delete child comments (replies) first — self-referencing FK parent_id → comments.id
        db.query(Comment).filter(
            Comment.post_id == post_id,
            Comment.parent_id.isnot(None)
        ).delete(synchronize_session=False)
        # 4. Delete parent comments
        db.query(Comment).filter(
            Comment.post_id == post_id,
            Comment.parent_id.is_(None)
        ).delete(synchronize_session=False)
        # 5. Delete post views (FK: post_views.post_id → posts.id)
        db.query(PostView).filter(PostView.post_id == post_id).delete(synchronize_session=False)
        # 6. Delete notifications referencing this post
        db.query(Notification).filter(
            Notification.related_id == post_id,
            Notification.notification_type.in_([
                'like', 'comment', 'post_mention',
            ]),
        ).delete(synchronize_session=False)
        if post.images:
            import json as _json
            try:
                imgs = _json.loads(post.images)
                for img_url in imgs:
                    if img_url:
                        FileUploader.delete_file(img_url)
            except (_json.JSONDecodeError, TypeError):
                pass
        if post.video_url:
            FileUploader.delete_file(post.video_url)
        db.delete(post)
        db.commit()
        return {"message": "Post deleted successfully"}
    except Exception as e:
        db.rollback()
        logger.error(f"Delete post failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while deleting the post")


@router.get("/user/{user_id}/liked")
def get_user_liked_posts(
    user_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取用户点赞过的帖子"""
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # 屏蔽检查
    from app.ws_manager import ws_manager
    if user_id in ws_manager.get_blocked_user_ids(user.id):
        return {"posts": [], "has_more": False, "current_page": page, "per_page": per_page}

    try:
        offset = (page - 1) * per_page
        liked_rows = (
            db.query(Post, Like.created_at.label("liked_at"))
            .join(Like, Like.post_id == Post.id)
            .filter(Like.user_id == user_id)
            .order_by(Like.created_at.desc())
            .offset(offset)
            .limit(per_page + 1)
            .all()
        )
        has_more = len(liked_rows) > per_page
        if has_more:
            liked_rows = liked_rows[:per_page]

        current_user_id = user.id
        post_objects = [post for post, _liked_at in liked_rows]

        from app.services.recommendation_service import RecommendationService
        batch_data = RecommendationService._batch_load_post_data(db, post_objects, current_user_id)

        posts_data = RecommendationService._serialize_posts(post_objects, batch_data)

        return {
            "posts": posts_data,
            "has_more": has_more,
            "current_page": page,
            "per_page": per_page,
        }
    except Exception as e:
        logger.error(f"Get user liked posts failed for user_id={user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="An error occurred while fetching liked posts")


@router.post("/{post_id}/view")
def record_post_view(post_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """记录帖子浏览"""
    from app.models.models import PostView

    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    existing = db.query(PostView).filter(
        PostView.user_id == user.id, PostView.post_id == post_id
    ).first()
    if existing:
        views_count = db.query(PostView).filter(PostView.post_id == post_id).count()
        return {"message": "Already viewed", "views": views_count}

    try:
        view = PostView(user_id=user.id, post_id=post_id)
        post.view_count += 1
        db.add(view)
        db.commit()
        views_count = db.query(PostView).filter(PostView.post_id == post_id).count()
        return {"message": "View recorded", "views": views_count, "view_count": post.view_count}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{post_id}/stats")
def get_post_stats(post_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """获取帖子统计数据"""
    from app.models.models import PostView

    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    likes_count = db.query(Like).filter(Like.post_id == post_id).count()
    comments_count = db.query(Comment).filter(Comment.post_id == post_id).count()
    views_count = db.query(PostView).filter(PostView.post_id == post_id).count()

    return {"post_id": post_id, "views": views_count, "likes": likes_count, "comments": comments_count}
