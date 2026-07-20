"""聊天回复（quote）服务。

负责：
1. 校验被引消息合法性（存在 / 同会话 / 非 system）。
2. 实时生成轻量预览文案（不入库），原消息撤回/删除后能反映最新状态。
3. 序列化消息时注入 quote_preview，覆盖历史入库值。

设计要点：
- ``quote_message_id`` 仍然持久化（用于点击跳转）。
- ``quote_preview`` 列保留但**停止写入**；所有响应里的 preview 由本服务实时生成。
- 前端可继续在乐观渲染时传 quote_preview，但后端会用自己生成的版本覆盖。
"""
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import Message
from app.services.block_service import visible_user_predicate

# 预览文案截断长度
_PREVIEW_TEXT_LIMIT = 50
_UNAVAILABLE_QUOTE_PREVIEW = "消息已撤回"


def _preview_text_for(message: Message) -> str:
    """根据消息类型生成预览文案。"""
    if message.is_recalled or message.deleted_by_admin:
        return "消息已撤回"

    mtype = message.message_type or "text"
    if mtype == "text":
        text = (message.content or "").strip()
        if not text:
            return ""
        return text[:_PREVIEW_TEXT_LIMIT] + ("..." if len(text) > _PREVIEW_TEXT_LIMIT else "")
    if mtype == "image":
        return "[图片]"
    if mtype == "video":
        return "[视频]"
    if mtype == "post":
        text = (message.content or "").strip()
        head = text[:30] + ("..." if len(text) > 30 else "") if text else f"帖子 #{message.id}"
        return f"[帖子] {head}"
    return (message.content or "")[:_PREVIEW_TEXT_LIMIT]


def validate_quote(
    db: Session,
    conversation_id: int,
    quote_message_id: Optional[int],
) -> Optional[Message]:
    """校验被引消息并返回实体。

    规则：
    - quote_message_id 为空 → 直接返回 None（无引用）。
    - 不存在 → 400。
    - 不属于同一会话 → 400（禁止跨会话引用）。
    - 系统消息 → 400（系统消息不可被回复）。
    - 已撤回/管理员删除 → 允许引用，但预览显示"消息已撤回"。

    返回被引 Message 实体（供发送方/读取方生成预览用）。
    """
    if quote_message_id is None:
        return None

    quoted = db.query(Message).filter(Message.id == quote_message_id).first()
    if not quoted:
        raise HTTPException(status_code=400, detail="被引用的消息不存在")
    if quoted.conversation_id != conversation_id:
        raise HTTPException(status_code=400, detail="不能跨会话引用消息")
    if quoted.message_type == "system":
        raise HTTPException(status_code=400, detail="系统消息不能被引用")
    return quoted


def build_quote_preview(quoted: Optional[Message]) -> Optional[str]:
    """根据被引实体生成预览文案。无引用时返回 None。"""
    if quoted is None:
        return None
    return _preview_text_for(quoted) or None


def _is_same_conversation_quote(quoted: Optional[Message], msg_dict: dict) -> bool:
    if quoted is None:
        return False
    conversation_id = msg_dict.get("conversation_id")
    return conversation_id is not None and quoted.conversation_id == conversation_id


def inject_quote_preview(
    db: Session,
    msg_dict: dict,
    viewer_user_id: int | None = None,
) -> dict:
    """序列化消息时实时回填 quote_preview。

    - 无 quote_message_id → preview 置 None。
    - 有但原消息已删 → preview 显示"消息已撤回"。
    - 其余按类型生成。

    就地修改并返回同一 dict，便于链式调用。
    """
    quote_id = msg_dict.get("quote_message_id")
    if not quote_id:
        msg_dict["quote_preview"] = None
        return msg_dict

    quoted_query = db.query(Message).filter(Message.id == quote_id)
    if viewer_user_id is not None:
        quoted_query = quoted_query.filter(
            visible_user_predicate(viewer_user_id, Message.sender_id)
        )
    quoted = quoted_query.first()
    if not _is_same_conversation_quote(quoted, msg_dict):
        msg_dict["quote_preview"] = _UNAVAILABLE_QUOTE_PREVIEW
        return msg_dict

    msg_dict["quote_preview"] = build_quote_preview(quoted)
    return msg_dict


def inject_quote_preview_batch(
    db: Session,
    msg_dicts: list[dict],
    viewer_user_id: int | None = None,
) -> list[dict]:
    """批量回填 quote_preview。

    一次查出所有被引消息，避免 N+1。
    """
    quote_ids = {d.get("quote_message_id") for d in msg_dicts if d.get("quote_message_id")}
    if not quote_ids:
        for d in msg_dicts:
            d["quote_preview"] = None
        return msg_dicts

    quoted_query = db.query(Message).filter(Message.id.in_(quote_ids))
    if viewer_user_id is not None:
        quoted_query = quoted_query.filter(
            visible_user_predicate(viewer_user_id, Message.sender_id)
        )
    quoted_map: dict[int, Message] = {m.id: m for m in quoted_query.all()}
    for d in msg_dicts:
        qid = d.get("quote_message_id")
        if not qid:
            d["quote_preview"] = None
            continue
        quoted = quoted_map.get(qid)
        d["quote_preview"] = (
            build_quote_preview(quoted)
            if _is_same_conversation_quote(quoted, d)
            else _UNAVAILABLE_QUOTE_PREVIEW
        )
    return msg_dicts
