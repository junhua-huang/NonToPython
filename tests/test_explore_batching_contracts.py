import unittest


class ExploreBatchingContractsTest(unittest.TestCase):
    def test_trending_topics_batches_follow_lookup(self):
        with open('app/routers/topics.py', 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('followed_topic_ids', source)
        self.assertIn('topic_followers.c.topic_id.in_(topic_ids)', source)
        self.assertNotIn('TopicService.is_user_following_topic(db, user.id, topic["id"])', source)

    def test_comic_events_batches_follow_lookup(self):
        with open('app/routers/comic.py', 'r', encoding='utf-8') as f:
            source = f.read()
        events_list_source = source.split('@router.get("/events/{event_id}")')[0]
        self.assertIn('followed_event_ids', events_list_source)
        self.assertIn('bindparam(\'event_ids\', expanding=True)', events_list_source)
        self.assertNotIn('SELECT 1 FROM comic_event_follows WHERE event_id = :eid AND user_id = :uid', events_list_source)

    def test_comic_events_filter_uses_dates_not_stale_status(self):
        with open('app/routers/comic.py', 'r', encoding='utf-8') as f:
            source = f.read()
        events_list_source = source.split('@router.get("/events/{event_id}")')[0]
        self.assertIn('e.end_date >= CURDATE()', events_list_source)
        self.assertNotIn('e.status IN (0, 1)', events_list_source)

    def test_comic_comment_enrichment_uses_expanding_bind_for_comment_ids(self):
        with open('app/routers/comic.py', 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn("bindparam('cids', expanding=True)", source)
        self.assertNotIn('{"cids": tuple(comment_ids)}', source)
        self.assertNotIn('{"cids": tuple(comment_ids), "uid": current_user_id}', source)


if __name__ == '__main__':
    unittest.main()
