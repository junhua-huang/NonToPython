"""
搜索服务 - FastAPI 重构版 (Session 参数传递模式)
"""
from sqlalchemy import or_, and_, func
from sqlalchemy.orm import Session
from app.models.models import User, Post, ComicEvent, ComicCity
from app.services.post_visibility_service import post_visibility_predicate
from app.services.block_service import visible_user_predicate


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
            or_(
                User.username.ilike(search_term),
                and_(User.show_email.is_(True), or_(*email_conditions)),
            ),
            User.is_active == True,
            User.allow_search == True,
            visible_user_predicate(current_user_id, User.id) if current_user_id is not None else True,
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
        return RecommendationService._serialize_posts(posts, batch_data, current_user_id=user_id, db=db)

    @staticmethod
    def search_posts(
        db: Session,
        query: str,
        page: int = 1,
        per_page: int = 20,
        user_id: int = None,
        is_public: bool = True,
        current_user_id: int = None,
    ):
        if not query or len(query.strip()) < 1:
            return {
                'posts': [], 'total': 0, 'pages': 0,
                'current_page': page, 'per_page': per_page
            }

        search_term = f'%{query}%'
        post_query = db.query(Post).filter(
            Post.content.ilike(search_term),
            post_visibility_predicate(current_user_id),
        )
        if user_id:
            post_query = post_query.filter(Post.user_id == user_id)

        post_query = post_query.order_by(Post.created_at.desc())
        total = post_query.count()
        posts = post_query.offset((page - 1) * per_page).limit(per_page).all()
        pages = (total + per_page - 1) // per_page if total > 0 else 0

        return {
            'posts': SearchService._batch_serialize_posts(db, posts, current_user_id),
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
        post_results = SearchService.search_posts(
            db,
            query,
            page,
            per_page,
            is_public=True,
            current_user_id=current_user_id,
        )
        event_results = SearchService.search_comic_events(db, query, per_page=per_page, user_id=current_user_id)

        return {
            'query': query,
            'users': user_results['users'],
            'posts': post_results['posts'],
            'events': event_results['events'],
            'user_total': user_results['total'],
            'post_total': post_results['total'],
            'event_total': event_results['total'],
            'total_results': user_results['total'] + post_results['total'] + event_results['total'],
            'current_page': page,
            'per_page': per_page
        }

    @staticmethod
    def search_comic_events(db: Session, query: str, per_page: int = 10, user_id: int = None):
        """模糊搜索漫展：名称 / 场馆 / 城市名"""
        if not query or len(query.strip()) < 1:
            return {'events': [], 'total': 0}

        from datetime import datetime
        search_term = f'%{query}%'
        now = datetime.utcnow()

        events_query = (
            db.query(ComicEvent)
            .join(ComicCity, ComicEvent.city_id == ComicCity.id)
            .filter(
                or_(
                    ComicEvent.name.ilike(search_term),
                    ComicEvent.venue.ilike(search_term),
                    ComicCity.name.ilike(search_term),
                ),
                ComicEvent.end_date >= now,
            )
            .order_by(ComicEvent.start_date.asc())
            .limit(per_page)
        )

        total = events_query.count()
        events = events_query.all()

        if not events:
            return {'events': [], 'total': 0}

        event_ids = [e.id for e in events]
        city_map = {c.id: c.name for c in db.query(ComicCity).filter(ComicCity.id.in_({e.city_id for e in events})).all()}

        from app.models.models import ComicEventImage, ComicEventFollow
        images_map = {}
        imgs = db.query(ComicEventImage).filter(
            ComicEventImage.event_id.in_(event_ids)
        ).order_by(ComicEventImage.is_cover.desc(), ComicEventImage.sort_order.asc()).all()
        for img in imgs:
            images_map.setdefault(img.event_id, []).append({
                "id": img.id, "imageUrl": img.image_url,
                "isCover": bool(img.is_cover), "sortOrder": img.sort_order,
            })

        follow_set = set()
        follow_count_map = {}
        if user_id:
            follows = db.query(ComicEventFollow).filter(
                ComicEventFollow.event_id.in_(event_ids),
                ComicEventFollow.user_id == user_id,
            ).all()
            follow_set = {f.event_id for f in follows}

        from sqlalchemy import text
        follow_counts = db.execute(text(
            "SELECT event_id, COUNT(*) FROM comic_event_follows "
            "WHERE event_id IN :eids GROUP BY event_id"
        ), {"eids": tuple(event_ids)}).fetchall()
        for fc in follow_counts:
            follow_count_map[fc[0]] = fc[1]

        # 实时重算状态：数据库里的 status 是创建时算的，不会随时间变化，
        # 必须按当前日期重新计算，否则已结束的漫展会一直显示"即将开始"。
        from app.routers.comic import _recalc_status_text

        result = []
        for e in events:
            real_status, real_status_text = _recalc_status_text(e.start_date, e.end_date)
            result.append({
                "id": e.id,
                "name": e.name,
                "cityName": city_map.get(e.city_id, ""),
                "venue": e.venue or "",
                "startDate": e.start_date.isoformat() if e.start_date else None,
                "endDate": e.end_date.isoformat() if e.end_date else None,
                "status": real_status,
                "statusText": real_status_text,
                "ticketInfo": e.ticket_info or "",
                "images": images_map.get(e.id, []),
                "isFollowed": e.id in follow_set,
                "followCount": follow_count_map.get(e.id, 0),
                "createdAt": e.created_at.isoformat() if e.created_at else None,
            })

        return {'events': result, 'total': total}

    @staticmethod
    def search_posts_by_hashtag(
        db: Session,
        hashtag: str,
        page: int = 1,
        per_page: int = 20,
        current_user_id: int = None,
    ):
        if not hashtag:
            return {
                'posts': [], 'total': 0, 'pages': 0,
                'current_page': page, 'per_page': per_page
            }

        search_term = f'%#{hashtag}%'
        post_query = db.query(Post).filter(
            Post.content.ilike(search_term),
            post_visibility_predicate(current_user_id),
        ).order_by(Post.created_at.desc())

        total = post_query.count()
        posts = post_query.offset((page - 1) * per_page).limit(per_page).all()
        pages = (total + per_page - 1) // per_page if total > 0 else 0

        return {
            'hashtag': hashtag,
            'posts': SearchService._batch_serialize_posts(db, posts, current_user_id),
            'total': total,
            'pages': pages,
            'current_page': page,
            'per_page': per_page
        }

    @staticmethod
    def get_trending_hashtags(db: Session, limit: int = 10, current_user_id: int = None):
        recent_posts = db.query(Post).filter(
            post_visibility_predicate(current_user_id)
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
            User.allow_search == True,
            visible_user_predicate(current_user_id, User.id) if current_user_id is not None else True,
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