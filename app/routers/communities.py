"""社群路由 - Phase 1 + Phase 2（群聊/撤回/@提及）。

路由定义顺序很重要：静态路径（/my）必须在动态路径（/{community_id}）之前。
"""
import logging
import asyncio
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, get_optional_user
from app.models.models import User, Conversation, Message
from app.models.community import Community, CommunityMember
from app.services.community_service import CommunityService, CommunityError, MAX_ADMINS

logger = logging.getLogger(__name__)
router = APIRouter()


def _handle(fn):
    try:
        return fn()
    except CommunityError as e:
        raise HTTPException(status_code=e.code, detail=e.message)


def _community_message_to_dict(message: Message):
    data = message.to_dict()
    return {
        **data,
        "sender": message.sender.to_dict() if message.sender else None,
    }


def _normalize_community_message_payload(payload: dict):
    content = (payload.get("content") or "").strip()
    message_type = (payload.get("message_type") or "text").strip().lower()
    media_url = (payload.get("media_url") or "").strip()
    mention_user_ids = payload.get("mention_user_ids", [])
    allowed_types = {'text', 'image', 'video'}

    if message_type not in allowed_types:
        raise HTTPException(status_code=400, detail="不支持的消息类型")

    if message_type == 'text' and not content:
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    if message_type in {'image', 'video'}:
        if not (media_url or content):
            raise HTTPException(status_code=400, detail="媒体消息不能为空")
        if not media_url:
            media_url = content
        if not content:
            content = media_url

    if not isinstance(mention_user_ids, list):
        mention_user_ids = []

    return content, message_type, media_url or None, mention_user_ids


# ============================================================
# 我的社群（必须在 /{community_id} 前定义）
# ============================================================

@router.get("/my")
def my_communities(
    manage_only: bool = Query(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """我加入的社群（manage_only=True 仅返回我管理的）"""
    items = CommunityService.list_my_communities(db, user.id, manage_only=manage_only)
    return {"communities": [c.to_dict() for c in items]}


# ============================================================
# 社群 CRUD
# ============================================================

@router.post("")
def create_community(
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建社群"""
    def _do():
        c = CommunityService.create_community(
            db=db, owner_id=user.id,
            name=payload.get("name"),
            description=payload.get("description"),
            avatar_url=payload.get("avatar_url"),
            banner_url=payload.get("banner_url"),
            rules=payload.get("rules"),
            join_policy=payload.get("join_policy", "approval"),
            topic_id=payload.get("topic_id"),
        )
        return {"community": c.to_dict()}
    return _handle(_do)


@router.get("")
def list_communities(
    keyword: str = Query(None),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """搜索/列出社群"""
    items = CommunityService.search_communities(db, keyword=keyword, limit=limit, offset=offset)
    return {"communities": [c.to_dict() for c in items]}


@router.get("/{community_id}")
def get_community(
    community_id: int,
    user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """社群详情"""
    c = db.query(Community).filter(Community.id == community_id).first()
    if not c or c.status != 'active':
        raise HTTPException(status_code=404, detail="社群不存在或已解散")
    data = c.to_dict()
    my_role, my_status = None, None
    if user:
        m = CommunityService.get_member(db, community_id, user.id)
        if m:
            my_role, my_status = m.role, m.status
    data["my_role"] = my_role
    data["my_status"] = my_status
    return {"community": data}


@router.patch("/{community_id}")
def update_community(
    community_id: int,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """编辑社群（owner/admin）"""
    def _do():
        c = CommunityService.update_community(db, community_id, user.id, **payload)
        return {"community": c.to_dict()}
    return _handle(_do)


@router.delete("/{community_id}")
def disband_community(
    community_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """解散社群（仅 owner）"""
    def _do():
        CommunityService.disband_community(db, community_id, user.id)
        return {"message": "社群已解散"}
    return _handle(_do)


# ============================================================
# 加群 / 审核
# ============================================================

@router.post("/{community_id}/join")
def join_community(
    community_id: int,
    payload: dict = Body(default={}),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """申请加群"""
    def _do():
        return CommunityService.request_join(db, community_id, user.id, payload.get("message"))
    return _handle(_do)


@router.get("/{community_id}/join-requests")
def list_join_requests(
    community_id: int,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """待审核申请列表"""
    def _do():
        items = CommunityService.list_join_requests(db, community_id, user.id, limit, offset)
        return {"requests": [r.to_dict() for r in items]}
    return _handle(_do)


@router.post("/{community_id}/join-requests/{request_id}/approve")
def approve_join_request(
    community_id: int,
    request_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """通过申请"""
    def _do():
        return CommunityService.review_join(db, community_id, request_id, user.id, approve=True)
    return _handle(_do)


@router.post("/{community_id}/join-requests/{request_id}/reject")
def reject_join_request(
    community_id: int,
    request_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """拒绝申请"""
    def _do():
        return CommunityService.review_join(db, community_id, request_id, user.id, approve=False)
    return _handle(_do)


# ============================================================
# 成员管理
# ============================================================

@router.get("/{community_id}/members")
def list_members(
    community_id: int,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """成员列表"""
    items = CommunityService.list_members(db, community_id, limit, offset)
    return {"members": [m.to_dict() for m in items]}


@router.delete("/{community_id}/members/me")
def leave_community(
    community_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """退群"""
    def _do():
        CommunityService.leave_community(db, community_id, user.id)
        return {"message": "已退出社群"}
    return _handle(_do)


@router.delete("/{community_id}/members/{target_id}")
def kick_member(
    community_id: int,
    target_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """踢人"""
    def _do():
        CommunityService.kick_member(db, community_id, target_id, user.id)
        return {"message": "已移出成员"}
    return _handle(_do)


# ============================================================
# 公告（Phase 3）
# ============================================================

@router.get("/{community_id}/announcements")
def list_announcements(
    community_id: int,
    user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """公告列表（公开）"""
    items = CommunityService.list_announcements(db, community_id)
    return {"announcements": [a.to_dict() for a in items]}


@router.post("/{community_id}/announcements")
def create_announcement(
    community_id: int,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """发布公告（管理员+）"""
    def _do():
        a = CommunityService.create_announcement(
            db, community_id, user.id,
            title=payload.get("title"),
            content=payload.get("content"),
            is_pinned=payload.get("is_pinned", False),
        )
        return {"announcement": a.to_dict()}
    return _handle(_do)


@router.patch("/{community_id}/announcements/{announcement_id}")
def update_announcement(
    community_id: int,
    announcement_id: int,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """编辑公告（管理员+）"""
    def _do():
        a = CommunityService.update_announcement(
            db, community_id, announcement_id, user.id, **payload
        )
        return {"announcement": a.to_dict()}
    return _handle(_do)


@router.delete("/{community_id}/announcements/{announcement_id}")
def delete_announcement(
    community_id: int,
    announcement_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除公告（管理员+）"""
    def _do():
        CommunityService.delete_announcement(db, community_id, announcement_id, user.id)
        return {"message": "公告已删除"}
    return _handle(_do)


# ============================================================
# 黑名单（Phase 3）
# ============================================================

@router.get("/{community_id}/bans")
def list_bans(
    community_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """黑名单列表（管理员+）"""
    def _do():
        if not CommunityService.is_manager(db, community_id, user.id):
            raise CommunityError(403, "无权限查看黑名单")
        items = CommunityService.list_bans(db, community_id)
        return {"bans": [b.to_dict() for b in items]}
    return _handle(_do)


@router.post("/{community_id}/bans")
def ban_user(
    community_id: int,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """拉黑用户（管理员+）"""
    def _do():
        b = CommunityService.ban_user(
            db, community_id, user.id,
            target_id=payload.get("user_id"),
            reason=payload.get("reason"),
        )
        return {"ban": b.to_dict()}
    return _handle(_do)


@router.delete("/{community_id}/bans/{target_id}")
def unban_user(
    community_id: int,
    target_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """解封用户（管理员+）"""
    def _do():
        CommunityService.unban_user(db, community_id, user.id, target_id)
        return {"message": "已解封"}
    return _handle(_do)


# ============================================================
# 热门排序（Phase 3）
# ============================================================

@router.get("/{community_id}/hot-posts")
def list_hot_posts(
    community_id: int,
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """社群热门帖子（公开）"""
    posts = CommunityService.list_hot_posts(db, community_id, limit=limit)
    return {"posts": [p.to_dict() for p in posts]}


@router.patch("/{community_id}/members/{target_id}")
def set_member_role(
    community_id: int,
    target_id: int,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """任命/撤销管理员"""
    def _do():
        CommunityService.set_role(db, community_id, target_id, user.id, payload.get("role"))
        return {"message": "角色已更新"}
    return _handle(_do)


# ============================================================
# 群聊（Phase 2）
# ============================================================

@router.get("/{community_id}/chat")
def get_community_chat(
    community_id: int,
    limit: int = Query(50, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取社群群聊会话 + 最近消息"""
    c = db.query(Community).filter(Community.id == community_id, Community.status == 'active').first()
    if not c:
        raise HTTPException(status_code=404, detail="社群不存在")

    # 成员校验
    m = db.query(CommunityMember).filter(
        CommunityMember.community_id == community_id,
        CommunityMember.user_id == user.id,
        CommunityMember.status == 'active',
    ).first()
    if not m:
        raise HTTPException(status_code=403, detail="你不是该社群成员")

    conv = db.query(Conversation).filter(
        Conversation.type == 'community',
        Conversation.community_id == community_id,
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="群聊会话不存在")

    messages = db.query(Message).filter(
        Message.conversation_id == conv.id,
        Message.is_recalled == False,
    ).order_by(Message.created_at.desc()).limit(limit).all()

    return {
        "conversation": conv.to_dict(),
        "messages": [_community_message_to_dict(msg) for msg in reversed(messages)],
    }


@router.post("/{community_id}/chat/messages")
async def send_community_message(
    community_id: int,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """发送群聊消息（WS 扇出给所有成员）"""
    c = db.query(Community).filter(Community.id == community_id, Community.status == 'active').first()
    if not c:
        raise HTTPException(status_code=404, detail="社群不存在")

    m = db.query(CommunityMember).filter(
        CommunityMember.community_id == community_id,
        CommunityMember.user_id == user.id,
        CommunityMember.status == 'active',
    ).first()
    if not m:
        raise HTTPException(status_code=403, detail="你不是该社群成员")

    conv = db.query(Conversation).filter(
        Conversation.type == 'community',
        Conversation.community_id == community_id,
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="群聊会话不存在")

    content, message_type, media_url, mention_user_ids = (
        _normalize_community_message_payload(payload)
    )

    now = datetime.utcnow()
    msg = Message(
        conversation_id=conv.id,
        sender_id=user.id,
        content=content,
        message_type=message_type,
        media_url=media_url,
        created_at=now,
    )
    db.add(msg)
    conv.last_message_at = now
    db.commit()
    db.refresh(msg)

    msg_dict = _community_message_to_dict(msg)
    msg_dict["community_id"] = community_id
    msg_dict["community_name"] = c.name

    # WS 扇出给所有在线成员
    from app.ws_manager import ws_manager
    members = db.query(CommunityMember).filter(
        CommunityMember.community_id == community_id,
        CommunityMember.status == 'active',
    ).all()
    for member in members:
        if ws_manager.is_connected(member.user_id):
            await ws_manager.send_with_seq(member.user_id, "new_message", {
                "message": msg_dict,
                "conversation_id": conv.id,
                "community_id": community_id,
            })

    # @提及通知（离线/在线都推）
    if mention_user_ids:
        for uid in mention_user_ids:
            if uid == user.id:
                continue
            # 验证被 @ 者是成员
            tm = db.query(CommunityMember).filter(
                CommunityMember.community_id == community_id,
                CommunityMember.user_id == uid,
                CommunityMember.status == 'active',
            ).first()
            if not tm:
                continue
            from app.services.notification_service import NotificationService
            NotificationService.create_notification(
                user_id=uid,
                notification_type='community_mention',
                title=f"「{c.name}」有人 @ 你",
                content=f"{user.username}：{content[:50]}",
                sender_id=user.id,
                related_id=community_id,
                related_type='community',
            )

    return {"message": msg_dict}


@router.delete("/{community_id}/chat/messages/{message_id}")
def recall_community_message(
    community_id: int,
    message_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """撤回群聊消息（发送者 2 分钟内，管理员不限时间）"""
    c = db.query(Community).filter(Community.id == community_id, Community.status == 'active').first()
    if not c:
        raise HTTPException(status_code=404, detail="社群不存在")

    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="消息不存在")

    conv = db.query(Conversation).filter(Conversation.id == msg.conversation_id).first()
    if not conv or conv.type != 'community' or conv.community_id != community_id:
        raise HTTPException(status_code=400, detail="消息不属于该社群")

    # 权限：发送者本人（2 分钟内）或管理员
    is_sender = msg.sender_id == user.id
    is_admin = CommunityService.is_manager(db, community_id, user.id)

    if is_sender:
        if msg.created_at < datetime.utcnow() - timedelta(minutes=2):
            raise HTTPException(status_code=400, detail="消息已超过 2 分钟，无法撤回")
    elif not is_admin:
        raise HTTPException(status_code=403, detail="无权撤回此消息")

    msg.is_recalled = True
    msg.recalled_at = datetime.utcnow()
    if not is_sender and is_admin:
        msg.deleted_by_admin = True
    db.commit()

    return {"message": "消息已撤回"}