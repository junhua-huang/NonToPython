import json
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import Post

USER_MESSAGE_TYPES = {"text", "image", "video", "post"}
SYSTEM_MESSAGE_TYPES = {"system"}
MESSAGE_TYPES = USER_MESSAGE_TYPES | SYSTEM_MESSAGE_TYPES
MEDIA_MESSAGE_TYPES = {"image", "video"}
CARD_MESSAGE_TYPES = {"post"}


def _clean_text(value: Any) -> str:
    return (value or "").strip()


def _parse_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_post_image(post: Post) -> Optional[str]:
    if not post.images:
        return None
    try:
        images = json.loads(post.images)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(images, list):
        return None
    for item in images:
        url = str(item or "").strip()
        if url:
            return url
    return None


def _post_preview_content(post: Post) -> str:
    content = _clean_text(post.content)
    if not content:
        return f"查看帖子 #{post.id}"
    return content[:60]


def normalize_user_message_payload(payload: dict, db: Session) -> dict:
    message_type = _clean_text(payload.get("message_type") or "text").lower()
    content = _clean_text(payload.get("content"))
    media_url = _clean_text(payload.get("media_url") or payload.get("file_url"))
    related_id = _parse_int(payload.get("related_id"))

    if message_type == "system":
        raise HTTPException(status_code=403, detail="系统消息不能由用户发送")
    if message_type not in USER_MESSAGE_TYPES:
        raise HTTPException(status_code=400, detail="不支持的消息类型")

    if message_type == "text":
        if not content:
            raise HTTPException(status_code=400, detail="消息内容不能为空")
        return {
            "content": content,
            "message_type": message_type,
            "media_url": None,
            "related_id": related_id,
        }

    if message_type in MEDIA_MESSAGE_TYPES:
        if not media_url and content.startswith(("http://", "https://")):
            media_url = content
        if not media_url:
            raise HTTPException(status_code=400, detail="媒体消息不能为空")
        return {
            "content": content or media_url,
            "message_type": message_type,
            "media_url": media_url,
            "related_id": related_id,
        }

    if message_type == "post":
        if related_id is None:
            raise HTTPException(status_code=400, detail="帖子消息缺少 related_id")
        post = db.query(Post).filter(Post.id == related_id).first()
        if not post:
            raise HTTPException(status_code=404, detail="帖子不存在")
        return {
            "content": content or _post_preview_content(post),
            "message_type": message_type,
            "media_url": media_url or _first_post_image(post),
            "related_id": related_id,
        }

    raise HTTPException(status_code=400, detail="不支持的消息类型")
