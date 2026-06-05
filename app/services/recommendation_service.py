"""
推荐算法服务 - FastAPI 重构版（Session 参数传递模式）
"""
from app.models.models import Post, User, Like, Comment, Friendship
from sqlalchemy import func, case, or_, and_
from sqlalchemy.orm import Session
from datetime import datetime, timedelta


class RecommendationService:
    """推荐算法服务"""

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
    def get_personalized_feed(db: Session, user_id: int, page: int = 1, per_page: int = 20):
        from app.models.models import Topic, post_topics

        friendships = db.query(Friendship).filter(
            ((Friendship.sender_id == user_id) | (Friendship.receiver_id == user_id)) &
            (Friendship.status == 'accepted')
        ).all()
        friend_ids = [f.sender_id if f.receiver_id == user_id else f.receiver_id for f in friendships]
        is_new_user = len(friend_ids) == 0

        interaction_scores = db.query(
            Post.user_id.label('interacted_user_id'),
            func.count(Like.id).label('like_count'),
            func.count(Comment.id).label('comment_count')
        ).outerjoin(Like, Post.id == Like.post_id).outerjoin(Comment, Post.id == Comment.post_id).filter(
            or_(Like.user_id == user_id, Comment.user_id == user_id)
        ).group_by(Post.user_id).subquery()

        user_interaction_scores = db.query(
            interaction_scores.c.interacted_user_id,
            (interaction_scores.c.like_count + interaction_scores.c.comment_count * 2).label('score')
        ).subquery()

        user_topics = db.query(post_topics.c.topic_id).join(Post, Post.id == post_topics.c.post_id).filter(
            Post.user_id == user_id
        ).distinct().all()
        topic_ids = [t[0] for t in user_topics]

        now = datetime.utcnow()
        time_decay_threshold = now - timedelta(days=30 if is_new_user else 7)

        # 合并 engagement 子查询到主查询，减少子查询扫描
        engagement_expr = (
            func.count(func.distinct(Like.id)) + func.count(func.distinct(Comment.id)) * 2
        ).label('engagement')

        posts_query = db.query(Post).outerjoin(
            Like, Post.id == Like.post_id
        ).outerjoin(
            Comment, Post.id == Comment.post_id
        ).outerjoin(
            user_interaction_scores, Post.user_id == user_interaction_scores.c.interacted_user_id
        ).outerjoin(
            post_topics, Post.id == post_topics.c.post_id
        ).filter(
            Post.is_public == True,
            Post.created_at >= time_decay_threshold
        ).group_by(Post.id).order_by(
            case((Post.user_id.in_(friend_ids), 80), else_=0).desc(),
            func.least(func.coalesce(user_interaction_scores.c.score, 0), 30).desc(),
            func.max(case((post_topics.c.topic_id.in_(topic_ids), 40), else_=0)).desc(),
            func.least(func.coalesce(engagement_expr, 0), 20).desc(),
            (1.0 / func.sqrt(func.unix_timestamp(now) - func.unix_timestamp(Post.created_at) + 3600)).desc(),
            Post.created_at.desc()
        )

        offset = (page - 1) * per_page
        # 多取 1 条判断 has_more，替代 expensive COUNT
        posts = posts_query.offset(offset).limit(per_page + 1).all()
        has_more = len(posts) > per_page
        if has_more:
            posts = posts[:per_page]

        diversified_posts = []
        author_count = {}
        max_posts_per_author = max(3, per_page // 5)
        for post in posts:
            author_id = post.user_id
            if author_count.get(author_id, 0) < max_posts_per_author:
                diversified_posts.append(post)
                author_count[author_id] = author_count.get(author_id, 0) + 1
            if len(diversified_posts) >= per_page:
                break

        # 批量加载聚合数据，消除 N+1
        batch_data = RecommendationService._batch_load_post_data(db, diversified_posts, user_id)

        return {
            'posts': RecommendationService._serialize_posts(diversified_posts, batch_data),
            'has_more': has_more,
            'current_page': page,
            'per_page': per_page,
            'algorithm': 'personalized_v3',
            'is_new_user': is_new_user,
            'diversity_applied': len(diversified_posts) < len(posts)
        }

    @staticmethod
    def get_trending_posts(db: Session, limit: int = 10, hours: int = 24):
        time_threshold = datetime.utcnow() - timedelta(hours=hours)
        now = datetime.utcnow()

        trending_query = db.query(
            Post,
            (func.count(func.distinct(Like.id)) + func.count(func.distinct(Comment.id)) * 2).label('engagement_score'),
            (1.0 / func.pow(
                (func.unix_timestamp(now) - func.unix_timestamp(Post.created_at)) / 3600.0 + 1, 0.7
            )).label('time_decay')
        ).outerjoin(Like, Post.id == Like.post_id).outerjoin(Comment, Post.id == Comment.post_id).filter(
            Post.is_public == True,
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
        batch_data = RecommendationService._batch_load_post_data(db, post_objects, None)

        # 重新构建，保持 engagement/decay 信息
        idx = 0
        for post, engagement, decay in posts_with_scores:
            if idx >= len(post_objects):
                break
            data = batch_data.get(post.id, {})
            post_dict = post.to_dict(
                current_user_id=None,
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
        friend_ids = [f.sender_id if f.receiver_id == current_user_id else f.receiver_id for f in friendships]

        pending_requests = db.query(Friendship).filter(
            ((Friendship.sender_id == current_user_id) | (Friendship.receiver_id == current_user_id)) &
            (Friendship.status == 'pending')
        ).all()
        pending_ids = [f.sender_id if f.receiver_id == current_user_id else f.receiver_id for f in pending_requests]

        exclude_ids = friend_ids + pending_ids + [current_user_id]

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
    def get_related_posts(db: Session, post_id: int, limit: int = 5):
        from app.models.models import Topic, post_topics

        post = db.query(Post).filter(Post.id == post_id).first()
        if not post:
            return {'posts': [], 'algorithm': 'related_v1'}

        post_topic_ids = db.query(post_topics.c.topic_id).filter(post_topics.c.post_id == post_id).all()
        topic_ids = [t[0] for t in post_topic_ids]

        related_query = db.query(Post).outerjoin(
            post_topics, Post.id == post_topics.c.post_id
        ).filter(
            Post.id != post_id,
            Post.is_public == True,
            or_(Post.user_id == post.user_id, post_topics.c.topic_id.in_(topic_ids) if topic_ids else False)
        ).order_by(
            case((post_topics.c.topic_id.in_(topic_ids), 100), else_=0).desc(),
            Post.created_at.desc()
        ).limit(limit)

        posts = related_query.all()
        batch_data = RecommendationService._batch_load_post_data(db, posts, None)
        return {'posts': RecommendationService._serialize_posts(posts, batch_data), 'algorithm': 'related_v2'}