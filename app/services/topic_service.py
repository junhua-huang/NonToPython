"""
话题服务 - FastAPI 重构版（Session 参数传递模式）
"""
from app.models.models import Topic, Post, User, post_topics, topic_followers
from sqlalchemy import func, text
from sqlalchemy.orm import Session
from datetime import datetime, timedelta


class TopicService:
    """话题服务类"""

    @staticmethod
    def create_topic(db: Session, name: str, description: str = None, icon_url: str = None, color: str = '#3b82f6'):
        existing = db.query(Topic).filter(func.lower(Topic.name) == func.lower(name)).first()
        if existing:
            return existing

        topic = Topic(name=name.strip(), description=description, icon_url=icon_url, color=color)
        try:
            db.add(topic)
            db.flush()
            return topic
        except Exception as e:
            print(f"Error creating topic: {e}")
            return None

    @staticmethod
    def get_topic_by_name(db: Session, name: str):
        return db.query(Topic).filter(func.lower(Topic.name) == func.lower(name)).first()

    @staticmethod
    def get_topic_by_id(db: Session, topic_id: int):
        return db.query(Topic).filter(Topic.id == topic_id).first()

    @staticmethod
    def add_post_to_topic(db: Session, post_id: int, topic_id: int):
        try:
            existing = db.execute(
                text('SELECT 1 FROM post_topics WHERE post_id = :post_id AND topic_id = :topic_id'),
                {'post_id': post_id, 'topic_id': topic_id}
            ).first()
            if existing:
                return True

            db.execute(
                text('INSERT INTO post_topics (post_id, topic_id, created_at) VALUES (:post_id, :topic_id, :created_at)'),
                {'post_id': post_id, 'topic_id': topic_id, 'created_at': datetime.utcnow()}
            )
            topic = db.query(Topic).filter(Topic.id == topic_id).first()
            if topic:
                topic.post_count = TopicService.get_topic_post_count(db, topic_id)
            db.flush()
            return True
        except Exception as e:
            print(f"Error adding post to topic: {e}")
            return False

    @staticmethod
    def remove_post_from_topic(db: Session, post_id: int, topic_id: int):
        try:
            db.execute(
                text('DELETE FROM post_topics WHERE post_id = :post_id AND topic_id = :topic_id'),
                {'post_id': post_id, 'topic_id': topic_id}
            )
            topic = db.query(Topic).filter(Topic.id == topic_id).first()
            if topic:
                topic.post_count = TopicService.get_topic_post_count(db, topic_id)
            db.flush()
            return True
        except Exception as e:
            print(f"Error removing post from topic: {e}")
            return False

    @staticmethod
    def get_topic_posts(db: Session, topic_id: int, page: int = 1, per_page: int = 20):
        offset = (page - 1) * per_page
        total = db.execute(
            text('SELECT COUNT(*) FROM post_topics WHERE topic_id = :topic_id'),
            {'topic_id': topic_id}
        ).scalar() or 0

        result = db.execute(
            text('''SELECT p.id FROM posts p 
                JOIN post_topics pt ON p.id = pt.post_id 
                WHERE pt.topic_id = :topic_id AND p.is_public = true
                ORDER BY p.created_at DESC
                LIMIT :limit OFFSET :offset'''),
            {'topic_id': topic_id, 'limit': per_page, 'offset': offset}
        )
        post_ids = [row[0] for row in result]
        posts = db.query(Post).filter(Post.id.in_(post_ids)).all() if post_ids else []
        post_map = {p.id: p for p in posts}
        posts = [post_map[pid] for pid in post_ids if pid in post_map]

        from app.services.recommendation_service import RecommendationService
        batch_data = RecommendationService._batch_load_post_data(db, posts, None)

        return {
            'posts': RecommendationService._serialize_posts(posts, batch_data),
            'total': total,
            'pages': (total + per_page - 1) // per_page if total > 0 else 0,
            'current_page': page,
            'per_page': per_page
        }

    @staticmethod
    def get_topic_post_count(db: Session, topic_id: int):
        result = db.execute(
            text('SELECT COUNT(*) FROM post_topics WHERE topic_id = :topic_id'),
            {'topic_id': topic_id}
        ).scalar()
        return result or 0

    @staticmethod
    def follow_topic(db: Session, user_id: int, topic_id: int):
        try:
            existing = db.execute(
                text('SELECT 1 FROM topic_followers WHERE user_id = :user_id AND topic_id = :topic_id'),
                {'user_id': user_id, 'topic_id': topic_id}
            ).first()
            if existing:
                return True

            db.execute(
                text('INSERT INTO topic_followers (user_id, topic_id, created_at) VALUES (:user_id, :topic_id, :created_at)'),
                {'user_id': user_id, 'topic_id': topic_id, 'created_at': datetime.utcnow()}
            )
            topic = db.query(Topic).filter(Topic.id == topic_id).first()
            if topic:
                topic.follower_count = TopicService.get_topic_follower_count(db, topic_id)
            db.flush()
            return True
        except Exception as e:
            print(f"Error following topic: {e}")
            return False

    @staticmethod
    def unfollow_topic(db: Session, user_id: int, topic_id: int):
        try:
            db.execute(
                text('DELETE FROM topic_followers WHERE user_id = :user_id AND topic_id = :topic_id'),
                {'user_id': user_id, 'topic_id': topic_id}
            )
            topic = db.query(Topic).filter(Topic.id == topic_id).first()
            if topic:
                topic.follower_count = TopicService.get_topic_follower_count(db, topic_id)
            db.flush()
            return True
        except Exception as e:
            print(f"Error unfollowing topic: {e}")
            return False

    @staticmethod
    def get_topic_follower_count(db: Session, topic_id: int):
        result = db.execute(
            text('SELECT COUNT(*) FROM topic_followers WHERE topic_id = :topic_id'),
            {'topic_id': topic_id}
        ).scalar()
        return result or 0

    @staticmethod
    def is_user_following_topic(db: Session, user_id: int, topic_id: int):
        result = db.execute(
            text('SELECT 1 FROM topic_followers WHERE user_id = :user_id AND topic_id = :topic_id'),
            {'user_id': user_id, 'topic_id': topic_id}
        ).first()
        return result is not None

    @staticmethod
    def get_user_followed_topics(db: Session, user_id: int, page: int = 1, per_page: int = 20):
        offset = (page - 1) * per_page
        total = db.execute(
            text('SELECT COUNT(*) FROM topic_followers WHERE user_id = :user_id'),
            {'user_id': user_id}
        ).scalar() or 0

        result = db.execute(
            text('''SELECT t.id FROM topics t
                JOIN topic_followers tf ON t.id = tf.topic_id
                WHERE tf.user_id = :user_id
                ORDER BY tf.created_at DESC
                LIMIT :limit OFFSET :offset'''),
            {'user_id': user_id, 'limit': per_page, 'offset': offset}
        )
        topic_ids = [row[0] for row in result]
        topics = db.query(Topic).filter(Topic.id.in_(topic_ids)).all() if topic_ids else []
        topic_map = {t.id: t for t in topics}
        topics = [topic_map[tid] for tid in topic_ids if tid in topic_map]

        return {
            'topics': [topic.to_dict() for topic in topics],
            'total': total,
            'pages': (total + per_page - 1) // per_page if total > 0 else 0,
            'current_page': page,
            'per_page': per_page
        }

    @staticmethod
    def get_trending_topics(db: Session, limit: int = 10):
        seven_days_ago = datetime.utcnow() - timedelta(days=7)
        result = db.execute(
            text('''SELECT t.id, COUNT(pt.post_id) as recent_post_count
                FROM topics t
                JOIN post_topics pt ON t.id = pt.topic_id
                JOIN posts p ON pt.post_id = p.id
                WHERE p.created_at >= :seven_days_ago AND p.is_public = true
                GROUP BY t.id
                ORDER BY recent_post_count DESC
                LIMIT :limit'''),
            {'seven_days_ago': seven_days_ago, 'limit': limit}
        )
        trending = []
        for row in result:
            topic = db.query(Topic).filter(Topic.id == row[0]).first()
            if topic:
                topic_data = topic.to_dict()
                topic_data['recent_post_count'] = row[1]
                trending.append(topic_data)
        return trending

    @staticmethod
    def get_all_topics(db: Session, page: int = 1, per_page: int = 20, search_query: str = None, current_user_id: int = None):
        query = db.query(Topic)
        if search_query:
            search_term = f'%{search_query}%'
            query = query.filter(Topic.name.ilike(search_term) | Topic.description.ilike(search_term))
        query = query.order_by(Topic.follower_count.desc(), Topic.post_count.desc())

        total = query.count()
        topics = query.offset((page - 1) * per_page).limit(per_page).all()

        return {
            'topics': [topic.to_dict() for topic in topics],
            'total': total,
            'pages': (total + per_page - 1) // per_page if total > 0 else 0,
            'current_page': page,
            'per_page': per_page
        }

    @staticmethod
    def extract_hashtags_from_content(content: str):
        import re
        hashtags = re.findall(r'#([\w\u4e00-\u9fa5]+)', content)
        return [tag.lower() for tag in hashtags]

    @staticmethod
    def auto_link_topics(db: Session, post_id: int, content: str):
        hashtags = TopicService.extract_hashtags_from_content(content)
        linked = []
        for hashtag in hashtags:
            topic = TopicService.get_topic_by_name(db, hashtag)
            if not topic:
                topic = TopicService.create_topic(db, name=hashtag, description=f'关于 #{hashtag} 的讨论', color=TopicService.generate_random_color())
            if topic and TopicService.add_post_to_topic(db, post_id, topic.id):
                linked.append(topic)
        return linked

    @staticmethod
    def generate_random_color():
        import random
        colors = ['#3b82f6', '#ef4444', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4', '#84cc16']
        return random.choice(colors)

    @staticmethod
    def update_topic_stats(db: Session, topic_id: int):
        topic = db.query(Topic).filter(Topic.id == topic_id).first()
        if not topic:
            return False
        try:
            topic.post_count = TopicService.get_topic_post_count(db, topic_id)
            topic.follower_count = TopicService.get_topic_follower_count(db, topic_id)
            db.flush()
            return True
        except Exception as e:
            print(f"Error updating topic stats: {e}")
            return False