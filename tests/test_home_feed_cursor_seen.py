import unittest
from datetime import datetime, timezone


class HomeFeedSeenModelSourceTest(unittest.TestCase):
    def _models_source(self):
        with open('app/models/models.py', 'r', encoding='utf-8') as f:
            return f.read()

    def _migration_source(self):
        with open('alembic/versions/2026_06_20_1200-7f3c2a9b8d10_add_post_feed_seen.py', 'r', encoding='utf-8') as f:
            return f.read()

    def test_post_feed_seen_model_declares_required_indexes(self):
        source = self._models_source()
        self.assertIn('class PostFeedSeen(Base):', source)
        self.assertIn("__tablename__ = 'post_feed_seen'", source)
        self.assertIn("UniqueConstraint('user_id', 'post_id', 'source', name='uq_post_feed_seen_user_post_source')", source)
        self.assertIn("Index('idx_post_feed_seen_user_source_seen_post', 'user_id', 'source', 'seen_at', 'post_id')", source)
        self.assertIn("Index('idx_post_feed_seen_post_source', 'post_id', 'source')", source)
        self.assertIn("Index('idx_post_feed_seen_seen_at', 'seen_at')", source)

    def test_post_feed_seen_migration_creates_table_and_indexes(self):
        source = self._migration_source()
        self.assertIn("revision: str = '7f3c2a9b8d10'", source)
        self.assertIn("down_revision: Union[str, Sequence[str], None] = '9e5c87b1dde7'", source)
        self.assertIn("op.create_table('post_feed_seen'", source)
        self.assertIn("op.create_index('idx_post_feed_seen_user_source_seen_post'", source)
        self.assertIn("op.create_index('idx_post_feed_seen_post_source'", source)
        self.assertIn("op.create_index('idx_post_feed_seen_seen_at'", source)
        self.assertIn("op.drop_index('idx_post_feed_seen_seen_at'", source)
        self.assertIn("op.drop_table('post_feed_seen')", source)


class HomeFeedCursorHelperTest(unittest.TestCase):
    def test_cursor_round_trip_preserves_stable_sort_fields(self):
        from app.services.recommendation_service import RecommendationService

        cursor = RecommendationService._encode_feed_cursor({
            'version': 1,
            'last_score': 12.5,
            'last_created_at': '2026-06-20T12:00:00',
            'last_post_id': 123,
            'window_stage': '30d',
            'issued_at': '2026-06-20T12:00:00',
        })
        decoded = RecommendationService._decode_feed_cursor(cursor)

        self.assertEqual(decoded['version'], 1)
        self.assertEqual(decoded['last_score'], 12.5)
        self.assertEqual(decoded['last_created_at'], '2026-06-20T12:00:00')
        self.assertEqual(decoded['last_post_id'], 123)
        self.assertEqual(decoded['window_stage'], '30d')

    def test_invalid_cursor_decodes_to_none(self):
        from app.services.recommendation_service import RecommendationService

        self.assertIsNone(RecommendationService._decode_feed_cursor('not-a-valid-cursor'))
        self.assertIsNone(RecommendationService._decode_feed_cursor('abc'))
        self.assertIsNone(RecommendationService._decode_feed_cursor(''))
        self.assertIsNone(RecommendationService._decode_feed_cursor(None))

    def test_cursor_seek_values_are_parsed(self):
        from app.services.recommendation_service import RecommendationService

        cursor = RecommendationService._encode_feed_cursor({
            'version': 1,
            'last_score': 9.25,
            'last_created_at': '2026-06-20T12:00:00',
            'last_post_id': 42,
            'window_stage': '90d',
            'issued_at': '2026-06-20T12:00:00',
        })
        decoded = RecommendationService._decode_feed_cursor(cursor)
        self.assertEqual(decoded['window_stage'], '90d')
        self.assertEqual(decoded['last_post_id'], 42)


class HomeFeedSeenRecordHelperTest(unittest.TestCase):
    class PostStub:
        def __init__(self, post_id):
            self.id = post_id

    def test_extract_post_ids_for_seen_recording(self):
        from app.services.recommendation_service import RecommendationService

        posts = [self.PostStub(1), self.PostStub(2), self.PostStub(2), self.PostStub(None)]
        self.assertEqual(RecommendationService._extract_post_ids(posts), [1, 2])


class HomeFeedRouteAndPerformanceSourceTest(unittest.TestCase):
    def _route_source(self):
        with open('app/routers/recommendations.py', 'r', encoding='utf-8') as f:
            return f.read()

    def _service_source(self):
        with open('app/services/recommendation_service.py', 'r', encoding='utf-8') as f:
            return f.read()

    def test_route_accepts_cursor_and_clamps_page(self):
        source = self._route_source()
        self.assertIn('cursor: str | None = Query(None)', source)
        self.assertIn('page = min(page, 50)', source)
        self.assertIn('cursor=cursor', source)

    def test_cursor_selection_has_bounded_candidate_limit(self):
        source = self._service_source()
        helper_source = source.split('def _select_cursor_feed_page')[1].split('def get_personalized_feed')[0]
        self.assertIn('fetch_limit = per_page * fetch_multiplier', helper_source)
        self.assertIn('query.limit(fetch_limit).all()', helper_source)
        self.assertIn('for fetch_multiplier in (10, 20, 30):', helper_source)
        self.assertNotIn('while True', helper_source)
        self.assertNotIn('.offset(', helper_source)

    def test_fallback_window_excludes_posts_already_selected_from_primary_window(self):
        source = self._service_source()
        self.assertIn('exclude_post_ids: list[int] | None = None', source)
        self.assertIn('if exclude_post_ids:', source)
        self.assertIn('candidate_filters.append(~Post.id.in_(exclude_post_ids))', source)

    def test_seen_write_occurs_after_serialization(self):
        source = self._service_source()
        feed_source = source.split('def get_personalized_feed')[1].split('def get_trending_posts')[0]
        serialize_index = feed_source.index('serialized_posts = RecommendationService._serialize_posts')
        seen_index = feed_source.index('RecommendationService._record_feed_seen')
        self.assertLess(serialize_index, seen_index)

    def test_seen_write_uses_independent_session_and_commit_guard(self):
        source = self._service_source()
        helper_source = source.split('def _record_feed_seen')[1].split('def _get_interest_topic_ids')[0]
        self.assertIn('from app.database import SessionLocal', helper_source)
        self.assertIn('seen_db = SessionLocal()', helper_source)
        self.assertIn('seen_db.commit()', helper_source)
        self.assertIn('seen_db.rollback()', helper_source)
        self.assertIn('seen_db.close()', helper_source)

    def test_has_more_tracks_early_stop_with_full_candidate_batch(self):
        source = self._service_source()
        helper_source = source.split('def _select_cursor_feed_page')[1].split('def get_personalized_feed')[0]
        self.assertIn('candidate_exhausted = len(rows) < fetch_limit', helper_source)
        self.assertIn('has_more = len(diversified_posts) > per_page or not candidate_exhausted', helper_source)

    def test_full_primary_window_can_continue_to_fallback_window(self):
        source = self._service_source()
        feed_source = source.split('def get_personalized_feed')[1].split('def get_trending_posts')[0]
        self.assertIn('for stage_index, window_stage in enumerate(stages):', feed_source)
        self.assertIn('has_next_stage = stage_index < len(stages) - 1', feed_source)
        self.assertIn("next_cursor = RecommendationService._build_stage_start_cursor('90d', ranking_now)", feed_source)
        self.assertIn("decoded_cursor.get('stage_start')", feed_source)


if __name__ == '__main__':
    unittest.main()
