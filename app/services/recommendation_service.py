"""
推荐算法服务 - FastAPI 重构版（Session 参数传递模式）
"""
import base64
import binascii
import json
import logging

from app.models.models import Post, User, Like, Comment, Friendship, Block, PostFeedSeen
from app.services.block_service import excluded_user_ids, visible_user_predicate
from app.services.post_visibility_service import post_visibility_predicate
from sqlalchemy import func, case, or_, and_, exists
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload
from datetime import datetime, timedelta


logger = logging.getLogger(__name__)


class RecommendationService:
    """推荐算法服务"""

    HOME_FEED_SEEN_SOURCE = 'home_feed'
    HOME_FEED_CURSOR_VERSION = 1
    HOME_FEED_SEEN_TTL_DAYS = 7
    HOME_FEED_PRIMARY_WINDOW_DAYS = 30
    HOME_FEED_FALLBACK_WINDOW_DAYS = 90
    MAX_HOME_FEED_PAGE = 50
    MAX_HOME_FEED_PER_PAGE = 20

    @staticmethod
    def _encode_feed_cursor(cursor_data: dict) -> str:
        """Encode cursor data as URL-safe base64 JSON."""
        payload = json.dumps(cursor_data, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
        return base64.urlsafe_b64encode(payload.encode('utf-8')).decode('ascii')

    @staticmethod
    def _decode_feed_cursor(cursor: str | None) -> dict | None:
        """Decode a feed cursor; invalid cursors are ignored as a fresh first page."""
        if not cursor:
            return None
        try:
            raw = base64.urlsafe_b64decode(cursor.encode('ascii')).decode('utf-8')
            data = json.loads(raw)
        except (ValueError, TypeError, json.JSONDecodeError, binascii.Error):
            return None

        if data.get('version') != RecommendationService.HOME_FEED_CURSOR_VERSION:
            return None
        required_fields = {'last_score', 'last_created_at', 'last_post_id', 'window_stage', 'issued_at'}
        if not required_fields.issubset(data.keys()):
            return None
        return data

    # ================================================================
    # 批量数据加载（消除 N+1）
    # ================================================================
    @staticmethod
    def _batch_load_post_data(db: Session, posts: list, current_user_id: int) -> dict:
        """批量加载帖子的聚合数据，返回 {post_id: {like_count, comment_count, topics, is_liked}}"""
        if not posts:
            return {}

        post_ids = [p.id for p in posts]

        # 批量查询 like/comment 统计
        like_counts = dict(
            db.query(Like.post_id, func.count(Like.id))
            .filter(Like.post_id.in_(post_ids))
            .group_by(Like.post_id)
            .all()
        )
        comment_counts = dict(
            db.query(Comment.post_id, func.count(Comment.id))
            .filter(Comment.post_id.in_(post_ids))
            .group_by(Comment.post_id)
            .all()
        )

        # 批量查询 is_liked
        liked_post_ids = set(
            row[0] for row in db.query(Like.post_id)
            .filter(Like.user_id == current_user_id, Like.post_id.in_(post_ids))
            .all()
        )

        # 批量查询 topics
        from app.models.models import Topic, post_topics
        topic_rows = (
            db.query(post_topics.c.post_id, Topic)
            .join(Topic, post_topics.c.topic_id == Topic.id)
            .filter(post_topics.c.post_id.in_(post_ids))
            .all()
        )
        topics_by_post: dict = {}
        for post_id, topic in topic_rows:
            topics_by_post.setdefault(post_id, []).append(topic.to_dict())

        return {
            pid: {
                'like_count': like_counts.get(pid, 0),
                'comment_count': comment_counts.get(pid, 0),
                'topics': topics_by_post.get(pid, []),
                'is_liked': pid in liked_post_ids,
            }
            for pid in post_ids
        }

    @staticmethod
    def _serialize_posts(posts: list, batch_data: dict) -> list:
        """使用批量数据序列化帖子列表"""
        result = []
        for post in posts:
            data = batch_data.get(post.id, {})
            result.append(post.to_dict(
                current_user_id=None,  # 不再让 to_dict 内部查询
                like_count=data.get('like_count'),
                comment_count=data.get('comment_count'),
                topics=data.get('topics'),
                is_liked=data.get('is_liked'),
            ))
        return result

    @staticmethod
    def _recent_seen_exists_expr(user_id: int, recent_seen_threshold: datetime):
        """SQL EXISTS expression for posts seen in the 7-day home-feed de-dupe window."""
        return exists().where(
            and_(
                PostFeedSeen.user_id == user_id,
                PostFeedSeen.source == RecommendationService.HOME_FEED_SEEN_SOURCE,
                PostFeedSeen.post_id == Post.id,
                PostFeedSeen.seen_at >= recent_seen_threshold,
            )
        )

    @staticmethod
    def _extract_post_ids(posts: list) -> list[int]:
        """Return unique non-null post IDs in display order."""
        post_ids = []
        seen_ids = set()
        for post in posts:
            post_id = getattr(post, 'id', None)
            if post_id is None or post_id in seen_ids:
                continue
            post_ids.append(post_id)
            seen_ids.add(post_id)
        return post_ids

    @staticmethod
    def _record_feed_seen(db: Session, user_id: int, posts: list, now: datetime | None = None) -> None:
        """Record home-feed exposure without failing the feed response if the write fails."""
        post_ids = RecommendationService._extract_post_ids(posts)
        if not post_ids:
            return

        now = now or datetime.utcnow()
        from app.database import SessionLocal
        seen_db = SessionLocal()
        try:
            rows = [
                {
                    'user_id': user_id,
                    'post_id': post_id,
                    'source': RecommendationService.HOME_FEED_SEEN_SOURCE,
                    'seen_at': now,
                    'created_at': now,
                    'updated_at': now,
                }
                for post_id in post_ids
            ]
            stmt = mysql_insert(PostFeedSeen.__table__).values(rows)
            stmt = stmt.on_duplicate_key_update(
                seen_at=stmt.inserted.seen_at,
                updated_at=stmt.inserted.updated_at,
            )
            seen_db.execute(stmt)
            seen_db.commit()
        except SQLAlchemyError as exc:
            seen_db.rollback()
            logger.warning('Failed to record home feed seen posts for user %s: %s', user_id, exc)
        finally:
            seen_db.close()

    @staticmethod
    def _get_interest_topic_ids(db: Session, user_id: int) -> list[int]:
        """Return topic IDs from posts the user authored, liked, or commented on."""
        from app.models.models import post_topics

        authored_topic_rows = (
            db.query(post_topics.c.topic_id)
            .join(Post, Post.id == post_topics.c.post_id)
            .filter(Post.user_id == user_id)
            .distinct()
            .all()
        )
        liked_topic_rows = (
            db.query(post_topics.c.topic_id)
            .join(Post, Post.id == post_topics.c.post_id)
            .join(Like, Like.post_id == Post.id)
            .filter(Like.user_id == user_id)
            .distinct()
            .all()
        )
        commented_topic_rows = (
            db.query(post_topics.c.topic_id)
            .join(Post, Post.id == post_topics.c.post_id)
            .join(Comment, Comment.post_id == Post.id)
            .filter(Comment.user_id == user_id)
            .distinct()
            .all()
        )

        topic_ids = []
        seen_topic_ids = set()
        for row in authored_topic_rows + liked_topic_rows + commented_topic_rows:
            topic_id = row[0]
            if topic_id not in seen_topic_ids:
                topic_ids.append(topic_id)
                seen_topic_ids.add(topic_id)
        return topic_ids

    @staticmethod
    def _apply_author_diversity(posts: list, max_posts_per_author: int):
        """Limit repeated authors while preserving ranking order."""
        diversified_posts = []
        author_count = {}
        diversity_applied = False

        for post in posts:
            author_id = post.user_id
            if author_count.get(author_id, 0) >= max_posts_per_author:
                diversity_applied = True
                continue

            diversified_posts.append(post)
            author_count[author_id] = author_count.get(author_id, 0) + 1

        return diversified_posts, diversity_applied

    @staticmethod
    def _select_diversified_page(posts: list, page: int, per_page: int, max_posts_per_author: int):
        """Apply author diversity first, then slice the requested public page."""
        diversified_posts, diversity_applied = RecommendationService._apply_author_diversity(
            posts,
            max_posts_per_author=max_posts_per_author,
        )
        start = (page - 1) * per_page
        end = start + per_page
        page_posts = diversified_posts[start:end]
        has_more = len(diversified_posts) > end
        return page_posts, has_more, diversity_applied

    @staticmethod
    def _parse_cursor_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _apply_cursor_seek(query, score_column, cursor_data: dict | None):
        """Apply stable seek pagination for feed_score DESC, created_at DESC, id DESC."""
        if not cursor_data:
            return query

        last_created_at = RecommendationService._parse_cursor_datetime(cursor_data.get('last_created_at'))
        if last_created_at is None:
            return query

        try:
            last_score = float(cursor_data.get('last_score'))
            last_post_id = int(cursor_data.get('last_post_id'))
        except (TypeError, ValueError):
            return query

        return query.filter(or_(
            score_column < last_score,
            and_(
                score_column == last_score,
                Post.created_at < last_created_at,
            ),
            and_(
                score_column == last_score,
                Post.created_at == last_created_at,
                Post.id < last_post_id,
            ),
        ))

    @staticmethod
    def _get_blocked_feed_author_ids(db: Session, user_id: int) -> set[int]:
        """Return DB-authoritative authors hidden by either block direction."""
        return excluded_user_ids(db, user_id)

    @staticmethod
    def _build_home_feed_query(
        db: Session,
        user_id: int,
        friend_ids: list[int],
        topic_ids: list[int],
        window_start: datetime,
        recent_seen_threshold: datetime,
        ranking_now: datetime,
        cursor_data: dict | None = None,
        exclude_post_ids: list[int] | None = None,
        blocked_ids: set[int] | None = None,
        window_end: datetime | None = None,
    ):
        from app.models.models import post_topics

        like_author_scores = (
            db.query(
                Post.user_id.label('author_id'),
                func.count(func.distinct(Like.id)).label('like_score'),
            )
            .join(Like, Like.post_id == Post.id)
            .filter(Like.user_id == user_id)
            .group_by(Post.user_id)
            .subquery()
        )

        comment_author_scores = (
            db.query(
                Post.user_id.label('author_id'),
                func.count(func.distinct(Comment.id)).label('comment_score'),
            )
            .join(Comment, Comment.post_id == Post.id)
            .filter(Comment.user_id == user_id)
            .group_by(Post.user_id)
            .subquery()
        )

        engagement_expr = (
            func.count(func.distinct(Like.id)) + func.count(func.distinct(Comment.id)) * 2
        ).label('engagement')
        age_hours_expr = (
            (func.unix_timestamp(ranking_now) - func.unix_timestamp(Post.created_at)) / 3600.0 + 1
        )
        freshness_score_expr = (40.0 / func.sqrt(age_hours_expr)).label('freshness_score')
        friend_score_expr = case((Post.user_id.in_(friend_ids), 12), else_=0)
        like_author_score_expr = func.max(func.coalesce(like_author_scores.c.like_score, 0))
        comment_author_score_expr = func.max(func.coalesce(comment_author_scores.c.comment_score, 0))
        interacted_author_score_expr = func.least(
            like_author_score_expr + comment_author_score_expr * 2,
            10,
        )
        topic_score_expr = func.max(case((post_topics.c.topic_id.in_(topic_ids), 8), else_=0))
        engagement_score_expr = func.least(func.coalesce(engagement_expr, 0), 8)
        feed_score_expr = (
            freshness_score_expr
            + friend_score_expr
            + interacted_author_score_expr
            + topic_score_expr
            + engagement_score_expr
        ).label('feed_score')

        candidate_filters = [
            post_visibility_predicate(user_id),
            Post.created_at >= window_start,
            or_(Post.community_only == False, Post.community_only == None),
            or_(Post.hidden_by_admin == False, Post.hidden_by_admin == None),
            ~RecommendationService._recent_seen_exists_expr(user_id, recent_seen_threshold),
        ]
        if blocked_ids:
            candidate_filters.append(~Post.user_id.in_(blocked_ids))
        if window_end is not None:
            candidate_filters.append(Post.created_at < window_end)
        if exclude_post_ids:
            candidate_filters.append(~Post.id.in_(exclude_post_ids))

        score_subquery = (
            db.query(
                Post.id.label('post_id'),
                feed_score_expr,
            )
            .outerjoin(Like, Post.id == Like.post_id)
            .outerjoin(Comment, Post.id == Comment.post_id)
            .outerjoin(like_author_scores, Post.user_id == like_author_scores.c.author_id)
            .outerjoin(comment_author_scores, Post.user_id == comment_author_scores.c.author_id)
            .outerjoin(post_topics, Post.id == post_topics.c.post_id)
            .filter(*candidate_filters)
            .group_by(Post.id, Post.user_id, Post.created_at)
            .subquery()
        )

        query = (
            db.query(Post, score_subquery.c.feed_score)
            .options(joinedload(Post.author))
            .join(score_subquery, Post.id == score_subquery.c.post_id)
        )
        query = RecommendationService._apply_cursor_seek(query, score_subquery.c.feed_score, cursor_data)
        return query.order_by(score_subquery.c.feed_score.desc(), Post.created_at.desc(), Post.id.desc())

    @staticmethod
    def _build_stage_start_cursor(window_stage: str, ranking_now: datetime) -> str:
        """Build a cursor that starts reading from the beginning of a later window stage."""
        return RecommendationService._encode_feed_cursor({
            'version': RecommendationService.HOME_FEED_CURSOR_VERSION,
            'last_score': 0,
            'last_created_at': ranking_now.isoformat(),
            'last_post_id': 0,
            'window_stage': window_stage,
            'stage_start': True,
            'issued_at': ranking_now.isoformat(),
        })

    @staticmethod
    def _select_cursor_feed_page(query, per_page: int, max_posts_per_author: int, window_stage: str, ranking_now: datetime):
        rows = []
        diversified_posts = []
        diversity_applied = False
        candidate_exhausted = True

        for fetch_multiplier in (10, 20, 30):
            fetch_limit = per_page * fetch_multiplier
            rows = query.limit(fetch_limit).all()
            posts = [row[0] for row in rows]
            diversified_posts, diversity_applied = RecommendationService._apply_author_diversity(
                posts,
                max_posts_per_author=max_posts_per_author,
            )
            candidate_exhausted = len(rows) < fetch_limit
            if len(diversified_posts) >= per_page or candidate_exhausted:
                break

        score_by_post_id = {row[0].id: float(row[1] or 0) for row in rows}
        page_posts = diversified_posts[:per_page]
        has_more = len(diversified_posts) > per_page or not candidate_exhausted

        next_cursor = None
        if page_posts and has_more:
            last_post = page_posts[-1]
            next_cursor = RecommendationService._encode_feed_cursor({
                'version': RecommendationService.HOME_FEED_CURSOR_VERSION,
                'last_score': score_by_post_id.get(last_post.id, 0.0),
                'last_created_at': last_post.created_at.isoformat(),
                'last_post_id': last_post.id,
                'window_stage': window_stage,
                'issued_at': ranking_now.isoformat(),
            })

        return page_posts, has_more, diversity_applied, next_cursor

    @staticmethod
    def get_personalized_feed(db: Session, user_id: int, page: int = 1, per_page: int = 20, cursor: str | None = None):
        page = max(1, min(page, RecommendationService.MAX_HOME_FEED_PAGE))
        per_page = max(1, min(per_page, RecommendationService.MAX_HOME_FEED_PER_PAGE))
        decoded_cursor = RecommendationService._decode_feed_cursor(cursor)

        friendships = db.query(Friendship).filter(
            ((Friendship.sender_id == user_id) | (Friendship.receiver_id == user_id)) &
            (Friendship.status == 'accepted')
        ).all()
        friend_ids = [f.sender_id if f.receiver_id == user_id else f.receiver_id for f in friendships]
        is_new_user = len(friend_ids) == 0
        topic_ids = RecommendationService._get_interest_topic_ids(db, user_id)
        blocked_ids = RecommendationService._get_blocked_feed_author_ids(db, user_id)

        now = datetime.utcnow()
        cursor_issued_at = RecommendationService._parse_cursor_datetime(decoded_cursor.get('issued_at')) if decoded_cursor else None
        ranking_now = cursor_issued_at or now
        recent_seen_threshold = now - timedelta(days=RecommendationService.HOME_FEED_SEEN_TTL_DAYS)
        max_posts_per_author = max(3, per_page // 5)

        start_stage = decoded_cursor.get('window_stage') if decoded_cursor else '30d'
        stages = ['30d', '90d'] if start_stage == '30d' else ['90d']

        diversified_posts = []
        has_more = False
        diversity_applied = False
        next_cursor = None
        feed_status = 'active'

        for stage_index, window_stage in enumerate(stages):
            window_stage = '30d' if window_stage == '30d' else '90d'
            remaining_count = per_page - len(diversified_posts)
            if remaining_count <= 0:
                break

            window_days = (
                RecommendationService.HOME_FEED_PRIMARY_WINDOW_DAYS
                if window_stage == '30d'
                else RecommendationService.HOME_FEED_FALLBACK_WINDOW_DAYS
            )
            window_start = ranking_now - timedelta(days=window_days)
            window_end = ranking_now - timedelta(days=RecommendationService.HOME_FEED_PRIMARY_WINDOW_DAYS) if window_stage == '90d' else None
            stage_cursor = decoded_cursor if decoded_cursor and decoded_cursor.get('window_stage') == window_stage and not decoded_cursor.get('stage_start') else None
            exclude_post_ids = RecommendationService._extract_post_ids(diversified_posts)
            posts_query = RecommendationService._build_home_feed_query(
                db=db,
                user_id=user_id,
                friend_ids=friend_ids,
                topic_ids=topic_ids,
                window_start=window_start,
                recent_seen_threshold=recent_seen_threshold,
                ranking_now=ranking_now,
                cursor_data=stage_cursor,
                exclude_post_ids=exclude_post_ids,
                blocked_ids=blocked_ids,
                window_end=window_end,
            )
            stage_posts, stage_has_more, stage_diversity_applied, stage_next_cursor = RecommendationService._select_cursor_feed_page(
                posts_query,
                per_page=remaining_count,
                max_posts_per_author=max_posts_per_author,
                window_stage=window_stage,
                ranking_now=ranking_now,
            )
            diversified_posts.extend(stage_posts)
            has_more = stage_has_more
            diversity_applied = diversity_applied or stage_diversity_applied
            next_cursor = stage_next_cursor
            has_next_stage = stage_index < len(stages) - 1
            if len(diversified_posts) >= per_page:
                if not stage_has_more and has_next_stage:
                    has_more = True
                    next_cursor = RecommendationService._build_stage_start_cursor('90d', ranking_now)
                break
            if stage_has_more:
                break

        if not diversified_posts:
            feed_status = 'exhausted_recent'

        # 批量加载聚合数据，消除 N+1
        batch_data = RecommendationService._batch_load_post_data(db, diversified_posts, user_id)
        serialized_posts = RecommendationService._serialize_posts(diversified_posts, batch_data)
        RecommendationService._record_feed_seen(db, user_id, diversified_posts, now=now)

        return {
            'posts': serialized_posts,
            'has_more': has_more,
            'current_page': page,
            'per_page': per_page,
            'algorithm': 'small_circle_fresh_cursor_seen_v1',
            'is_new_user': is_new_user,
            'diversity_applied': diversity_applied,
            'next_cursor': next_cursor,
            'feed_status': feed_status,
        }

    @staticmethod
    def get_trending_posts(
        db: Session,
        limit: int = 10,
        hours: int = 24,
        current_user_id: int | None = None,
    ):
        time_threshold = datetime.utcnow() - timedelta(hours=hours)
        now = datetime.utcnow()

        trending_query = db.query(
            Post,
            (func.count(func.distinct(Like.id)) + func.count(func.distinct(Comment.id)) * 2).label('engagement_score'),
            (1.0 / func.pow(
                (func.unix_timestamp(now) - func.unix_timestamp(Post.created_at)) / 3600.0 + 1, 0.7
            )).label('time_decay')
        ).outerjoin(Like, Post.id == Like.post_id).outerjoin(Comment, Post.id == Comment.post_id).filter(
            post_visibility_predicate(current_user_id),
            Post.created_at >= time_threshold
        ).group_by(Post.id).order_by(
            ((func.count(func.distinct(Like.id)) + func.count(func.distinct(Comment.id)) * 2) *
             (1.0 / func.pow(
                 (func.unix_timestamp(now) - func.unix_timestamp(Post.created_at)) / 3600.0 + 1, 0.7
             ))).desc(),
            Post.created_at.desc()
        ).limit(limit * 2)

        posts_with_scores = trending_query.all()

        diversified_posts = []
        author_count = {}
        max_posts_per_author = max(2, limit // 3)
        post_objects = []
        for post, engagement, decay in posts_with_scores:
            author_id = post.user_id
            if author_count.get(author_id, 0) < max_posts_per_author:
                post_objects.append(post)
                author_count[author_id] = author_count.get(author_id, 0) + 1
            if len(post_objects) >= limit:
                break

        # 批量加载聚合数据
        batch_data = RecommendationService._batch_load_post_data(db, post_objects, current_user_id)

        # 重新构建，保持 engagement/decay 信息
        idx = 0
        for post, engagement, decay in posts_with_scores:
            if idx >= len(post_objects):
                break
            data = batch_data.get(post.id, {})
            post_dict = post.to_dict(
                current_user_id=current_user_id,
                like_count=data.get('like_count'),
                comment_count=data.get('comment_count'),
                topics=data.get('topics'),
                is_liked=data.get('is_liked'),
            )
            post_dict['engagement_score'] = round(engagement, 2)
            post_dict['time_decay_factor'] = round(decay, 4)
            diversified_posts.append(post_dict)
            idx += 1

        return {
            'posts': diversified_posts,
            'time_window_hours': hours,
            'algorithm': 'trending_v2',
            'diversity_applied': len(diversified_posts) < len(posts_with_scores)
        }

    @staticmethod
    def get_suggested_users(db: Session, current_user_id: int, limit: int = 10):
        from app.models.models import Topic, post_topics, topic_followers

        friendships = db.query(Friendship).filter(
            ((Friendship.sender_id == current_user_id) | (Friendship.receiver_id == current_user_id)) &
            (Friendship.status == 'accepted')
        ).all()
        friend_ids = [f.sender_id if f.receiver_id == current_user_id else f.receiver_id for f in friendships]

        friends_of_friends = db.query(
            Friendship.sender_id.label('user_id'),
            func.count(Friendship.id).label('mutual_friends')
        ).filter(
            Friendship.receiver_id.in_(friend_ids),
            Friendship.status == 'accepted',
            Friendship.sender_id != current_user_id,
            ~Friendship.sender_id.in_(friend_ids + [current_user_id])
        ).group_by(Friendship.sender_id).subquery()

        user_topics = db.query(post_topics.c.topic_id).join(
            Post, Post.id == post_topics.c.post_id
        ).filter(Post.user_id == current_user_id).distinct().subquery()

        common_topic_users = db.query(
            Post.user_id,
            func.count(post_topics.c.topic_id).label('common_topics')
        ).join(post_topics, Post.id == post_topics.c.post_id).filter(
            post_topics.c.topic_id.in_(db.query(user_topics.c.topic_id)),
            Post.user_id != current_user_id,
            ~Post.user_id.in_(friend_ids + [current_user_id])
        ).group_by(Post.user_id).subquery()

        suggested_query = db.query(
            User,
            func.coalesce(friends_of_friends.c.mutual_friends, 0).label('mutual_friends'),
            func.coalesce(common_topic_users.c.common_topics, 0).label('common_topics')
        ).outerjoin(friends_of_friends, User.id == friends_of_friends.c.user_id).outerjoin(
            common_topic_users, User.id == common_topic_users.c.user_id
        ).filter(
            User.is_active == True,
            User.id != current_user_id,
            ~User.id.in_(friend_ids + [current_user_id]),
            visible_user_predicate(current_user_id, User.id),
            or_(friends_of_friends.c.user_id.isnot(None), common_topic_users.c.user_id.isnot(None))
        ).order_by(
            (func.coalesce(friends_of_friends.c.mutual_friends, 0) * 2 +
             func.coalesce(common_topic_users.c.common_topics, 0)).desc(),
            User.created_at.desc()
        ).limit(limit)

        suggestions = []
        for user, mutual_friends, common_topics in suggested_query.all():
            suggestion = user.to_dict()
            suggestion['mutual_friends_count'] = mutual_friends
            suggestion['common_topics_count'] = common_topics
            suggestion['recommendation_reason'] = []
            if mutual_friends > 0:
                suggestion['recommendation_reason'].append(f'{mutual_friends} 个共同好友')
            if common_topics > 0:
                suggestion['recommendation_reason'].append(f'{common_topics} 个共同话题')
            suggestions.append(suggestion)

        return {'suggestions': suggestions, 'algorithm': 'social_graph_v1'}

    @staticmethod
    def get_friend_recommendations(db: Session, current_user_id: int, limit: int = 10):
        from app.models.models import Topic, post_topics, topic_followers
        import random

        friendships = db.query(Friendship).filter(
            ((Friendship.sender_id == current_user_id) | (Friendship.receiver_id == current_user_id)) &
            (Friendship.status == 'accepted')
        ).all()
        blocked_ids = excluded_user_ids(db, current_user_id)
        friend_ids = [
            f.sender_id if f.receiver_id == current_user_id else f.receiver_id
            for f in friendships
            if (f.sender_id if f.receiver_id == current_user_id else f.receiver_id) not in blocked_ids
        ]

        pending_requests = db.query(Friendship).filter(
            ((Friendship.sender_id == current_user_id) | (Friendship.receiver_id == current_user_id)) &
            (Friendship.status == 'pending')
        ).all()
        pending_ids = [
            f.sender_id if f.receiver_id == current_user_id else f.receiver_id
            for f in pending_requests
            if (f.sender_id if f.receiver_id == current_user_id else f.receiver_id) not in blocked_ids
        ]

        exclude_ids = friend_ids + pending_ids + list(blocked_ids) + [current_user_id]

        candidates = {}

        if friend_ids:
            friends_of_friends = db.query(
                Friendship.sender_id.label('user_id'),
                func.count(Friendship.id).label('mutual_friends')
            ).filter(
                Friendship.receiver_id.in_(friend_ids),
                Friendship.status == 'accepted',
                ~Friendship.sender_id.in_(exclude_ids)
            ).group_by(Friendship.sender_id).all()

            for user_id, mutual_count in friends_of_friends:
                if user_id not in candidates:
                    candidates[user_id] = {'score': 0, 'mutual_friends': 0, 'common_topics': 0, 'reasons': []}
                candidates[user_id]['mutual_friends'] = mutual_count
                candidates[user_id]['score'] += mutual_count * 15
                candidates[user_id]['reasons'].append(f'{mutual_count} 个共同好友')

        if friend_ids:
            friend_topics = db.query(
                post_topics.c.topic_id,
                func.count(post_topics.c.post_id).label('topic_popularity')
            ).join(Post, Post.id == post_topics.c.post_id).filter(
                Post.user_id.in_(friend_ids)
            ).group_by(post_topics.c.topic_id).subquery()

            topic_based_users = db.query(
                Post.user_id,
                func.count(func.distinct(post_topics.c.topic_id)).label('common_topics'),
                func.sum(friend_topics.c.topic_popularity).label('topic_score')
            ).join(post_topics, Post.id == post_topics.c.post_id).join(
                friend_topics, post_topics.c.topic_id == friend_topics.c.topic_id
            ).filter(~Post.user_id.in_(exclude_ids)).group_by(Post.user_id).all()

            for user_id, common_count, topic_score in topic_based_users:
                if user_id not in candidates:
                    candidates[user_id] = {'score': 0, 'mutual_friends': 0, 'common_topics': 0, 'reasons': []}
                candidates[user_id]['common_topics'] = common_count
                candidates[user_id]['score'] += common_count * 8 + min(topic_score or 0, 30)
                candidates[user_id]['reasons'].append(f'{common_count} 个共同话题')

        week_ago = datetime.utcnow() - timedelta(days=7)
        active_users = db.query(
            Post.user_id, func.count(Post.id).label('post_count')
        ).filter(Post.created_at >= week_ago, ~Post.user_id.in_(exclude_ids)).group_by(Post.user_id).all()

        for user_id, post_count in active_users:
            if user_id in candidates:
                activity_bonus = min(post_count * 3, 20)
                candidates[user_id]['score'] += activity_bonus
                if post_count >= 5:
                    candidates[user_id]['reasons'].append('活跃用户')

        current_user = db.query(User).filter(User.id == current_user_id).first()
        if current_user:
            current_month = current_user.created_at.month
            current_year = current_user.created_at.year

            similar_registration = db.query(User).filter(
                ~User.id.in_(exclude_ids),
                or_(
                    and_(func.month(User.created_at) == current_month, func.year(User.created_at) == current_year),
                    and_(
                        func.month(User.created_at) == (current_month - 1 if current_month > 1 else 12),
                        func.year(User.created_at) == (current_year if current_month > 1 else current_year - 1)
                    )
                )
            ).all()

            for user in similar_registration:
                if user.id not in candidates:
                    candidates[user.id] = {'score': 0, 'mutual_friends': 0, 'common_topics': 0, 'reasons': []}
                candidates[user.id]['score'] += 5
                candidates[user.id]['reasons'].append('注册时间相近')

        if len(candidates) < limit:
            exploration_count = limit - len(candidates)
            random_active_users = db.query(User).join(Post, User.id == Post.user_id).filter(
                Post.created_at >= week_ago,
                ~User.id.in_(exclude_ids),
                ~User.id.in_(candidates.keys())
            ).distinct().order_by(func.rand()).limit(exploration_count).all()

            for user in random_active_users:
                candidates[user.id] = {'score': 10, 'mutual_friends': 0, 'common_topics': 0, 'reasons': ['探索推荐']}

        recommended_users = []
        for user_id, data in candidates.items():
            if data['score'] > 0:
                user = db.query(User).filter(User.id == user_id).first()
                if user and user.is_active:
                    user_dict = user.to_dict()
                    user_dict['recommendation_score'] = data['score']
                    user_dict['mutual_friends_count'] = data['mutual_friends']
                    user_dict['common_topics_count'] = data['common_topics']
                    user_dict['recommendation_reasons'] = list(set(data['reasons']))
                    match_percentage = int(100 * (1 / (1 + pow(2.718, -data['score'] / 30))))
                    user_dict['match_percentage'] = min(match_percentage, 99)
                    recommended_users.append(user_dict)

        recommended_users.sort(key=lambda x: x['recommendation_score'], reverse=True)

        return {
            'recommendations': recommended_users[:limit],
            'total_candidates': len(candidates),
            'algorithm': 'smart_friend_recommendation_v3',
            'factors': [
                'mutual_friends (weight: 15)',
                'common_topics (weight: 8 + popularity bonus)',
                'activity_level (weight: 3, max 20)',
                'registration_time (bonus: 5)',
                'exploration (bonus: 10)'
            ]
        }

    @staticmethod
    def get_related_posts(
        db: Session,
        post_id: int,
        limit: int = 5,
        current_user_id: int | None = None,
    ):
        from app.models.models import Topic, post_topics

        post = db.query(Post).filter(
            Post.id == post_id,
            post_visibility_predicate(current_user_id),
        ).first()
        if not post:
            return {'posts': [], 'algorithm': 'related_v1'}

        post_topic_ids = db.query(post_topics.c.topic_id).filter(post_topics.c.post_id == post_id).all()
        topic_ids = [t[0] for t in post_topic_ids]

        related_query = db.query(Post).outerjoin(
            post_topics, Post.id == post_topics.c.post_id
        ).filter(
            Post.id != post_id,
            post_visibility_predicate(current_user_id),
            or_(Post.user_id == post.user_id, post_topics.c.topic_id.in_(topic_ids) if topic_ids else False)
        ).order_by(
            case((post_topics.c.topic_id.in_(topic_ids), 100), else_=0).desc(),
            Post.created_at.desc()
        ).limit(limit)

        posts = related_query.all()
        batch_data = RecommendationService._batch_load_post_data(db, posts, current_user_id)
        return {'posts': RecommendationService._serialize_posts(posts, batch_data), 'algorithm': 'related_v2'}
