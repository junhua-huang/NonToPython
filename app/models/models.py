"""
FastAPI 版本 - 数据模型定义
从 Flask-SQLAlchemy 迁移至 SQLAlchemy 2.0+ Declarative
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, Float, ForeignKey,
    Table, Index, func
)
from sqlalchemy.orm import relationship
from app.database import Base


# ============================================================
# 关联表
# ============================================================

post_topics = Table(
    'post_topics', Base.metadata,
    Column('post_id', Integer, ForeignKey('posts.id', ondelete='CASCADE'), primary_key=True),
    Column('topic_id', Integer, ForeignKey('topics.id', ondelete='CASCADE'), primary_key=True),
    Column('created_at', DateTime, default=datetime.utcnow),
)

post_visibility = Table(
    'post_visibility', Base.metadata,
    Column('post_id', Integer, ForeignKey('posts.id', ondelete='CASCADE'), primary_key=True),
    Column('user_id', Integer, ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
)

topic_followers = Table(
    'topic_followers', Base.metadata,
    Column('user_id', Integer, ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    Column('topic_id', Integer, ForeignKey('topics.id', ondelete='CASCADE'), primary_key=True),
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
    posts = relationship('Post', back_populates='author', lazy='dynamic', cascade='all, delete-orphan')
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
    
    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'display_name': self.username,
            'bio': self.bio,
            'avatar_url': self.avatar_url,
            'cover_photo_url': self.cover_photo_url,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Post(Base):
    """帖子模型"""
    __tablename__ = 'posts'
    
    id = Column(Integer, primary_key=True)
    content = Column(Text, nullable=False)
    images = Column(Text)  # JSON array string for multi-image support
    video_url = Column(String(255))
    post_type = Column(String(20), default='text')
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    visibility = Column(String(20), default='public')
    is_public = Column(Boolean, default=True)
    view_count = Column(Integer, default=0)
    
    # 关系
    author = relationship('User', back_populates='posts')
    comments = relationship('Comment', back_populates='post', lazy='dynamic', cascade='all, delete-orphan')
    likes = relationship('Like', back_populates='post', lazy='dynamic', cascade='all, delete-orphan')
    
    def get_like_count(self):
        return self.likes.count()
    
    def get_comment_count(self):
        return self.comments.count()
    
    def get_topics(self):
        from app.database import SessionLocal
        db = SessionLocal()
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
            db.close()
    
    def to_dict(self, current_user_id=None, like_count=None, comment_count=None, topics=None, is_liked=None):
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

        result = {
            'id': self.id,
            'content': self.content,
            'images': images_list,
            'image_urls': images_list,
            'video_url': self.video_url,
            'post_type': self.post_type,
            'user_id': self.user_id,
            'author': self.author.to_dict() if self.author else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'visibility': self.visibility,
            'is_public': self.is_public,
            'like_count': like_count if like_count is not None else self.get_like_count(),
            'comment_count': comment_count if comment_count is not None else self.get_comment_count(),
            'view_count': self.view_count,
            'topics': topics if topics is not None else self.get_topics(),
            'is_liked': is_liked if is_liked is not None else False,
        }
        return result


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
        result = {
            'id': self.id,
            'content': self.content,
            'user_id': self.user_id,
            'post_id': self.post_id,
            'parent_id': self.parent_id,
            'reply_to_user_id': self.reply_to_user_id,
            'like_count': self.like_count,
            'reply_count': self.reply_count,
            'author': self.author.to_dict() if self.author else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }
        return result


class Like(Base):
    """点赞模型"""
    __tablename__ = 'likes'
    __table_args__ = (
        Index('idx_likes_comment_user', 'comment_id', 'user_id'),
    )
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    post_id = Column(Integer, ForeignKey('posts.id'), nullable=True, index=True)
    comment_id = Column(Integer, ForeignKey('comments.id'), nullable=True, index=True)
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
    """会话模型"""
    __tablename__ = 'conversations'
    
    id = Column(Integer, primary_key=True)
    user1_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    user2_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_message_at = Column(DateTime)
    
    user1 = relationship('User', foreign_keys=[user1_id])
    user2 = relationship('User', foreign_keys=[user2_id])
    messages = relationship('Message', back_populates='conversation', lazy='dynamic',
                            cascade='all, delete-orphan', order_by='Message.created_at')
    participants = relationship('ConversationParticipant', back_populates='conversation',
                                lazy='dynamic', cascade='all, delete-orphan')
    
    def to_dict(self):
        return {
            'id': self.id,
            'user1_id': self.user1_id,
            'user2_id': self.user2_id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'last_message_at': self.last_message_at.isoformat() if self.last_message_at else None,
        }


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
    
    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey('conversations.id'), nullable=False, index=True)
    sender_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    content = Column(Text)
    message_type = Column(String(20), default='text')  # text, image, system
    media_url = Column(String(255))
    related_id = Column(Integer)
    is_read = Column(Boolean, default=False)
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
            'is_read': self.is_read,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class Notification(Base):
    """通知模型"""
    __tablename__ = 'notifications'
    
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
    
    id = Column(Integer, primary_key=True)
    reporter_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    target_type = Column(String(20), nullable=False)
    target_id = Column(Integer, nullable=False)
    reason = Column(String(50), nullable=False)
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
    creator_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    city = relationship('ComicCity')
    creator = relationship('User')
    images = relationship('ComicEventImage', back_populates='event', cascade='all, delete-orphan')
    tag_rels = relationship('ComicEventTagRel', back_populates='event', cascade='all, delete-orphan')
    follows = relationship('ComicEventFollow', back_populates='event', cascade='all, delete-orphan')


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
