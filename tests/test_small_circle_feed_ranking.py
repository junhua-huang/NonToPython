import unittest


class SmallCircleFeedRankingSourceTest(unittest.TestCase):
    def _source(self):
        with open('app/services/recommendation_service.py', 'r', encoding='utf-8') as f:
            return f.read()

    def _feed_source(self):
        source = self._source()
        return source.split('def get_personalized_feed')[1].split('def get_trending_posts')[0]

    def test_feed_uses_small_circle_algorithm_marker(self):
        feed_source = self._feed_source()
        self.assertIn("'algorithm': 'small_circle_fresh_cursor_seen_v1'", feed_source)
        self.assertNotIn("'algorithm': 'personalized_v3'", feed_source)

    def test_friends_are_light_score_not_first_sort_layer(self):
        source = self._source()
        self.assertIn('friend_score_expr', source)
        self.assertIn('feed_score_expr', source)
        self.assertIn('+ friend_score_expr', source)
        self.assertNotIn('case((Post.user_id.in_(friend_ids), 80), else_=0).desc()', source)

    def test_interest_topics_include_authored_liked_and_commented_posts(self):
        source = self._source()
        self.assertIn('def _get_interest_topic_ids', source)
        helper_source = source.split('def _get_interest_topic_ids')[1].split('def _apply_author_diversity')[0]
        self.assertIn('authored_topic_rows', helper_source)
        self.assertIn('liked_topic_rows', helper_source)
        self.assertIn('commented_topic_rows', helper_source)
        self.assertIn('Post.user_id == user_id', helper_source)
        self.assertIn('Like.user_id == user_id', helper_source)
        self.assertIn('Comment.user_id == user_id', helper_source)
        self.assertNotIn('.outerjoin(Like', helper_source)
        self.assertNotIn('.outerjoin(Comment', helper_source)

    def test_candidate_pool_is_bounded_before_author_diversity(self):
        source = self._source()
        self.assertIn('def _select_cursor_feed_page', source)
        helper_source = source.split('def _select_cursor_feed_page')[1].split('def get_personalized_feed')[0]
        self.assertIn('query.limit(fetch_limit).all()', helper_source)
        self.assertIn('RecommendationService._apply_author_diversity(', helper_source)
        self.assertIn('for fetch_multiplier in (10, 20, 30):', helper_source)
        self.assertNotIn('while True', helper_source)

    def test_feed_eager_loads_authors_to_avoid_author_n_plus_one(self):
        source = self._source()
        query_source = source.split('def _build_home_feed_query')[1].split('def _select_cursor_feed_page')[0]
        self.assertIn('from sqlalchemy.orm import Session, joinedload', source)
        self.assertIn('.options(joinedload(Post.author))', query_source)

    def test_engagement_score_is_lightweight_and_capped(self):
        source = self._source()
        self.assertIn('engagement_score_expr', source)
        self.assertIn('func.least(func.coalesce(engagement_expr, 0), 8)', source)

    def test_feed_aggregates_scores_before_joining_full_posts(self):
        source = self._source()
        query_source = source.split('def _build_home_feed_query')[1].split('def _select_cursor_feed_page')[0]
        self.assertIn('score_subquery = (', query_source)
        self.assertIn('Post.id.label(\'post_id\')', query_source)
        self.assertIn('.group_by(Post.id, Post.user_id, Post.created_at)', query_source)
        self.assertIn('func.max(func.coalesce(like_author_scores.c.like_score, 0))', query_source)
        self.assertIn('func.max(func.coalesce(comment_author_scores.c.comment_score, 0))', query_source)
        self.assertIn('db.query(Post, score_subquery.c.feed_score)', query_source)
        self.assertNotIn('db.query(Post).outerjoin(', query_source)

    def test_feed_defends_service_layer_pagination_and_uses_stable_tiebreaker(self):
        source = self._source()
        feed_source = self._feed_source()
        self.assertIn('page = max(1, min(page, RecommendationService.MAX_HOME_FEED_PAGE))', feed_source)
        self.assertIn('per_page = max(1, min(per_page, RecommendationService.MAX_HOME_FEED_PER_PAGE))', feed_source)
        self.assertIn('Post.id.desc()', source)

    def test_feed_clamps_page_and_exposes_cursor_fields(self):
        feed_source = self._feed_source()
        self.assertIn('MAX_HOME_FEED_PAGE = 50', self._source())
        self.assertIn('page = max(1, min(page, RecommendationService.MAX_HOME_FEED_PAGE))', feed_source)
        self.assertIn('cursor: str | None = None', feed_source)
        self.assertIn("'next_cursor':", feed_source)
        self.assertIn("'feed_status':", feed_source)

    def test_feed_filters_recent_seen_posts_and_records_seen_non_blocking(self):
        source = self._source()
        feed_source = self._feed_source()
        self.assertIn('HOME_FEED_SEEN_TTL_DAYS = 7', source)
        self.assertIn('def _recent_seen_exists_expr', source)
        self.assertIn('PostFeedSeen.seen_at >= recent_seen_threshold', source)
        self.assertIn('~RecommendationService._recent_seen_exists_expr(', source)
        self.assertIn('def _record_feed_seen', source)
        self.assertIn('mysql_insert(PostFeedSeen.__table__)', source)
        self.assertIn('on_duplicate_key_update', source)
        self.assertNotIn('existing_rows = (', source)
        self.assertIn('except SQLAlchemyError', source)
        self.assertIn('logger.warning', source)
        self.assertIn('RecommendationService._record_feed_seen(', feed_source)

    def test_feed_limits_candidates_to_30_and_90_day_windows(self):
        source = self._source()
        feed_source = self._feed_source()
        self.assertIn('HOME_FEED_PRIMARY_WINDOW_DAYS = 30', source)
        self.assertIn('HOME_FEED_FALLBACK_WINDOW_DAYS = 90', source)
        self.assertIn("start_stage = decoded_cursor.get('window_stage') if decoded_cursor else '30d'", feed_source)
        self.assertIn("stages = ['30d', '90d'] if start_stage == '30d' else ['90d']", feed_source)
        self.assertIn('remaining_count = per_page - len(diversified_posts)', feed_source)
        self.assertIn('exclude_post_ids = RecommendationService._extract_post_ids(diversified_posts)', feed_source)
        self.assertIn('window_end=window_end', feed_source)
        self.assertIn('if len(diversified_posts) >= per_page:', feed_source)
        self.assertIn('if stage_has_more:', feed_source)
        self.assertIn("feed_status = 'exhausted_recent'", feed_source)

    def test_feed_applies_visibility_and_block_filters(self):
        source = self._source()
        query_source = source.split('def _build_home_feed_query')[1].split('def _select_cursor_feed_page')[0]
        feed_source = self._feed_source()
        self.assertIn('or_(Post.community_only == False, Post.community_only == None)', query_source)
        self.assertIn('or_(Post.hidden_by_admin == False, Post.hidden_by_admin == None)', query_source)
        self.assertIn('if blocked_ids:', query_source)
        self.assertIn('candidate_filters.append(~Post.user_id.in_(blocked_ids))', query_source)
        self.assertIn('def _get_blocked_feed_author_ids', source)
        self.assertIn('return excluded_user_ids(db, user_id)', source)
        self.assertIn('blocked_ids = RecommendationService._get_blocked_feed_author_ids(db, user_id)', feed_source)
        self.assertIn('blocked_ids=blocked_ids', feed_source)

    def test_fallback_90_day_window_excludes_primary_30_day_range(self):
        source = self._source()
        query_source = source.split('def _build_home_feed_query')[1].split('def _select_cursor_feed_page')[0]
        feed_source = self._feed_source()
        self.assertIn('window_end: datetime | None = None', query_source)
        self.assertIn('if window_end is not None:', query_source)
        self.assertIn('candidate_filters.append(Post.created_at < window_end)', query_source)
        self.assertIn('window_end = ranking_now - timedelta(days=RecommendationService.HOME_FEED_PRIMARY_WINDOW_DAYS) if window_stage == \'90d\' else None', feed_source)

    def test_cursor_mode_uses_seek_not_offset(self):
        source = self._source()
        self.assertIn('def _apply_cursor_seek', source)
        cursor_helper_source = source.split('def _apply_cursor_seek')[1].split('def get_personalized_feed')[0]
        self.assertIn('score_column < last_score', cursor_helper_source)
        self.assertIn('Post.created_at < last_created_at', cursor_helper_source)
        self.assertIn('Post.id < last_post_id', cursor_helper_source)
        self.assertNotIn('.offset(', cursor_helper_source)


class AuthorDiversityHelperTest(unittest.TestCase):
    class PostStub:
        def __init__(self, post_id, user_id):
            self.id = post_id
            self.user_id = user_id

    def test_author_diversity_filters_spam_and_backfills_page(self):
        from app.services.recommendation_service import RecommendationService

        posts = [
            self.PostStub(1, 1),
            self.PostStub(2, 1),
            self.PostStub(3, 1),
            self.PostStub(4, 1),
            self.PostStub(5, 2),
            self.PostStub(6, 3),
        ]

        diversified, diversity_applied = RecommendationService._apply_author_diversity(
            posts,
            max_posts_per_author=2,
        )

        self.assertTrue(diversity_applied)
        self.assertEqual([p.id for p in diversified], [1, 2, 5, 6])
        self.assertLessEqual(sum(1 for p in diversified if p.user_id == 1), 2)

    def test_select_diversified_page_does_not_skip_unreturned_candidates(self):
        from app.services.recommendation_service import RecommendationService

        posts = [self.PostStub(post_id, post_id) for post_id in range(1, 26)]

        first_page, first_has_more, _ = RecommendationService._select_diversified_page(
            posts,
            page=1,
            per_page=20,
            max_posts_per_author=3,
        )
        second_page, second_has_more, _ = RecommendationService._select_diversified_page(
            posts,
            page=2,
            per_page=20,
            max_posts_per_author=3,
        )

        self.assertEqual([p.id for p in first_page], list(range(1, 21)))
        self.assertTrue(first_has_more)
        self.assertEqual([p.id for p in second_page], list(range(21, 26)))
        self.assertFalse(second_has_more)

    def test_select_diversified_page_backfills_after_skewed_author_prefix(self):
        from app.services.recommendation_service import RecommendationService

        skewed_posts = [self.PostStub(post_id, 1) for post_id in range(1, 101)]
        backfill_posts = [self.PostStub(post_id, post_id) for post_id in range(101, 125)]
        posts = skewed_posts + backfill_posts

        first_page, first_has_more, _ = RecommendationService._select_diversified_page(
            posts,
            page=1,
            per_page=20,
            max_posts_per_author=4,
        )
        second_page, second_has_more, _ = RecommendationService._select_diversified_page(
            posts,
            page=2,
            per_page=20,
            max_posts_per_author=4,
        )

        self.assertEqual([p.id for p in first_page], [1, 2, 3, 4] + list(range(101, 117)))
        self.assertTrue(first_has_more)
        self.assertEqual([p.id for p in second_page], list(range(117, 125)))
        self.assertFalse(second_has_more)


if __name__ == '__main__':
    unittest.main()
