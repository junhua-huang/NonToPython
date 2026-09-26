import json
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.community import CommunityMember
from app.models.models import Message, Post
from app.services.post_visibility_service import (
    can_view_post,
    load_visible_post,
    post_visibility_predicate,
)

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


def validate_post_card_destination(
    db: Session,
    post: Post,
    sender_user_id: int,
    recipient_user_ids: list[int] | set[int] | tuple[int, ...],
    destination_community_id: int | None = None,
) -> None:
    """Fail closed unless every destination audience member can view the post."""
    recipient_ids = {int(user_id) for user_id in recipient_user_ids if user_id is not None}
    recipient_ids.discard(sender_user_id)

    if destination_community_id is not None:
        if post.community_only is True and post.community_id != destination_community_id:
            raise HTTPException(status_code=404, detail="帖子不可用")

        active_member_ids = {
            row.user_id
            for row in db.query(CommunityMember.user_id).filter(
                CommunityMember.community_id == destination_community_id,
                CommunityMember.status == "active",
            ).all()
        }
        if sender_user_id not in active_member_ids:
            raise HTTPException(status_code=404, detail="帖子不可用")
        recipient_ids = active_member_ids - {sender_user_id}

    for recipient_id in recipient_ids:
        if not can_view_post(db, post, recipient_id):
            raise HTTPException(status_code=404, detail="帖子不可用")


def normalize_user_message_payload(
    payload: dict,
    db: Session,
    viewer_user_id: int | None = None,
    recipient_user_ids: list[int] | set[int] | tuple[int, ...] = (),
    destination_community_id: int | None = None,
) -> dict:
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
        if db is None or viewer_user_id is None:
            raise HTTPException(status_code=404, detail="帖子不可用")
        post = load_visible_post(db, related_id, viewer_user_id)
        if not post:
            raise HTTPException(status_code=404, detail="帖子不可用")
        validate_post_card_destination(
            db,
            post,
            viewer_user_id,
            recipient_user_ids,
            destination_community_id=destination_community_id,
        )
        return {
            "content": content or _post_preview_content(post),
            "message_type": message_type,
            "media_url": media_url or _first_post_image(post),
            "related_id": related_id,
        }

    raise HTTPException(status_code=400, detail="不支持的消息类型")


def redact_unavailable_post_cards_batch(
    db: Session,
    message_dicts: list[dict],
    viewer_user_id: int,
) -> list[dict]:
    """Redact legacy post-card previews the current viewer can no longer access."""
    direct_post_ids = {
        _parse_int(message.get("related_id"))
        for message in message_dicts
        if message.get("message_type") == "post"
    }
    direct_post_ids.discard(None)

    quote_ids = {
        _parse_int(message.get("quote_message_id"))
        for message in message_dicts
        if message.get("quote_message_id")
    }
    quote_ids.discard(None)
    quoted_post_ids: dict[int, int] = {}
    if quote_ids:
        for quoted in db.query(Message).filter(
            Message.id.in_(quote_ids),
            Message.message_type == "post",
        ).all():
            if quoted.related_id is not None:
                quoted_post_ids[quoted.id] = quoted.related_id

    candidate_post_ids = direct_post_ids | set(quoted_post_ids.values())
    visible_post_ids: set[int] = set()
    if candidate_post_ids:
        visible_post_ids = {
            row.id
            for row in db.query(Post.id).filter(
                Post.id.in_(candidate_post_ids),
                post_visibility_predicate(viewer_user_id),
            ).all()
        }

    for message in message_dicts:
        post_id = _parse_int(message.get("related_id"))
        if message.get("message_type") == "post" and post_id not in visible_post_ids:
            message["content"] = "帖子不可用"
            message["media_url"] = None
            message["file_url"] = None
            message["post_unavailable"] = True
        elif message.get("message_type") == "post":
            message["post_unavailable"] = False

        quoted_post_id = quoted_post_ids.get(_parse_int(message.get("quote_message_id")))
        if quoted_post_id is not None and quoted_post_id not in visible_post_ids:
            message["quote_preview"] = "[帖子] 帖子不可用"

    return message_dicts


def redact_unavailable_post_card(
    db: Session,
    message_dict: dict,
    viewer_user_id: int,
) -> dict:
    redact_unavailable_post_cards_batch(db, [message_dict], viewer_user_id)
    return message_dict
