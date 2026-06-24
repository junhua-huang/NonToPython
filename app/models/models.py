"""
FastAPI 版本 - 数据模型定义
从 Flask-SQLAlchemy 迁移至 SQLAlchemy 2.0+ Declarative
"""
from datetime import datetime
import json
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, Float, ForeignKey,
    Table, Index, func, UniqueConstraint
)
from sqlalchemy.orm import relationship
from app.database import Base


def _get_ws_manager():
    """延迟导入，避免循环依赖"""
    from app.ws_manager import ws_manager
    return ws_manager


BUSINESS_IDENTITY_ROLES = {
    "event_organizer": "活动方",
    "coser": "Coser",
    "photographer": "摄影师",
    "wig_stylist": "毛娘",
    "makeup_artist": "妆娘",
    "ticket_agent": "票代",
    "prop_maker": "道具师",
    "costume_maker": "服装师",
    "retoucher": "后期师",
}

SYSTEM_ROLE_NAMES = {"admin", "super_admin", "moderator"}
ROLE_APPLICATION_STATUSES = {"pending", "verified", "rejected", "suspended"}


def is_business_identity_role(role_name: str | None) -> bool:
    return bool(role_name) and role_name in BUSINESS_IDENTITY_ROLES


def get_business_identity_label(role_name: str | None) -> str | None:
    if not role_name:
        return None
    return BUSINESS_IDENTITY_ROLES.get(role_name)


def _json_list(value):
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, json.JSONDecodeError):
        return []


# ============================================================
# 关联表
# ============================================================

post_topics = Table(
    'post_topics', Base.metadata,
    Column('post_id', Integer, ForeignKey('posts.id', ondelete='CASCADE', name='fk_post_topics_post'), primary_key=True),
    Column('topic_id', Integer, ForeignKey('topics.id', ondelete='CASCADE', name='fk_post_topics_topic'), primary_key=True),
    Column('created_at', DateTime, default=datetime.utcnow),
)

post_visibility = Table(
    'post_visibility', Base.metadata,
    Column('post_id', Integer, ForeignKey('posts.id', ondelete='CASCADE', name='fk_post_visibility_post'), primary_key=True),
    Column('user_id', Integer, ForeignKey('users.id', ondelete='CASCADE', name='fk_post_visibility_user'), primary_key=True),
)

topic_followers = Table(
    'topic_followers', Base.metadata,
    Column('user_id', Integer, ForeignKey('users.id', ondelete='CASCADE', name='fk_topic_followers_user'), primary_key=True),
    Column('topic_id', Integer, ForeignKey('topics.id', ondelete='CASCADE', name='fk_topic_followers_topic'), primary_key=True),
    Column('created_at', DateTime, default=datetime.utcnow),
)

# ============================================================
# 模型
# ============================================================

class User(Base):
    """用户模型"""
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    username = Column(String(80), unique=True, nullable=False, index=True)
    email = Column(String(120), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    bio = Column(Text)
    avatar_url = Column(String(255))
    cover_photo_url = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    # 邮箱是否已验证（注册时通过邮箱验证码验证后置 True）
    is_email_verified = Column(Boolean, default=False)

    # 隐私设置
    profile_visibility = Column(String(20), default='public')
    post_default_visibility = Column(String(20), default='public')
    show_email = Column(Boolean, default=False)
    allow_search = Column(Boolean, default=True)
    allow_friend_requests = Column(String(20), default='everyone')
    
    # 通知偏好设置
    notify_push = Column(Boolean, default=True)
    notify_message = Column(Boolean, default=True)
    notify_sound = Column(Boolean, default=True)
    
    # 关系
    posts = relationship('Post', back_populates='author', lazy='dynamic', cascade='all, delete-orphan', foreign_keys='Post.user_id')
    comments = relationship('Comment', foreign_keys='Comment.user_id', back_populates='author', lazy='dynamic', cascade='all, delete-orphan')
    likes = relationship('Like', back_populates='user', lazy='dynamic', cascade='all, delete-orphan')
    blocks = relationship('Block', foreign_keys='Block.blocker_id', back_populates='blocker', lazy='dynamic', cascade='all, delete-orphan')
    post_views = relationship('PostView', back_populates='viewer', lazy='dynamic', cascade='all, delete-orphan')
    
    friends_sent = relationship(
        'Friendship', foreign_keys='Friendship.sender_id',
        back_populates='sender', lazy='dynamic', cascade='all, delete-orphan'
    )
    friends_received = relationship(
        'Friendship', foreign_keys='Friendship.receiver_id',
        back_populates='receiver', lazy='dynamic', cascade='all, delete-orphan'
    )
    user_roles = relationship('UserRole', back_populates='user', lazy='joined', cascade='all, delete-orphan')
    
    def get_role_names(self):
        """返回当前用户的全部系统角色名，用于权限判断。"""
        return [ur.role.name for ur in self.user_roles if ur.role]

    def get_role_labels(self):
        """返回当前用户的全部系统角色标签，用于后台/鉴权响应。"""
        return [ur.role.label for ur in self.user_roles if ur.role]

    def get_verified_identity_roles(self):
        """返回公开展示的已认证业务身份。"""
        return [ur.role.name for ur in self.user_roles if ur.role and is_business_identity_role(ur.role.name)]

    def get_verified_identity_labels(self):
        """返回公开展示的已认证业务身份标签。"""
        return [ur.role.label for ur in self.user_roles if ur.role and is_business_identity_role(ur.role.name)]

    def has_role(self, role_name: str) -> bool:
        """检查用户是否拥有某个角色"""
        return role_name in self.get_role_names()

    def to_dict(self):
        ws_manager = _get_ws_manager()
        is_online = ws_manager.is_connected(self.id) if self.id else False
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'display_name': self.username,
            'bio': self.bio,
            'avatar': self.avatar_url,
            'avatar_url': self.avatar_url,
            'cover_photo_url': self.cover_photo_url,
            'is_online': is_online,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'roles': self.get_verified_identity_roles(),
            'role_labels': self.get_verified_identity_labels(),
            'verified_roles': self.get_verified_identity_roles(),
            'verified_role_labels': self.get_verified_identity_labels(),
        }


class Post(Base):
    """帖子模型"""
    __tablename__ = 'posts'
    __table_args__ = (
        Index('ix_posts_community_created', 'community_id', 'created_at'),
    )
    
    id = Column(Integer, primary_key=True)
    content = Column(Text, nullable=False)
    images = Column(Text)  # JSON array string for multi-image support
    video_url = Column(String(255))
    post_type = Column(String(20), default='text')
    content_category = Column(String(32), nullable=True)
    display_role_type = Column(String(32), nullable=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    visibility = Column(String(20), default='public')
    is_public = Column(Boolean, default=True)
    view_count = Column(Integer, default=0)

    # 社群关联（Phase 1）
    community_id = Column(Integer, ForeignKey('communities.id', name='fk_posts_community'), nullable=True)
    community_only = Column(Boolean, default=False)   # true=仅社群可见，不进主信息流
    hidden_by_admin = Column(Boolean, default=False)  # 社群管理员隐藏标记
    hidden_by = Column(Integer, ForeignKey('users.id', name='fk_posts_hidden_by'), nullable=True)
    hidden_at = Column(DateTime, nullable=True)
    
    # 关系
    author = relationship('User', back_populates='posts', foreign_keys=[user_id])
    comments = relationship('Comment', back_populates='post', lazy='dynamic', cascade='all, delete-orphan')
    likes = relationship('Like', back_populates='post', lazy='dynamic', cascade='all, delete-orphan')
    
    def get_like_count(self):
        return self.likes.filter(Like.comment_id.is_(None)).count()
    
    def get_comment_count(self):
        return self.comments.count()
    
    def get_topics(self, db=None):
        if db is None:
            from app.database import SessionLocal
            db = SessionLocal()
            own_db = True
        else:
            own_db = False
        try:
            from sqlalchemy import text
            result = db.execute(
                text("SELECT t.id, t.name, t.description, t.icon_url, t.color, t.post_count, t.follower_count, t.is_trending, t.created_at, t.updated_at FROM topics t JOIN post_topics pt ON t.id = pt.topic_id WHERE pt.post_id = :post_id"),
                {"post_id": self.id}
            )
            topics = []
            for row in result:
                topics.append({
                    "id": row[0], "name": row[1], "description": row[2],
                    "icon_url": row[3], "color": row[4], "post_count": row[5],
                    "follower_count": row[6], "is_trending": row[7],
                })
            return topics
        finally:
            if own_db:
                db.close()
    
    def to_dict(self, current_user_id=None, like_count=None, comment_count=None, topics=None, is_liked=None, db=None):
        import json
        images_list = None
        if self.images:
            try:
                images_list = json.loads(self.images)
            except (json.JSONDecodeError, TypeError):
                images_list = None

        # is_liked 必须由调用方传入，不再内部创建 Session
        # 如果调用方未传入且 current_user_id 不为 None，说明调用方未做批量查询，
        # 此时使用默认值 False（避免 N+1 反模式）
        effective_display_role_type = self.display_role_type
        if self.author is not None and self.display_role_type not in self.author.get_verified_identity_roles():
            effective_display_role_type = None

        result = {
            'id': self.id,
            'content': self.content,
            'images': images_list,
            'image_urls': images_list,
            'video_url': self.video_url,
            'post_type': self.post_type,
            'content_category': self.content_category,
            'display_role_type': effective_display_role_type,
            'display_role_label': get_business_identity_label(effective_display_role_type),
            'user_id': self.user_id,
            'author': self.author.to_dict() if self.author else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'visibility': self.visibility,
            'is_public': self.is_public,
            'like_count': like_count if like_count is not None else self.get_like_count(),
            'comment_count': comment_count if comment_count is not None else self.get_comment_count(),
            'view_count': self.view_count,
            'community_id': self.community_id,
            'topics': topics if topics is not None else self.get_topics(db=db),
            'is_liked': is_liked if is_liked is not None else False,
        }
        return result


class PostFeedSeen(Base):
    """首页推荐流曝光记录，用于短期去重。"""
    __tablename__ = 'post_feed_seen'
    __table_args__ = (
        UniqueConstraint('user_id', 'post_id', 'source', name='uq_post_feed_seen_user_post_source'),
        Index('idx_post_feed_seen_user_source_seen_post', 'user_id', 'source', 'seen_at', 'post_id'),
        Index('idx_post_feed_seen_post_source', 'post_id', 'source'),
        Index('idx_post_feed_seen_seen_at', 'seen_at'),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    post_id = Column(Integer, ForeignKey('posts.id', ondelete='CASCADE'), nullable=False)
    source = Column(String(32), nullable=False, default='home_feed')
    seen_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Comment(Base):
    """评论模型"""
    __tablename__ = 'comments'
    __table_args__ = (
        Index('idx_comments_post_parent', 'post_id', 'parent_id'),
        Index('idx_comments_parent_created', 'parent_id', 'created_at'),
    )
    
    id = Column(Integer, primary_key=True)
    content = Column(Text, nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    post_id = Column(Integer, ForeignKey('posts.id'), nullable=False, index=True)
    parent_id = Column(Integer, ForeignKey('comments.id'), nullable=True, index=True)
    reply_to_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    like_count = Column(Integer, default=0)
    reply_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    author = relationship('User', foreign_keys=[user_id], back_populates='comments')
    post = relationship('Post', back_populates='comments')
    parent = relationship('Comment', remote_side=[id], backref='replies')
    reply_to_user = relationship('User', foreign_keys=[reply_to_user_id])
    
    def to_dict(self, current_user_id=None):
        author_dict = self.author.to_dict() if self.author else None
        result = {
            'id': self.id,
            'content': self.content,
            'user_id': self.user_id,
            'post_id': self.post_id,
            'parent_id': self.parent_id,
            'reply_to_user_id': self.reply_to_user_id,
            'reply_to_user': self.reply_to_user.to_dict() if self.reply_to_user else None,
            'author': author_dict,
            'user': author_dict,  # 前端兼容
            'like_count': self.like_count,
            'reply_count': self.reply_count,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'replies': [],
            'replies_has_more': False,
            'replies_page': 1,
        }
        return result


class Like(Base):
    """点赞模型"""
    __tablename__ = 'likes'
    __table_args__ = (
        Index('idx_likes_comment_user', 'comment_id', 'user_id'),
        UniqueConstraint('user_id', 'post_id', name='unique_user_post_like'),
        Index('idx_comment_id', 'comment_id'),
    )
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    post_id = Column(Integer, ForeignKey('posts.id'), nullable=True, index=True)
    comment_id = Column(Integer, ForeignKey('comments.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship('User', back_populates='likes')
    post = relationship('Post', back_populates='likes')
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'post_id': self.post_id,
            'comment_id': self.comment_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'user': self.user.to_dict() if self.user else None,
        }


class Friendship(Base):
    """好友关系模型"""
    __tablename__ = 'friendships'
    __table_args__ = (
        UniqueConstraint('sender_id', 'receiver_id', name='unique_friendship'),
    )

    id = Column(Integer, primary_key=True)
    sender_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    receiver_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    status = Column(String(20), default='pending')  # pending, accepted, rejected, blocked
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    sender = relationship('User', foreign_keys=[sender_id], back_populates='friends_sent')
    receiver = relationship('User', foreign_keys=[receiver_id], back_populates='friends_received')
    
    def to_dict(self):
        return {
            'id': self.id,
            'sender_id': self.sender_id,
            'receiver_id': self.receiver_id,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Conversation(Base):
    """会话模型（支持一对一私信 + 社群群聊）"""
    __tablename__ = 'conversations'
    __table_args__ = (
        UniqueConstraint('user1_id', 'user2_id', name='unique_conversation'),  # 仅一对一生效
        Index('ix_conversations_type', 'type'),
    )

    id = Column(Integer, primary_key=True)
    type = Column(String(20), default='direct')       # direct（一对一）/ community（社群群聊）
    community_id = Column(Integer, ForeignKey('communities.id', name='fk_conversations_community'), nullable=True)
    user1_id = Column(Integer, ForeignKey('users.id'), nullable=True, index=True)   # 一对一用
    user2_id = Column(Integer, ForeignKey('users.id'), nullable=True, index=True)   # 一对一用
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_message_at = Column(DateTime)

    user1 = relationship('User', foreign_keys=[user1_id])
    user2 = relationship('User', foreign_keys=[user2_id])
    community = relationship('Community', foreign_keys=[community_id])
    messages = relationship('Message', back_populates='conversation', lazy='dynamic',
                            cascade='all, delete-orphan', order_by='Message.created_at')
    participants = relationship('ConversationParticipant', back_populates='conversation',
                                lazy='dynamic', cascade='all, delete-orphan')

    def to_dict(self):
        d = {
            'id': self.id,
            'type': self.type,
            'community_id': self.community_id,
            'user1_id': self.user1_id,
            'user2_id': self.user2_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'last_message_at': self.last_message_at.isoformat() if self.last_message_at else None,
        }
        if self.community and self.type == 'community':
            d['community_name'] = self.community.name
            d['community_avatar'] = self.community.avatar_url
        return d


class ConversationParticipant(Base):
    """会话参与者"""
    __tablename__ = 'conversation_participants'
    
    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey('conversations.id'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    joined_at = Column(DateTime, default=datetime.utcnow)
    
    conversation = relationship('Conversation', back_populates='participants')
    user = relationship('User')


class Message(Base):
    """消息模型"""
    __tablename__ = 'messages'
    __table_args__ = (
        Index('idx_messages_conv_time', 'conversation_id', 'created_at'),
        Index('ix_messages_created_at', 'created_at'),
    )
    
    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey('conversations.id'), nullable=False, index=True)
    sender_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    content = Column(Text)
    message_type = Column(String(20), default='text')  # text, image, video, post, system
    media_url = Column(String(255))
    related_id = Column(Integer)
    quote_message_id = Column(Integer, nullable=True)
    quote_preview = Column(Text, nullable=True)
    is_read = Column(Boolean, default=False)
    is_recalled = Column(Boolean, default=False)
    recalled_at = Column(DateTime, nullable=True)
    deleted_by_admin = Column(Boolean, default=False)     # 管理员删除他人消息标记
    created_at = Column(DateTime, default=datetime.utcnow)
    
    conversation = relationship('Conversation', back_populates='messages')
    sender = relationship('User')
    
    def to_dict(self):
        return {
            'id': self.id,
            'conversation_id': self.conversation_id,
            'sender_id': self.sender_id,
            'content': self.content,
            'message_type': self.message_type,
            'media_url': self.media_url,
            'related_id': self.related_id,
            'quote_message_id': self.quote_message_id,
            'quote_preview': self.quote_preview,
            'is_read': self.is_read,
            'is_recalled': self.is_recalled,
            'recalled_at': self.recalled_at.isoformat() if self.recalled_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Notification(Base):
    """通知模型"""
    __tablename__ = 'notifications'
    __table_args__ = (
        Index('ix_notifications_created_at', 'created_at'),
    )
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    sender_id = Column(Integer, ForeignKey('users.id'), nullable=True, index=True)
    notification_type = Column(String(50), nullable=False)
    title = Column(String(200))
    content = Column(Text)
    related_id = Column(Integer)
    related_type = Column(String(50))
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship('User', foreign_keys=[user_id])
    sender = relationship('User', foreign_keys=[sender_id])
    
    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'sender_id': self.sender_id,
            'notification_type': self.notification_type,
            'title': self.title,
            'content': self.content,
            'related_id': self.related_id,
            'related_type': self.related_type,
            'is_read': self.is_read,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Topic(Base):
    """话题模型"""
    __tablename__ = 'topics'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    description = Column(Text)
    icon_url = Column(String(255))
    color = Column(String(20))
    post_count = Column(Integer, default=0)
    follower_count = Column(Integer, default=0)
    is_trending = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    posts = relationship('Post', secondary=post_topics, backref='topics')
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'icon_url': self.icon_url,
            'color': self.color,
            'post_count': self.post_count,
            'follower_count': self.follower_count,
            'is_trending': self.is_trending,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class Block(Base):
    """屏蔽模型"""
    __tablename__ = 'blocks'
    __table_args__ = (
        UniqueConstraint('blocker_id', 'blocked_id', name='unique_block'),
    )

    id = Column(Integer, primary_key=True)
    blocker_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    blocked_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    blocker = relationship('User', foreign_keys=[blocker_id], back_populates='blocks')
    blocked = relationship('User', foreign_keys=[blocked_id])
    
    def to_dict(self):
        return {
            'id': self.id,
            'blocker_id': self.blocker_id,
            'blocked_id': self.blocked_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'blocked_user': self.blocked.to_dict() if self.blocked else None,
        }


class Report(Base):
    """举报模型"""
    __tablename__ = 'reports'
    __table_args__ = (
        Index('idx_reporter_target', 'reporter_id', 'target_type', 'target_id'),
        Index('ix_reports_created_at', 'created_at'),
        Index('ix_reports_target_id', 'target_id'),
    )
    
    id = Column(Integer, primary_key=True)
    reporter_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    target_type = Column(String(20), nullable=False)
    target_id = Column(Integer, nullable=False)
    reason = Column(String(200), nullable=False)
    description = Column(Text)
    status = Column(String(20), default='pending')
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime)
    
    reporter = relationship('User', foreign_keys=[reporter_id])
    
    def to_dict(self):
        return {
            'id': self.id,
            'reporter_id': self.reporter_id,
            'target_type': self.target_type,
            'target_id': self.target_id,
            'reason': self.reason,
            'description': self.description,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'resolved_at': self.resolved_at.isoformat() if self.resolved_at else None,
        }


class PostView(Base):
    """帖子浏览记录"""
    __tablename__ = 'post_views'
    __table_args__ = (
        UniqueConstraint('user_id', 'post_id', name='uq_user_post_view'),
    )
    
    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey('posts.id'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    post = relationship('Post')
    viewer = relationship('User', back_populates='post_views')


class SearchHistory(Base):
    """搜索历史模型"""
    __tablename__ = 'search_history'
    __table_args__ = (
        Index('idx_user_created', 'user_id', 'created_at'),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    query = Column(String(200), nullable=False)
    search_type = Column(String(50), default='global')
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship('User')

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'query': self.query,
            'search_type': self.search_type,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class SensitiveWord(Base):
    """敏感词模型"""
    __tablename__ = 'sensitive_words'

    id = Column(Integer, primary_key=True)
    word = Column(String(100), nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'word': self.word,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


# ============================================================
# 漫展相关模型（从 C:\PythonProject 迁移）
# ============================================================

class ComicCity(Base):
    """漫展城市"""
    __tablename__ = 'comic_cities'

    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False)
    province = Column(String(32), default='广西')
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class ComicTag(Base):
    """漫展标签"""
    __tablename__ = 'comic_tags'

    id = Column(Integer, primary_key=True)
    name = Column(String(64), nullable=False)
    tag_type = Column(String(32), default='type')  # type=展会类型, scale=展会规模
    created_at = Column(DateTime, default=datetime.utcnow)


class ComicEvent(Base):
    """漫展主表"""
    __tablename__ = 'comic_events'

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    city_id = Column(Integer, ForeignKey('comic_cities.id'), nullable=False)
    venue = Column(String(256), default='')
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    start_time = Column(String(8), default='09:00')
    end_time = Column(String(8), default='18:00')
    ticket_info = Column(String(256), default='')
    website = Column(String(512), default='')
    intro = Column(Text)
    status = Column(Integer, default=0)  # 0=即将开始, 1=进行中, 2=已结束
    like_count = Column(Integer, default=0)
    comment_count = Column(Integer, default=0)
    creator_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    city = relationship('ComicCity')
    creator = relationship('User')
    images = relationship('ComicEventImage', back_populates='event', cascade='all, delete-orphan')
    tag_rels = relationship('ComicEventTagRel', back_populates='event', cascade='all, delete-orphan')
    follows = relationship('ComicEventFollow', back_populates='event', cascade='all, delete-orphan')
    comments = relationship('ComicComment', back_populates='event', cascade='all, delete-orphan')
    likes = relationship('ComicLike', back_populates='event', cascade='all, delete-orphan')


class ComicEventImage(Base):
    """漫展图片"""
    __tablename__ = 'comic_event_images'

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey('comic_events.id', ondelete='CASCADE'), nullable=False)
    image_url = Column(String(512), nullable=False)
    is_cover = Column(Integer, default=0)  # TINYINT → Integer: 0/1
    sort_order = Column(Integer, default=0)

    event = relationship('ComicEvent', back_populates='images')


class ComicEventTagRel(Base):
    """漫展-标签关联"""
    __tablename__ = 'comic_event_tag_rel'

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey('comic_events.id', ondelete='CASCADE'), nullable=False)
    tag_id = Column(Integer, ForeignKey('comic_tags.id', ondelete='CASCADE'), nullable=False)

    event = relationship('ComicEvent', back_populates='tag_rels')
    tag = relationship('ComicTag')


class ComicEventFollow(Base):
    """漫展关注"""
    __tablename__ = 'comic_event_follows'

    id = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey('comic_events.id', ondelete='CASCADE'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    event = relationship('ComicEvent', back_populates='follows')
    user = relationship('User')


class ComicComment(Base):
    """漫展评论"""
    __tablename__ = 'comic_comments'
    __table_args__ = (
        Index('idx_comic_comments_event_parent', 'event_id', 'parent_id'),
        Index('idx_comic_comments_parent_created', 'parent_id', 'created_at'),
    )

    id = Column(Integer, primary_key=True)
    content = Column(Text, nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    event_id = Column(Integer, ForeignKey('comic_events.id'), nullable=False, index=True)
    parent_id = Column(Integer, ForeignKey('comic_comments.id'), nullable=True, index=True)
    reply_to_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    like_count = Column(Integer, default=0)
    reply_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    author = relationship('User', foreign_keys=[user_id])
    event = relationship('ComicEvent', back_populates='comments')
    parent = relationship('ComicComment', remote_side=[id], backref='replies')
    reply_to_user = relationship('User', foreign_keys=[reply_to_user_id])

    def to_dict(self, current_user_id=None):
        result = {
            'id': self.id,
            'content': self.content,
            'user_id': self.user_id,
            'event_id': self.event_id,
            'parent_id': self.parent_id,
            'reply_to_user_id': self.reply_to_user_id,
            'reply_to_user': self.reply_to_user.to_dict() if self.reply_to_user else None,
            'user': self.author.to_dict() if self.author else None,
            'like_count': self.like_count,
            'reply_count': self.reply_count,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'replies': [],
            'replies_has_more': False,
            'replies_page': 1,
        }
        return result


class ComicLike(Base):
    """漫展点赞"""
    __tablename__ = 'comic_likes'
    __table_args__ = (
        Index('idx_comic_likes_event_user', 'event_id', 'user_id', unique=True),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    event_id = Column(Integer, ForeignKey('comic_events.id'), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship('User')
    event = relationship('ComicEvent', back_populates='likes')


class ComicCommentLike(Base):
    """漫展评论点赞"""
    __tablename__ = 'comic_comment_likes'
    __table_args__ = (
        Index('idx_ccl_user_comment', 'user_id', 'comment_id', unique=True),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    comment_id = Column(Integer, ForeignKey('comic_comments.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


# ============================================================
# WebSocket 协议表（序号机制 + 断线补发 + ACK 去重）
# ============================================================

class WSMessageLog(Base):
    """消息日志 — 支持断线补发"""
    __tablename__ = 'ws_message_log'
    __table_args__ = (
        UniqueConstraint('user_id', 'seq', name='uq_ws_msg_user_seq'),
        Index('idx_ws_msg_user_seq', 'user_id', 'seq'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    seq = Column(Integer, nullable=False)
    payload = Column(Text, nullable=False)  # JSON string
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class WSUserSeq(Base):
    """每用户序号计数器"""
    __tablename__ = 'ws_user_seq'

    user_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    current_seq = Column(Integer, nullable=False, default=0)


class WSAckDedup(Base):
    """ACK 去重表（clientMsgId 幂等，定期清理 24h 前记录）"""
    __tablename__ = 'ws_ack_dedup'

    client_msg_id = Column(String(36), primary_key=True)  # UUID
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    message_id = Column(Integer, nullable=True)  # 首次处理时记录的 message_id，重复 ACK 时回传
    processed_at = Column(DateTime, nullable=False, default=datetime.utcnow)


# ============================================================
# 角色系统模型
# ============================================================

class Role(Base):
    """角色定义表"""
    __tablename__ = 'roles'

    id = Column(Integer, primary_key=True)
    name = Column(String(32), unique=True, nullable=False)    # admin / organizer / coser / ...
    label = Column(String(32), nullable=False)                # 管理员 / 主办方 / Coser / ...
    description = Column(String(128), default='')
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'label': self.label,
            'description': self.description,
            'sort_order': self.sort_order,
            'is_business_identity': is_business_identity_role(self.name),
        }


class UserRole(Base):
    """用户-角色关联表（多对多）"""
    __tablename__ = 'user_roles'

    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    role_id = Column(Integer, ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship('User', back_populates='user_roles')
    role = relationship('Role')


class CoserProfile(Base):
    """Coser 专属资料"""
    __tablename__ = 'coser_profiles'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), unique=True, nullable=False)
    cosname = Column(String(64), default='')
    bio = Column(Text)
    styles = Column(String(512), default='')
    city = Column(String(32), default='')
    is_available = Column(Boolean, default=True)
    price_range_min = Column(Integer, default=0)
    price_range_max = Column(Integer, default=0)
    portfolio_images = Column(Text)
    social_links = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship('User')

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'cosname': self.cosname,
            'bio': self.bio,
            'styles': self.styles.split(',') if self.styles else [],
            'city': self.city,
            'is_available': self.is_available,
            'price_range_min': self.price_range_min,
            'price_range_max': self.price_range_max,
            'portfolio_images': self.portfolio_images,
            'social_links': self.social_links,
        }


class PhotographerProfile(Base):
    """摄影师专属资料"""
    __tablename__ = 'photographer_profiles'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), unique=True, nullable=False)
    equipment = Column(String(512), default='')
    styles = Column(String(512), default='')
    city = Column(String(32), default='')
    is_available = Column(Boolean, default=True)
    price_range_min = Column(Integer, default=0)
    price_range_max = Column(Integer, default=0)
    portfolio_images = Column(Text)
    social_links = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship('User')

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'equipment': self.equipment,
            'styles': self.styles.split(',') if self.styles else [],
            'city': self.city,
            'is_available': self.is_available,
            'price_range_min': self.price_range_min,
            'price_range_max': self.price_range_max,
            'portfolio_images': self.portfolio_images,
            'social_links': self.social_links,
        }


class ServiceProfile(Base):
    """通用服务商资料（毛娘 / 妆娘 / 后期师 / 票务代理）"""
    __tablename__ = 'service_profiles'
    __table_args__ = (
        UniqueConstraint('user_id', 'service_type', name='uq_service_profiles_user_type'),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    service_type = Column(String(32), nullable=False)  # wig_stylist / makeup_artist / retoucher / ticket_agent / prop_maker / costume_maker
    description = Column(Text)
    city = Column(String(32), default='')
    is_available = Column(Boolean, default=True)
    price_info = Column(String(512), default='')
    portfolio_images = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship('User')

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'service_type': self.service_type,
            'description': self.description,
            'city': self.city,
            'is_available': self.is_available,
            'price_info': self.price_info,
            'portfolio_images': self.portfolio_images,
        }


class RoleApplication(Base):
    """角色申请表"""
    __tablename__ = 'role_applications'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True)
    role_id = Column(Integer, ForeignKey('roles.id', ondelete='CASCADE'), nullable=False)
    status = Column(String(16), default='pending')   # pending / verified / rejected / suspended
    reason = Column(Text)
    application_text = Column(Text)
    proof_images = Column(Text)
    portfolio_links = Column(Text)
    contact_info = Column(String(255))
    extra_note = Column(Text)
    review_comment = Column(Text)
    reviewer_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    reviewed_at = Column(DateTime, nullable=True)

    user = relationship('User', foreign_keys=[user_id])
    role = relationship('Role')
    reviewer = relationship('User', foreign_keys=[reviewer_id])

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'role_id': self.role_id,
            'role': self.role.to_dict() if self.role else None,
            'status': 'verified' if self.status == 'approved' else self.status,
            'reason': self.reason,
            'application_text': self.application_text or self.reason,
            'proof_images': _json_list(self.proof_images),
            'portfolio_links': _json_list(self.portfolio_links),
            'contact_info': self.contact_info,
            'extra_note': self.extra_note,
            'review_comment': self.review_comment,
            'reviewer_id': self.reviewer_id,
            'user': self.user.to_dict() if self.user else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'reviewed_at': self.reviewed_at.isoformat() if self.reviewed_at else None,
        }


class UserDevice(Base):
    """用户设备 - 存储极光推送 registrationId，支持多设备登录"""
    __tablename__ = 'user_devices'

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    registration_id = Column(String(255), nullable=False, index=True)
    platform = Column(String(20), default='android')  # android / ios / harmony
    app_version = Column(String(50))
    last_active_at = Column(DateTime, default=datetime.utcnow)
    app_state = Column(String(20), default='unknown')  # foreground / background / unknown
    app_state_updated_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'registration_id': self.registration_id,
            'platform': self.platform,
            'app_version': self.app_version,
            'last_active_at': self.last_active_at.isoformat() if self.last_active_at else None,
            'app_state': self.app_state,
            'app_state_updated_at': self.app_state_updated_at.isoformat() if self.app_state_updated_at else None,
            'is_active': self.is_active,
        }


class EmailOtp(Base):
    """邮箱验证码 - 用于注册验证、忘记密码、登录限流验证"""
    __tablename__ = 'email_otps'

    id = Column(Integer, primary_key=True)
    email = Column(String(120), nullable=False, index=True)
    code = Column(String(6), nullable=False)
    purpose = Column(String(20), nullable=False)  # register / reset_password / login
    is_used = Column(Boolean, default=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    ip_address = Column(String(45))  # 审计/限流用


class LoginAttempt(Base):
    """登录失败记录 - 用于登录错误限流（连续失败 N 次要求邮箱验证码）"""
    __tablename__ = 'login_attempts'

    id = Column(Integer, primary_key=True)
    identifier = Column(String(120), nullable=False, index=True)  # username 或 email
    ip_address = Column(String(45), nullable=False, index=True)
    success = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
