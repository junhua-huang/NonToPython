"""
搜索服务 - FastAPI 重构版 (Session 参数传递模式)
"""
from sqlalchemy import or_, and_, func
from sqlalchemy.orm import Session
from app.models.models import User, Post


class SearchService:
    """搜索服务类"""

    @staticmethod
    def search_users(db: Session, query: str, page: int = 1, per_page: int = 20, current_user_id: int = None):
        if not query or len(query.strip()) < 1:
            return {
                'users': [], 'total': 0, 'pages': 0,
                'current_page': page, 'per_page': per_page
            }

        search_term = f'%{query}%'
        email_conditions = [User.email.ilike(search_term)]

        if '@' in query:
            parts = query.split('@')
            if len(parts) == 2:
                username_part, domain_part = parts[0], parts[1]
                if username_part:
                    email_conditions.append(User.email.ilike(f'%{username_part}%'))
                if domain_part:
                    email_conditions.append(User.email.ilike(f'%{domain_part}%'))

        user_query = db.query(User).filter(
            or_(User.username.ilike(search_term), *email_conditions),
            User.is_active == True,
            User.allow_search == True
        ).order_by(
            User.username.ilike(search_term).desc(),
            User.created_at.desc()
        )

        total = user_query.count()
        users = user_query.offset((page - 1) * per_page).limit(per_page).all()
        pages = (total + per_page - 1) // per_page if total > 0 else 0

        return {
            'users': [user.to_dict() for user in users],
            'total': total,
            'pages': pages,
            'current_page': page,
            'per_page': per_page
        }

    @staticmethod
    def _batch_serialize_posts(db, posts, user_id=None):
        """批量序列化帖子，避免 N+1"""
        if not posts:
            return []
        from app.services.recommendation_service import RecommendationService
        batch_data = RecommendationService._batch_load_post_data(db, posts, user_id)
        return RecommendationService._serialize_posts(posts, batch_data)

    @staticmethod
    def search_posts(db: Session, query: str, page: int = 1, per_page: int = 20, user_id: int = None, is_public: bool = True):
        if not query or len(query.strip()) < 1:
            return {
                'posts': [], 'total': 0, 'pages': 0,
                'current_page': page, 'per_page': per_page
            }

        search_term = f'%{query}%'
        post_query = db.query(Post).filter(
            Post.content.ilike(search_term),
            Post.is_public == is_public
        )
        if user_id:
            post_query = post_query.filter(Post.user_id == user_id)

        post_query = post_query.order_by(Post.created_at.desc())
        total = post_query.count()
        posts = post_query.offset((page - 1) * per_page).limit(per_page).all()
        pages = (total + per_page - 1) // per_page if total > 0 else 0

        return {
            'posts': SearchService._batch_serialize_posts(db, posts),
            'total': total,
            'pages': pages,
            'current_page': page,
            'per_page': per_page
        }

    @staticmethod
    def global_search(db: Session, query: str, page: int = 1, per_page: int = 10, current_user_id: int = None):
        if not query or len(query.strip()) < 1:
            return {
                'query': query, 'users': [], 'posts': [],
                'user_total': 0, 'post_total': 0, 'total_results': 0
            }

        user_results = SearchService.search_users(db, query, page, per_page, current_user_id)
        post_results = SearchService.search_posts(db, query, page, per_page, is_public=True)

        return {
            'query': query,
            'users': user_results['users'],
            'posts': post_results['posts'],
            'user_total': user_results['total'],
            'post_total': post_results['total'],
            'total_results': user_results['total'] + post_results['total'],
            'current_page': page,
            'per_page': per_page
        }

    @staticmethod
    def search_posts_by_hashtag(db: Session, hashtag: str, page: int = 1, per_page: int = 20):
        if not hashtag:
            return {
                'posts': [], 'total': 0, 'pages': 0,
                'current_page': page, 'per_page': per_page
            }

        search_term = f'%#{hashtag}%'
        post_query = db.query(Post).filter(
            Post.content.ilike(search_term),
            Post.is_public == True
        ).order_by(Post.created_at.desc())

        total = post_query.count()
        posts = post_query.offset((page - 1) * per_page).limit(per_page).all()
        pages = (total + per_page - 1) // per_page if total > 0 else 0

        return {
            'hashtag': hashtag,
            'posts': [post.to_dict() for post in posts],
            'total': total,
            'pages': pages,
            'current_page': page,
            'per_page': per_page
        }

    @staticmethod
    def get_trending_hashtags(db: Session, limit: int = 10):
        recent_posts = db.query(Post).filter(
            Post.is_public == True
        ).order_by(Post.created_at.desc()).limit(1000).all()

        hashtag_count = {}
        for post in recent_posts:
            if not post.content:
                continue
            words = post.content.split()
            for word in words:
                if word.startswith('#') and len(word) > 1:
                    hashtag = word[1:].lower()
                    hashtag_count[hashtag] = hashtag_count.get(hashtag, 0) + 1

        trending = sorted(hashtag_count.items(), key=lambda x: x[1], reverse=True)[:limit]
        return [{'hashtag': tag, 'count': count} for tag, count in trending]

    @staticmethod
    def suggest_users(db: Session, prefix: str, limit: int = 5, current_user_id: int = None):
        if not prefix or len(prefix) < 1:
            return []

        search_term = f'{prefix}%'
        users = db.query(User).filter(
            or_(User.username.ilike(search_term)),
            User.is_active == True,
            User.allow_search == True
        ).limit(limit).all()

        return [
            {
                'id': user.id,
                'username': user.username,
                'full_name': user.username,
                'avatar_url': user.avatar_url
            }
            for user in users
        ]