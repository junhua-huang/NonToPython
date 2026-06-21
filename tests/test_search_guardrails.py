import unittest


class SearchGuardrailsSourceTest(unittest.TestCase):
    def test_search_uses_short_query_guard_helper(self):
        with open('app/routers/search.py', 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('def _is_short_query(q: str) -> bool:', source)
        self.assertIn('return len((q or "").strip()) < 2', source)
        self.assertIn('if _is_short_query(q):', source)

    def test_global_search_clamps_per_page(self):
        with open('app/routers/search.py', 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('per_page = min(per_page, 10)', source)

    def test_comic_events_applies_page_offset_before_limit(self):
        with open('app/routers/search.py', 'r', encoding='utf-8') as f:
            source = f.read()
        comic_events_source = source.split('def _comic_events')[1].split('@router.get("/users")')[0]
        self.assertIn('offset = (page - 1) * per_page', comic_events_source)
        self.assertIn('.offset(offset)\n        .limit(per_page)', comic_events_source)

    def test_comic_events_follow_counts_use_expanding_bind(self):
        with open('app/routers/search.py', 'r', encoding='utf-8') as f:
            source = f.read()
        comic_events_source = source.split('def _comic_events')[1].split('@router.get("/users")')[0]
        self.assertIn('from sqlalchemy import case, or_, func, text, bindparam', source)
        self.assertIn("bindparam('eids', expanding=True)", comic_events_source)
        self.assertNotIn('{"eids": tuple(event_ids)}', comic_events_source)

    def test_recommendation_feed_clamps_per_page(self):
        with open('app/routers/recommendations.py', 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('per_page = min(per_page, 20)', source)


if __name__ == '__main__':
    unittest.main()
