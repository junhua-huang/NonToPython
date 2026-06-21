import unittest


class ChatPerformanceContractsTest(unittest.TestCase):
    def test_sessions_endpoint_is_paginated(self):
        with open('app/routers/chat.py', 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('page: int = Query(1, ge=1)', source)
        self.assertIn('per_page: int = Query(30, ge=1, le=100)', source)
        self.assertIn('.offset(offset)', source)
        self.assertIn('.limit(per_page)', source)

    def test_mark_read_uses_bulk_update(self):
        with open('app/routers/chat.py', 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('.update({Message.is_read: True}', source)
        self.assertNotIn('for m in unread:\n        m.is_read = True', source)

    def test_batch_messages_rejects_more_than_twenty_conversations(self):
        with open('app/routers/chat.py', 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('最多 20 个', source)
        self.assertIn('Maximum 20 conversation IDs allowed', source)
        self.assertNotIn('conv_id_list = conv_id_list[:20]', source)

    def test_sessions_filters_blocked_users_before_count_and_pagination(self):
        with open('app/routers/chat.py', 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('other_user_expr', source)
        self.assertIn('~other_user_expr.in_(blocked_ids)', source)
        self.assertIn('total = base_query.count()', source)

    def test_batch_messages_does_not_log_raw_ids_at_info(self):
        with open('app/routers/chat.py', 'r', encoding='utf-8') as f:
            source = f.read()
        batch_source = source.split('@router.get("/messages/batch")')[1].split('@router.post("/conversations/{conversation_id}/messages")')[0]
        self.assertNotIn('logger.info(f"Getting messages batch for user {user.id}")', batch_source)
        self.assertNotIn('logger.info(f"Conv IDs: {conv_ids}")', batch_source)
        self.assertNotIn('logger.info(type(conv_ids))', batch_source)


if __name__ == '__main__':
    unittest.main()
