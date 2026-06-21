"""社群(Community)模块 ORM 模型。

参考推特 Communities 设计：
- 社群独立于好友/漫展
- 帖子流聚合到社群（复用 posts 表 + community_id）
- 成员角色：owner（创始人）/ admin（管理员，最多5人）/ member（普通成员）
- 加群方式：approval（审核制，默认）/ open（开放）/ invite（仅邀请）
- 公告：管理员发布，可置顶
- 黑名单：踢人后阻止重新加入
"""
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint, Index,
)
from sqlalchemy.orm import relationship

from app.database import Base


class Community(Base):
    """社群主表"""
    __tablename__ = 'communities'
    __table_args__ = (
        UniqueConstraint('slug', name='uq_communities_slug'),
        Index('ix_communities_topic', 'topic_id'),
    )

    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False)
    slug = Column(String(64))                         # URL 友好标识，自动生成（唯一约束见 __table_args__）
    description = Column(Text)
    avatar_url = Column(String(255))
    banner_url = Column(String(255))                  # 推特风格封面图
    rules = Column(Text)                              # 社群规则

    owner_id = Column(Integer, ForeignKey('users.id', name='fk_communities_owner'), nullable=False, index=True)
    topic_id = Column(Integer, ForeignKey('topics.id', name='fk_communities_topic'), nullable=True)

    visibility = Column(String(20), default='public')     # public（第一版固定）
    join_policy = Column(String(20), default='approval')  # open/approval/invite

    member_count = Column(Integer, default=0)          # 冗余计数，定时校准
    post_count = Column(Integer, default=0)            # 冗余计数，定时校准
    status = Column(String(20), default='active')      # active/archived/banned

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)

    # relationships
    owner = relationship('User', foreign_keys=[owner_id])
    members = relationship('CommunityMember', back_populates='community',
                           cascade='all, delete-orphan', lazy='dynamic')
    join_requests = relationship('CommunityJoinRequest', back_populates='community',
                                 cascade='all, delete-orphan', lazy='dynamic')
    announcements = relationship('CommunityAnnouncement', back_populates='community',
                                 cascade='all, delete-orphan', lazy='dynamic')
    bans = relationship('CommunityBan', back_populates='community',
                        cascade='all, delete-orphan', lazy='dynamic')

    def to_dict(self, include_owner=False, db=None):
        d = {
            'id': self.id,
            'name': self.name,
            'slug': self.slug,
            'description': self.description,
            'avatar_url': self.avatar_url,
            'banner_url': self.banner_url,
            'rules': self.rules,
            'owner_id': self.owner_id,
            'topic_id': self.topic_id,
            'visibility': self.visibility,
            'join_policy': self.join_policy,
            'member_count': self.member_count,
            'post_count': self.post_count,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_owner and db:
            from app.models.models import User
            owner = db.query(User).filter(User.id == self.owner_id).first()
            if owner:
                d['owner'] = {
                    'id': owner.id,
                    'username': owner.username,
                    'avatar_url': owner.avatar_url,
                }
        return d


class CommunityMember(Base):
    """社群成员关系表"""
    __tablename__ = 'community_members'
    __table_args__ = (
        UniqueConstraint('community_id', 'user_id', name='uq_cm_community_user'),
        Index('ix_cm_user_status', 'user_id', 'status'),
        Index('ix_cm_community_role', 'community_id', 'role'),
    )

    id = Column(Integer, primary_key=True)
    community_id = Column(Integer, ForeignKey('communities.id', ondelete='CASCADE', name='fk_cm_community'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE', name='fk_cm_user'), nullable=False)
    role = Column(String(20), default='member')        # owner/admin/member
    status = Column(String(20), default='active')      # pending/active/muted
    joined_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    # relationships
    community = relationship('Community', back_populates='members')
    user = relationship('User')

    def to_dict(self):
        d = {
            'id': self.id,
            'community_id': self.community_id,
            'user_id': self.user_id,
            'role': self.role,
            'status': self.status,
            'joined_at': self.joined_at.isoformat() if self.joined_at else None,
        }
        if self.user:
            d['user'] = {
                'id': self.user.id,
                'username': self.user.username,
                'avatar_url': self.user.avatar_url,
            }
        return d


class CommunityJoinRequest(Base):
    """社群加群申请表（审核制社群用）"""
    __tablename__ = 'community_join_requests'
    __table_args__ = (
        UniqueConstraint('community_id', 'user_id', name='uq_cjr_community_user'),
    )

    id = Column(Integer, primary_key=True)
    community_id = Column(Integer, ForeignKey('communities.id', ondelete='CASCADE', name='fk_cjr_community'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE', name='fk_cjr_user'), nullable=False)
    message = Column(String(500))                       # 申请理由
    status = Column(String(20), default='pending')      # pending/approved/rejected
    reviewed_by = Column(Integer, ForeignKey('users.id', name='fk_cjr_reviewer'))
    reviewed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    # relationships
    community = relationship('Community', back_populates='join_requests')
    user = relationship('User', foreign_keys=[user_id])
    reviewer = relationship('User', foreign_keys=[reviewed_by])

    def to_dict(self):
        d = {
            'id': self.id,
            'community_id': self.community_id,
            'user_id': self.user_id,
            'message': self.message,
            'status': self.status,
            'reviewed_by': self.reviewed_by,
            'reviewed_at': self.reviewed_at.isoformat() if self.reviewed_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
        if self.user:
            d['user'] = {
                'id': self.user.id,
                'username': self.user.username,
                'avatar_url': self.user.avatar_url,
            }
        if self.community:
            d['community_name'] = self.community.name
        return d


class CommunityAnnouncement(Base):
    """社群公告"""
    __tablename__ = 'community_announcements'
    __table_args__ = (
        Index('ix_ca_community_pinned', 'community_id', 'is_pinned'),
    )

    id = Column(Integer, primary_key=True)
    community_id = Column(Integer, ForeignKey('communities.id', ondelete='CASCADE', name='fk_ca_community'), nullable=False)
    author_id = Column(Integer, ForeignKey('users.id', name='fk_ca_author'), nullable=False)
    title = Column(String(200), nullable=False)
    content = Column(Text)
    is_pinned = Column(Boolean, default=False)          # 是否置顶
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, onupdate=datetime.utcnow)

    community = relationship('Community', back_populates='announcements')
    author = relationship('User')

    def to_dict(self):
        d = {
            'id': self.id,
            'community_id': self.community_id,
            'author_id': self.author_id,
            'title': self.title,
            'content': self.content,
            'is_pinned': self.is_pinned,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
        if self.author:
            d['author'] = {
                'id': self.author.id,
                'username': self.author.username,
            }
        return d


class CommunityBan(Base):
    """社群黑名单"""
    __tablename__ = 'community_bans'
    __table_args__ = (
        UniqueConstraint('community_id', 'user_id', name='uq_cb_community_user'),
    )

    id = Column(Integer, primary_key=True)
    community_id = Column(Integer, ForeignKey('communities.id', ondelete='CASCADE', name='fk_cb_community'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE', name='fk_cb_user'), nullable=False)
    banned_by = Column(Integer, ForeignKey('users.id', name='fk_cb_banned_by'), nullable=False)
    reason = Column(String(200))
    banned_until = Column(DateTime, nullable=True)     # NULL=永久；有值=临时
    created_at = Column(DateTime, default=datetime.utcnow)

    community = relationship('Community', back_populates='bans')
    user = relationship('User', foreign_keys=[user_id])
    banner = relationship('User', foreign_keys=[banned_by])

    def to_dict(self):
        d = {
            'id': self.id,
            'community_id': self.community_id,
            'user_id': self.user_id,
            'banned_by': self.banned_by,
            'reason': self.reason,
            'banned_until': self.banned_until.isoformat() if self.banned_until else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
        if self.user:
            d['user'] = {
                'id': self.user.id,
                'username': self.user.username,
                'avatar_url': self.user.avatar_url,
            }
        if self.community:
            d['community_name'] = self.community.name
        return d
