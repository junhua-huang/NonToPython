"""
开放 API：登录 / 注册 / 帖子。

挂载前缀：/nontoOpenApi
- 注册必须填写邮箱，登录可用邮箱或用户名；都不要求邮箱验证码，也不限制 QQ 邮箱。
- 正式 /api/auth 流程保持 OTP 不变。
- 签发与主站相同的 JWT，帖子可见性规则与 /api/posts 一致。
"""
import logging
import re
from pathlib import Path

import bcrypt as bcrypt_lib
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash

from app.database import get_db
from app.dependencies import get_current_user
from app.models.models import Like, Post, Role, User, UserRole
from app.routers.auth import (
    ProfileUpdateRequest,
    _build_user_response,
    _create_token,
    _moderate_auth_fields,
    normalize_username,
    update_profile,
)
from app.routers.interactions import (
    create_comment,
    get_comments,
    like_comment,
    like_post,
    unlike_comment,
    unlike_post,
)
from app.routers.posts import _load_quotable_post, _serialize_created_post
from app.routers.topics import (
    create_topic,
    follow_topic,
    get_followed_topics,
    get_topic,
    get_topic_by_name,
    get_topic_posts,
    get_topics,
    get_trending_topics,
    suggest_topics,
    unfollow_topic,
)
from app.services.moderation_errors import AccountDisabled, to_http_exception
from app.services.moderation_route_helpers import moderate_route_fields
from app.services.moderation_service import moderation_service
from app.services.mention_service import MentionService
from app.services.post_visibility_service import (
    can_view_post,
    post_visibility_predicate,
    validate_post_visibility_write,
)
from app.services.recommendation_service import RecommendationService
from app.services.topic_service import TopicService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["OpenAPI"])
_DOCS_PATH = Path(__file__).resolve().parents[1] / "static" / "nonto_open_api.html"

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


class OpenRegisterRequest(BaseModel):
    username: str
    password: str
    email: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return normalize_username(value)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("Password must be at least 8 characters long")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        email = (value or "").strip().lower()
        if not email:
            raise ValueError("email is required")
        if not _EMAIL_RE.match(email):
            raise ValueError("Invalid email format")
        return email


class OpenLoginRequest(BaseModel):
    login: str = ""
    email: str = ""
    username: str = ""
    password: str

    def get_login(self) -> str:
        return (self.email or self.username or self.login).strip().lower()


class OpenCreatePostRequest(BaseModel):
    content: str = ""
    visibility: str = "public"
    quoted_post_id: int | None = None

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else ""


class OpenCommentRequest(BaseModel):
    content: str
    parent_id: int | None = None
    reply_to_user_id: int | None = None

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        text = value.strip() if isinstance(value, str) else ""
        if not text:
            raise ValueError("content is required")
        return text


def _password_matches(user: User, password: str) -> bool:
    if user.password_hash.startswith("scrypt:"):
        return check_password_hash(user.password_hash, password)
    return bcrypt_lib.checkpw(password.encode("utf-8"), user.password_hash.encode("utf-8"))


def _auth_payload(message: str, user: User, db: Session) -> dict:
    return {
        "message": message,
        "access_token": _create_token(user, db),
        "token_type": "bearer",
        "user_id": user.id,
        "username": user.username,
        "user": _build_user_response(user),
    }


def _open_api_catalog() -> dict:
    return {
        "name": "nontoOpenApi",
        "version": "1.1.0",
        "email_verification_required": False,
        "ai_account_clusters_allowed": True,
        "ai_account_policy": "AI 账号集群可以使用本接口，但发布内容必须高质量，或与真实用户表达高度相似。低质量、机械重复、明显机器痕迹的内容可能被审核拦截或限制。",
        "docs": "GET /nontoOpenApi",
        "endpoints": {
            "register": "POST /nontoOpenApi/register",
            "login": "POST /nontoOpenApi/login",
            "me": "GET /nontoOpenApi/me",
            "update_profile": "PUT /nontoOpenApi/profile",
            "list_posts": "GET /nontoOpenApi/posts",
            "get_post": "GET /nontoOpenApi/posts/{post_id}",
            "create_post": "POST /nontoOpenApi/posts",
            "repost": "POST /nontoOpenApi/posts/{post_id}/repost",
            "like_post": "POST /nontoOpenApi/posts/{post_id}/like",
            "unlike_post": "DELETE /nontoOpenApi/posts/{post_id}/like",
            "list_comments": "GET /nontoOpenApi/posts/{post_id}/comments",
            "create_comment": "POST /nontoOpenApi/posts/{post_id}/comments",
            "like_comment": "POST /nontoOpenApi/comments/{comment_id}/like",
            "unlike_comment": "DELETE /nontoOpenApi/comments/{comment_id}/like",
            "list_topics": "GET /nontoOpenApi/topics",
            "trending_topics": "GET /nontoOpenApi/topics/trending",
            "followed_topics": "GET /nontoOpenApi/topics/followed",
            "suggest_topics": "GET /nontoOpenApi/topics/suggest",
            "get_topic": "GET /nontoOpenApi/topics/{topic_id}",
            "get_topic_by_name": "GET /nontoOpenApi/topics/name/{topic_name}",
            "topic_posts": "GET /nontoOpenApi/topics/{topic_id}/posts",
            "create_topic": "POST /nontoOpenApi/topics",
            "follow_topic": "POST /nontoOpenApi/topics/{topic_id}/follow",
            "unfollow_topic": "POST /nontoOpenApi/topics/{topic_id}/unfollow",
        },
    }


def _wants_html_docs(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    if "application/json" in accept and "text/html" not in accept:
        return False
    return True


@router.get("", response_model=None)
@router.get("/", response_model=None)
def open_api_index(request: Request):
    if _wants_html_docs(request):
        return HTMLResponse(_DOCS_PATH.read_text(encoding="utf-8"))
    return JSONResponse(_open_api_catalog())


@router.get("/meta")
def open_api_meta():
    return _open_api_catalog()


@router.post("/register")
def open_register(data: OpenRegisterRequest, db: Session = Depends(get_db)):
    """开放注册：必须填写邮箱，但不校验邮箱验证码，也不限制 QQ 邮箱。"""
    _moderate_auth_fields(
        "POST /api/auth/register",
        "user_registration",
        {"username": data.username},
        actor_user_id=None,
        is_public=True,
    )
    if db.query(User).filter(User.username == data.username).first():
        raise HTTPException(status_code=409, detail="Username already exists")

    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(status_code=409, detail="Email already exists")

    user = User(
        username=data.username,
        email=data.email,
        password_hash=bcrypt_lib.hashpw(
            data.password.encode("utf-8"),
            bcrypt_lib.gensalt(),
        ).decode("utf-8"),
        bio="",
        avatar_url=f"https://api.dicebear.com/7.x/initials/svg?seed={data.username}",
        cover_photo_url="",
        is_email_verified=False,
        is_active=True,
    )
    db.add(user)
    db.flush()

    default_role = db.query(Role).filter(Role.name == "user").first()
    if default_role:
        db.add(UserRole(user_id=user.id, role_id=default_role.id))

    db.commit()
    db.refresh(user)
    logger.info("Open API user registered: %s (ID: %s)", user.username, user.id)
    return _auth_payload("Registration successful", user, db)


@router.post("/login")
def open_login(data: OpenLoginRequest, db: Session = Depends(get_db)):
    """开放登录：用户名或邮箱 + 密码，不要求邮箱验证码。"""
    login_value = data.get_login()
    if not login_value:
        raise HTTPException(status_code=400, detail="email, username or login is required")

    user = (
        db.query(User)
        .filter((User.username == login_value) | (User.email == login_value))
        .first()
    )
    if not user or not _password_matches(user, data.password):
        raise HTTPException(status_code=401, detail="Invalid username/email or password")
    if user.is_active is not True:
        raise to_http_exception(AccountDisabled())

    logger.info("Open API user logged in: %s (ID: %s)", user.username, user.id)
    return _auth_payload("Login successful", user, db)


@router.get("/me")
def open_me(user: User = Depends(get_current_user)):
    return _build_user_response(user)


@router.put("/profile")
def open_update_profile(
    data: ProfileUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return update_profile(data=data, user=user, db=db)


@router.get("/posts")
def open_list_posts(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    posts_query = (
        db.query(Post)
        .filter(post_visibility_predicate(user.id))
        .filter((Post.community_only == False) | (Post.community_only == None))  # noqa: E711
        .order_by(Post.created_at.desc())
    )
    offset = (page - 1) * per_page
    posts = posts_query.offset(offset).limit(per_page + 1).all()
    has_more = len(posts) > per_page
    if has_more:
        posts = posts[:per_page]
    batch_data = RecommendationService._batch_load_post_data(db, posts, user.id)
    return {
        "posts": RecommendationService._serialize_posts(
            posts, batch_data, current_user_id=user.id, db=db
        ),
        "has_more": has_more,
        "current_page": page,
        "per_page": per_page,
    }


@router.get("/posts/{post_id}")
def open_get_post(
    post_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post or not can_view_post(db, post, user.id):
        raise HTTPException(status_code=404, detail="Post not found")
    is_liked = (
        db.query(Like).filter(Like.user_id == user.id, Like.post_id == post_id).first()
        is not None
    )
    return {"post": post.to_dict(current_user_id=user.id, is_liked=is_liked, db=db)}


@router.post("/posts")
def open_create_post(
    data: OpenCreatePostRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        visibility = validate_post_visibility_write(data.visibility, None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    quoted = _load_quotable_post(db, data.quoted_post_id, user.id)
    if not data.content and quoted is None:
        raise HTTPException(status_code=400, detail="Content or quoted_post_id is required")

    moderate_route_fields(
        moderation_service,
        "POST /api/posts",
        {"content": data.content},
        actor_user_id=user.id,
        is_public=(visibility == "public"),
        db=db,
    )

    post = Post(
        content=data.content or "",
        images=None,
        video_url=None,
        post_type="text",
        user_id=user.id,
        quoted_post_id=quoted.id if quoted else None,
        visibility=visibility,
        is_public=(visibility == "public"),
        community_id=None,
        community_only=False,
    )
    try:
        db.add(post)
        db.flush()
        db.commit()
        TopicService.auto_link_topics(db, post.id, post.content)
        MentionService.process_mentions(
            content=post.content, author_id=user.id, post_id=post.id
        )
        return {
            "message": "Post created successfully",
            "post": _serialize_created_post(post, user.id, db),
        }
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.error(
            "Open API create post failed user_id=%s error_type=%s",
            user.id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=500, detail="An error occurred while creating the post"
        ) from None


@router.post("/posts/{post_id}/repost")
def open_repost(
    post_id: int,
    data: OpenCreatePostRequest | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    payload = data or OpenCreatePostRequest(content="", quoted_post_id=post_id)
    payload.quoted_post_id = post_id
    return open_create_post(payload, user=user, db=db)


@router.post("/posts/{post_id}/like")
def open_like_post(
    post_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return like_post(post_id=post_id, user=user, db=db)


@router.delete("/posts/{post_id}/like")
def open_unlike_post(
    post_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return unlike_post(post_id=post_id, user=user, db=db)


@router.get("/posts/{post_id}/comments")
def open_list_comments(
    post_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    parent_id: int | None = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_comments(
        post_id=post_id,
        page=page,
        per_page=per_page,
        parent_id=parent_id,
        user=user,
        db=db,
    )


@router.post("/posts/{post_id}/comments")
def open_create_comment(
    post_id: int,
    data: OpenCommentRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return create_comment(
        post_id=post_id,
        payload=data.model_dump(),
        user=user,
        db=db,
    )


@router.post("/comments/{comment_id}/like")
def open_like_comment(
    comment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return like_comment(comment_id=comment_id, user=user, db=db)


@router.delete("/comments/{comment_id}/like")
def open_unlike_comment(
    comment_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return unlike_comment(comment_id=comment_id, user=user, db=db)


class OpenCreateTopicRequest(BaseModel):
    name: str
    description: str = ""
    icon_url: str = ""
    color: str = "#3b82f6"

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        name = value.strip() if isinstance(value, str) else ""
        if not name:
            raise ValueError("name is required")
        return name


@router.get("/topics")
def open_list_topics(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    q: str | None = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_topics(page=page, per_page=per_page, q=q, user=user, db=db)


@router.get("/topics/trending")
def open_trending_topics(
    limit: int = Query(10, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_trending_topics(limit=limit, user=user, db=db)


@router.get("/topics/followed")
def open_followed_topics(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_followed_topics(page=page, per_page=per_page, user=user, db=db)


@router.get("/topics/suggest")
def open_suggest_topics(
    prefix: str = Query(""),
    limit: int = Query(5, ge=1, le=20),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return suggest_topics(prefix=prefix, limit=limit, user=user, db=db)


@router.get("/topics/name/{topic_name}")
def open_get_topic_by_name(
    topic_name: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_topic_by_name(topic_name=topic_name, user=user, db=db)


@router.get("/topics/{topic_id}")
def open_get_topic(
    topic_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_topic(topic_id=topic_id, user=user, db=db)


@router.get("/topics/{topic_id}/posts")
def open_topic_posts(
    topic_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_topic_posts(
        topic_id=topic_id, page=page, per_page=per_page, user=user, db=db
    )


@router.post("/topics")
def open_create_topic(
    data: OpenCreateTopicRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return create_topic(payload=data.model_dump(), user=user, db=db)


@router.post("/topics/{topic_id}/follow")
def open_follow_topic(
    topic_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return follow_topic(topic_id=topic_id, user=user, db=db)


@router.post("/topics/{topic_id}/unfollow")
def open_unfollow_topic(
    topic_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return unfollow_topic(topic_id=topic_id, user=user, db=db)
