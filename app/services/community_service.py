"""社群业务逻辑层。

职责划分（参考项目现有模式：复杂业务进 service，router 只做参数校验+鉴权）：
- 权限判定（角色查询、操作合法性）
- 状态机流转（加群申请 → 审核 → 成员）
- 冗余计数维护（member_count / post_count）
- 通知触发
"""
import re
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.models import Conversation, ConversationParticipant, User
from app.models.community import Community, CommunityMember, CommunityJoinRequest
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

MAX_ADMINS = 5            # 除创始人外，管理员上限
MAX_JOINED = 100          # 单用户加入社群总数上限
ROLE_OWNER = 'owner'
ROLE_ADMIN = 'admin'
ROLE_MEMBER = 'member'


class CommunityError(Exception):
    """业务异常，router 层捕获转 HTTPException。"""
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class CommunityService:

    # ---------- 工具 ----------

    @staticmethod
    def _slugify(name: str) -> str:
        """生成 URL 友好 slug。中文等非 ASCII 直接用拼音化做不到，
        退化为去除特殊字符 + 小写；冲突时调用方补随机后缀。"""
        s = re.sub(r'[^\w\s-]', '', name, flags=re.UNICODE).strip().lower()
        s = re.sub(r'[\s_-]+', '-', s)
        return s[:50] or 'community'

    @staticmethod
    def _unique_slug(db: Session, name: str) -> str:
        import secrets
        base = CommunityService._slugify(name)
        slug = base
        # 冲突就加 4 位随机后缀，最多试几次
        for _ in range(5):
            exists = db.query(Community).filter(Community.slug == slug).first()
            if not exists:
                return slug
            slug = f"{base}-{secrets.token_hex(2)}"
        return f"{base}-{secrets.token_hex(4)}"

    @staticmethod
    def get_member(db: Session, community_id: int, user_id: int):
        return db.query(CommunityMember).filter(
            CommunityMember.community_id == community_id,
            CommunityMember.user_id == user_id,
        ).first()

    @staticmethod
    def get_role(db: Session, community_id: int, user_id: int):
        """返回 user 在社群的角色（owner/admin/member），非成员返回 None。
        仅 active 成员才算。"""
        m = CommunityService.get_member(db, community_id, user_id)
        if m and m.status == 'active':
            return m.role
        return None

    @staticmethod
    def is_manager(db: Session, community_id: int, user_id: int) -> bool:
        """是否为 owner 或 admin（可管理社群）。"""
        return CommunityService.get_role(db, community_id, user_id) in (ROLE_OWNER, ROLE_ADMIN)

    @staticmethod
    def _get_active_community(db: Session, community_id: int) -> Community:
        c = db.query(Community).filter(Community.id == community_id).first()
        if not c or c.status != 'active':
            raise CommunityError(404, "社群不存在或已解散")
        return c

    @staticmethod
    def _ensure_chat_participant(db: Session, community_id: int, user_id: int):
        conv = db.query(Conversation).filter(
            Conversation.type == 'community',
            Conversation.community_id == community_id,
        ).first()
        if not conv:
            return
        exists = db.query(ConversationParticipant).filter(
            ConversationParticipant.conversation_id == conv.id,
            ConversationParticipant.user_id == user_id,
        ).first()
        if not exists:
            db.add(ConversationParticipant(
                conversation_id=conv.id,
                user_id=user_id,
                joined_at=datetime.utcnow(),
            ))

    # ---------- 社群 CRUD ----------

    @staticmethod
    def create_community(db: Session, owner_id: int, name: str, description: str = None,
                         avatar_url: str = None, banner_url: str = None, rules: str = None,
                         join_policy: str = 'approval', topic_id: int = None) -> Community:
        name = (name or '').strip()
        if not name:
            raise CommunityError(400, "社群名称不能为空")
        if len(name) > 64:
            raise CommunityError(400, "社群名称过长（最多64字）")
        if join_policy not in ('open', 'approval', 'invite'):
            raise CommunityError(400, "无效的加群方式")

        # 同名校验（软约束，名称不唯一但提示）
        community = Community(
            name=name,
            slug=CommunityService._unique_slug(db, name),
            description=(description or '').strip()[:2000] or None,
            avatar_url=avatar_url,
            banner_url=banner_url,
            rules=(rules or '').strip()[:2000] or None,
            owner_id=owner_id,
            topic_id=topic_id,
            visibility='public',
            join_policy=join_policy,
            member_count=1,
            post_count=0,
            status='active',
            created_at=datetime.utcnow(),
        )
        db.add(community)
        db.flush()

        # 创始人自动入群
        db.add(CommunityMember(
            community_id=community.id,
            user_id=owner_id,
            role=ROLE_OWNER,
            status='active',
            joined_at=datetime.utcnow(),
        ))

        # 自动创建群聊会话
        from app.models.models import Message
        conv = Conversation(
            type='community',
            community_id=community.id,
            created_at=datetime.utcnow(),
        )
        db.add(conv)
        db.flush()
        CommunityService._ensure_chat_participant(db, community.id, owner_id)

        # 发送系统欢迎消息
        welcome_msg = Message(
            conversation_id=conv.id,
            sender_id=owner_id,
            content=f"欢迎加入「{name}」！",
            message_type='system',
            created_at=datetime.utcnow(),
        )
        db.add(welcome_msg)
        conv.last_message_at = datetime.utcnow()

        db.commit()
        db.refresh(community)
        return community

    @staticmethod
    def update_community(db: Session, community_id: int, actor_id: int, **fields) -> Community:
        c = CommunityService._get_active_community(db, community_id)
        if not CommunityService.is_manager(db, community_id, actor_id):
            raise CommunityError(403, "无权限编辑该社群")

        editable = {'name', 'description', 'avatar_url', 'banner_url', 'rules', 'join_policy', 'topic_id'}
        for k, v in fields.items():
            if k not in editable or v is None:
                continue
            if k == 'name':
                v = v.strip()
                if not v or len(v) > 64:
                    raise CommunityError(400, "社群名称无效")
            if k == 'join_policy' and v not in ('open', 'approval', 'invite'):
                raise CommunityError(400, "无效的加群方式")
            setattr(c, k, v)
        c.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(c)
        return c

    @staticmethod
    def disband_community(db: Session, community_id: int, actor_id: int):
        c = CommunityService._get_active_community(db, community_id)
        if c.owner_id != actor_id:
            raise CommunityError(403, "只有创始人可以解散社群")

        # 软解散：status=archived，保留数据用于审计
        c.status = 'archived'
        c.updated_at = datetime.utcnow()

        # 帖子归个人（community_id 置空），避免悬空引用
        from app.models.models import Post
        db.query(Post).filter(Post.community_id == community_id).update(
            {Post.community_id: None}, synchronize_session=False
        )
        db.commit()

    # ---------- 加群 / 审核 ----------

    @staticmethod
    def request_join(db: Session, community_id: int, user_id: int, message: str = None):
        c = CommunityService._get_active_community(db, community_id)

        # 已是成员？
        existing = CommunityService.get_member(db, community_id, user_id)
        if existing and existing.status == 'active':
            raise CommunityError(400, "你已经是该社群成员")

        # 加群总数上限
        joined = db.query(CommunityMember).filter(
            CommunityMember.user_id == user_id,
            CommunityMember.status == 'active',
        ).count()
        if joined >= MAX_JOINED:
            raise CommunityError(400, f"最多只能加入 {MAX_JOINED} 个社群")

        # 开放制：直接入群
        if c.join_policy == 'open':
            if existing:
                existing.status = 'active'
                existing.role = ROLE_MEMBER
                existing.joined_at = datetime.utcnow()
            else:
                db.add(CommunityMember(
                    community_id=community_id, user_id=user_id,
                    role=ROLE_MEMBER, status='active', joined_at=datetime.utcnow(),
                ))
            c.member_count = (c.member_count or 0) + 1
            CommunityService._ensure_chat_participant(db, community_id, user_id)
            db.commit()
            return {'status': 'joined'}

        if c.join_policy == 'invite':
            raise CommunityError(403, "该社群仅限邀请加入")

        # 审核制：创建/复用申请
        req = db.query(CommunityJoinRequest).filter(
            CommunityJoinRequest.community_id == community_id,
            CommunityJoinRequest.user_id == user_id,
        ).first()
        if req and req.status == 'pending':
            raise CommunityError(400, "申请已提交，请等待审核")
        if req:
            req.status = 'pending'
            req.message = (message or '').strip()[:500] or None
            req.reviewed_by = None
            req.reviewed_at = None
            req.created_at = datetime.utcnow()
        else:
            req = CommunityJoinRequest(
                community_id=community_id, user_id=user_id,
                message=(message or '').strip()[:500] or None,
                status='pending', created_at=datetime.utcnow(),
            )
            db.add(req)
        db.commit()
        db.refresh(req)

        # 通知所有管理员（owner + admin）
        CommunityService._notify_managers_join_request(db, c, user_id)
        return {'status': 'pending', 'request_id': req.id}

    @staticmethod
    def review_join(db: Session, community_id: int, request_id: int, reviewer_id: int, approve: bool):
        c = CommunityService._get_active_community(db, community_id)
        if not CommunityService.is_manager(db, community_id, reviewer_id):
            raise CommunityError(403, "无权限审核加群申请")

        req = db.query(CommunityJoinRequest).filter(
            CommunityJoinRequest.id == request_id,
            CommunityJoinRequest.community_id == community_id,
        ).first()
        if not req:
            raise CommunityError(404, "申请不存在")
        if req.status != 'pending':
            raise CommunityError(400, "该申请已处理")

        req.status = 'approved' if approve else 'rejected'
        req.reviewed_by = reviewer_id
        req.reviewed_at = datetime.utcnow()

        if approve:
            existing = CommunityService.get_member(db, community_id, req.user_id)
            if existing:
                existing.status = 'active'
                existing.role = ROLE_MEMBER
                existing.joined_at = datetime.utcnow()
            else:
                db.add(CommunityMember(
                    community_id=community_id, user_id=req.user_id,
                    role=ROLE_MEMBER, status='active', joined_at=datetime.utcnow(),
                ))
            c.member_count = (c.member_count or 0) + 1
            CommunityService._ensure_chat_participant(db, community_id, req.user_id)

        db.commit()

        # 通知申请人
        NotificationService.create_notification(
            user_id=req.user_id,
            notification_type='community_join_approved' if approve else 'community_join_rejected',
            title=f"加入「{c.name}」{'通过' if approve else '被拒绝'}",
            content=f"你加入社群「{c.name}」的申请{'已通过' if approve else '被拒绝'}",
            sender_id=reviewer_id,
            related_id=community_id,
            related_type='community',
        )
        return {'status': req.status}

    # ---------- 成员管理 ----------

    @staticmethod
    def leave_community(db: Session, community_id: int, user_id: int):
        c = CommunityService._get_active_community(db, community_id)
        m = CommunityService.get_member(db, community_id, user_id)
        if not m or m.status != 'active':
            raise CommunityError(400, "你不是该社群成员")
        if m.role == ROLE_OWNER:
            raise CommunityError(400, "创始人不能退群，请先转让或解散社群")
        db.delete(m)
        c.member_count = max((c.member_count or 1) - 1, 0)
        db.commit()

    @staticmethod
    def kick_member(db: Session, community_id: int, target_id: int, actor_id: int):
        c = CommunityService._get_active_community(db, community_id)
        actor_role = CommunityService.get_role(db, community_id, actor_id)
        if actor_role not in (ROLE_OWNER, ROLE_ADMIN):
            raise CommunityError(403, "无权限踢人")

        target = CommunityService.get_member(db, community_id, target_id)
        if not target or target.status != 'active':
            raise CommunityError(404, "目标不是社群成员")
        if target.role == ROLE_OWNER:
            raise CommunityError(403, "不能踢出创始人")
        # admin 不能踢 admin（只有 owner 能）
        if target.role == ROLE_ADMIN and actor_role != ROLE_OWNER:
            raise CommunityError(403, "管理员不能踢出其他管理员")

        db.delete(target)
        c.member_count = max((c.member_count or 1) - 1, 0)
        db.commit()

        NotificationService.create_notification(
            user_id=target_id,
            notification_type='community_kicked',
            title=f"你已被移出「{c.name}」",
            content=f"你已被移出社群「{c.name}」",
            sender_id=actor_id,
            related_id=community_id,
            related_type='community',
        )

    @staticmethod
    def set_role(db: Session, community_id: int, target_id: int, actor_id: int, role: str):
        """任命/撤销管理员。仅 owner 可操作。role ∈ {admin, member}。"""
        c = CommunityService._get_active_community(db, community_id)
        if c.owner_id != actor_id:
            raise CommunityError(403, "只有创始人可以任命/撤销管理员")
        if role not in (ROLE_ADMIN, ROLE_MEMBER):
            raise CommunityError(400, "无效的角色")

        target = CommunityService.get_member(db, community_id, target_id)
        if not target or target.status != 'active':
            raise CommunityError(404, "目标不是社群成员")
        if target.role == ROLE_OWNER:
            raise CommunityError(400, "不能修改创始人角色")

        if role == ROLE_ADMIN:
            if target.role == ROLE_ADMIN:
                return  # 已是管理员
            admin_count = db.query(CommunityMember).filter(
                CommunityMember.community_id == community_id,
                CommunityMember.role == ROLE_ADMIN,
                CommunityMember.status == 'active',
            ).count()
            if admin_count >= MAX_ADMINS:
                raise CommunityError(400, f"管理员数量已达上限（{MAX_ADMINS}）")
            target.role = ROLE_ADMIN
            db.commit()
            NotificationService.create_notification(
                user_id=target_id,
                notification_type='community_new_admin',
                title=f"你已成为「{c.name}」管理员",
                content=f"创始人任命你为社群「{c.name}」的管理员",
                sender_id=actor_id,
                related_id=community_id,
                related_type='community',
            )
        else:
            target.role = ROLE_MEMBER
            db.commit()

    # ---------- 查询 ----------

    @staticmethod
    def list_members(
        db: Session,
        community_id: int,
        limit: int = 50,
        offset: int = 0,
        viewer_user_id: int | None = None,
    ):
        q = db.query(CommunityMember).filter(
            CommunityMember.community_id == community_id,
            CommunityMember.status == 'active',
        )
        if viewer_user_id is not None:
            from app.services.block_service import visible_user_predicate

            q = q.filter(visible_user_predicate(viewer_user_id, CommunityMember.user_id))
        q = q.order_by(
            # owner 在前，admin 次之，member 最后，再按加入时间
            CommunityMember.role.asc(), CommunityMember.joined_at.asc()
        )
        return q.offset(offset).limit(limit).all()

    @staticmethod
    def list_join_requests(db: Session, community_id: int, actor_id: int, limit: int = 50, offset: int = 0):
        if not CommunityService.is_manager(db, community_id, actor_id):
            raise CommunityError(403, "无权限查看加群申请")
        return db.query(CommunityJoinRequest).filter(
            CommunityJoinRequest.community_id == community_id,
            CommunityJoinRequest.status == 'pending',
        ).order_by(CommunityJoinRequest.created_at.asc()).offset(offset).limit(limit).all()

    @staticmethod
    def list_my_communities(db: Session, user_id: int, manage_only: bool = False):
        q = db.query(Community).join(
            CommunityMember, CommunityMember.community_id == Community.id
        ).filter(
            CommunityMember.user_id == user_id,
            CommunityMember.status == 'active',
            Community.status == 'active',
        )
        if manage_only:
            q = q.filter(CommunityMember.role.in_([ROLE_OWNER, ROLE_ADMIN]))
        return q.order_by(
            Community.updated_at.is_(None).asc(),
            Community.updated_at.desc(),
            Community.created_at.desc(),
        ).all()

    @staticmethod
    def search_communities(db: Session, keyword: str = None, limit: int = 20, offset: int = 0):
        q = db.query(Community).filter(Community.status == 'active')
        if keyword:
            kw = f"%{keyword.strip()}%"
            q = q.filter(Community.name.like(kw))
        return q.order_by(Community.member_count.desc()).offset(offset).limit(limit).all()

    # ---------- 内部通知 ----------

    @staticmethod
    def _notify_managers_join_request(db: Session, community: Community, applicant_id: int):
        managers = db.query(CommunityMember).filter(
            CommunityMember.community_id == community.id,
            CommunityMember.role.in_([ROLE_OWNER, ROLE_ADMIN]),
            CommunityMember.status == 'active',
        ).all()
        applicant = db.query(User).filter(User.id == applicant_id).first()
        applicant_name = applicant.username if applicant else "有用户"
        for m in managers:
            if m.user_id == applicant_id:
                continue
            NotificationService.create_notification(
                user_id=m.user_id,
                notification_type='community_join_request',
                title=f"「{community.name}」有新的加群申请",
                content=f"{applicant_name} 申请加入社群「{community.name}」",
                sender_id=applicant_id,
                related_id=community.id,
                related_type='community',
            )

    # ==================== 公告 ====================

    @staticmethod
    def create_announcement(db: Session, community_id: int, author_id: int,
                            title: str, content: str = None, is_pinned: bool = False):
        c = CommunityService._get_active_community(db, community_id)
        if not CommunityService.is_manager(db, community_id, author_id):
            raise CommunityError(403, "无权限发布公告")

        from app.models.community import CommunityAnnouncement
        announcement = CommunityAnnouncement(
            community_id=community_id,
            author_id=author_id,
            title=title.strip()[:200],
            content=(content or '').strip()[:5000] or None,
            is_pinned=is_pinned,
            created_at=datetime.utcnow(),
        )
        db.add(announcement)
        db.commit()
        db.refresh(announcement)

        # 通知所有成员
        members = db.query(CommunityMember).filter(
            CommunityMember.community_id == community_id,
            CommunityMember.status == 'active',
        ).all()
        for m in members:
            if m.user_id == author_id:
                continue
            NotificationService.create_notification(
                user_id=m.user_id,
                notification_type='community_announcement',
                title=f"「{c.name}」新公告",
                content=announcement.title,
                sender_id=author_id,
                related_id=community_id,
                related_type='community',
                extra_id=announcement.id,
            )

        return announcement

    @staticmethod
    def list_announcements(db: Session, community_id: int):
        from app.models.community import CommunityAnnouncement
        return db.query(CommunityAnnouncement).filter(
            CommunityAnnouncement.community_id == community_id,
        ).order_by(
            CommunityAnnouncement.is_pinned.desc(),
            CommunityAnnouncement.created_at.desc(),
        ).all()

    @staticmethod
    def update_announcement(db: Session, community_id: int, announcement_id: int,
                            actor_id: int, **fields):
        if not CommunityService.is_manager(db, community_id, actor_id):
            raise CommunityError(403, "无权限编辑公告")
        from app.models.community import CommunityAnnouncement
        ann = db.query(CommunityAnnouncement).filter(
            CommunityAnnouncement.id == announcement_id,
            CommunityAnnouncement.community_id == community_id,
        ).first()
        if not ann:
            raise CommunityError(404, "公告不存在")

        editable = {'title', 'content', 'is_pinned'}
        for k, v in fields.items():
            if k in editable and v is not None:
                setattr(ann, k, v)
        ann.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(ann)
        return ann

    @staticmethod
    def delete_announcement(db: Session, community_id: int, announcement_id: int, actor_id: int):
        if not CommunityService.is_manager(db, community_id, actor_id):
            raise CommunityError(403, "无权限删除公告")
        from app.models.community import CommunityAnnouncement
        ann = db.query(CommunityAnnouncement).filter(
            CommunityAnnouncement.id == announcement_id,
            CommunityAnnouncement.community_id == community_id,
        ).first()
        if not ann:
            raise CommunityError(404, "公告不存在")
        db.delete(ann)
        db.commit()

    # ==================== 黑名单 ====================

    @staticmethod
    def ban_user(db: Session, community_id: int, actor_id: int,
                 target_id: int, reason: str = None):
        """拉黑用户（同时踢出、阻止加群）。owner/admin 可操作。"""
        c = CommunityService._get_active_community(db, community_id)
        if not CommunityService.is_manager(db, community_id, actor_id):
            raise CommunityError(403, "无权限拉黑")
        if target_id == c.owner_id:
            raise CommunityError(400, "不能拉黑创始人")
        if target_id == actor_id:
            raise CommunityError(400, "不能拉黑自己")

        target_role = CommunityService.get_role(db, community_id, target_id)
        if target_role == ROLE_ADMIN and CommunityService.get_role(db, community_id, actor_id) != ROLE_OWNER:
            raise CommunityError(403, "管理员不能拉黑其他管理员")

        from app.models.community import CommunityBan
        existing = db.query(CommunityBan).filter(
            CommunityBan.community_id == community_id,
            CommunityBan.user_id == target_id,
        ).first()
        if existing:
            raise CommunityError(400, "该用户已在黑名单中")

        ban = CommunityBan(
            community_id=community_id,
            user_id=target_id,
            banned_by=actor_id,
            reason=(reason or '').strip()[:200] or None,
            created_at=datetime.utcnow(),
        )
        db.add(ban)

        # 同步踢出成员
        m = CommunityService.get_member(db, community_id, target_id)
        if m and m.status == 'active':
            if m.role == ROLE_OWNER:
                raise CommunityError(400, "不能拉黑创始人")
            db.delete(m)
            c.member_count = max((c.member_count or 1) - 1, 0)

        db.commit()

        NotificationService.create_notification(
            user_id=target_id,
            notification_type='community_banned',
            title=f"你已被「{c.name}」拉黑",
            content=ban.reason or "你已被该社群拉黑",
            sender_id=actor_id,
            related_id=community_id,
            related_type='community',
        )

        return ban

    @staticmethod
    def unban_user(db: Session, community_id: int, actor_id: int, target_id: int):
        if not CommunityService.is_manager(db, community_id, actor_id):
            raise CommunityError(403, "无权限解封")
        from app.models.community import CommunityBan
        ban = db.query(CommunityBan).filter(
            CommunityBan.community_id == community_id,
            CommunityBan.user_id == target_id,
        ).first()
        if not ban:
            raise CommunityError(404, "该用户不在黑名单中")
        db.delete(ban)
        db.commit()

    @staticmethod
    def is_banned(db: Session, community_id: int, user_id: int) -> bool:
        from app.models.community import CommunityBan
        return db.query(CommunityBan).filter(
            CommunityBan.community_id == community_id,
            CommunityBan.user_id == user_id,
        ).first() is not None

    @staticmethod
    def list_bans(db: Session, community_id: int):
        from app.models.community import CommunityBan
        return db.query(CommunityBan).filter(
            CommunityBan.community_id == community_id,
        ).order_by(CommunityBan.created_at.desc()).all()

    # ==================== 热门排序 ====================

    @staticmethod
    def list_hot_posts(
        db: Session,
        community_id: int,
        limit: int = 20,
        current_user_id: int | None = None,
    ):
        """按热度排序社群帖子：热度 = (like_count*2 + comment_count*3) / hours_ago。
        公式简单易懂，筛选近 72 小时的帖子。"""
        from math import ceil
        from app.models.models import Post
        from app.services.post_visibility_service import post_visibility_predicate
        from sqlalchemy import func

        # 近 72 小时的帖子
        cutoff = datetime.utcnow() - timedelta(hours=72)
        posts = db.query(Post).filter(
            Post.community_id == community_id,
            Post.hidden_by_admin.is_not(True),
            Post.created_at >= cutoff,
            post_visibility_predicate(current_user_id),
        ).all()

        now = datetime.utcnow()
        scored = []
        for p in posts:
            hours = max((now - p.created_at).total_seconds() / 3600, 0.1)
            like_score = (getattr(p, 'like_count', 0) or 0) * 2
            comment_score = (getattr(p, 'comment_count', 0) or 0) * 3
            share_score = (getattr(p, 'share_count', 0) or 0) * 1
            score = (like_score + comment_score + share_score) / hours
            scored.append((score, p))

        scored.sort(key=lambda x: -x[0])
        return [p for _, p in scored[:limit]]
