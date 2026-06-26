import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding='utf-8')


class ChatReadStateContractsTest(unittest.TestCase):
    def test_conversation_participant_has_member_read_cursor_fields(self):
        source = read('app/models/models.py')
        participant_source = source.split('class ConversationParticipant')[1].split('class Message')[0]

        self.assertIn('last_read_at', participant_source)
        self.assertIn('last_read_message_id', participant_source)
        self.assertIn('DateTime', participant_source)

    def test_read_cursor_migration_is_idempotent_and_initializes_community_members(self):
        migration_path = ROOT / 'alembic' / 'versions' / '2026_06_26_1900-add_conversation_participant_read_cursors.py'
        self.assertTrue(migration_path.exists())
        source = migration_path.read_text(encoding='utf-8')

        self.assertIn("last_read_at", source)
        self.assertIn("last_read_message_id", source)
        self.assertIn("_column_names", source)
        self.assertIn("_index_names", source)
        self.assertIn("conversation_participants", source)
        self.assertIn("UPDATE conversation_participants", source)
        self.assertIn("MAX(m.id)", source)
        self.assertIn("MAX(m.created_at)", source)

    def test_read_state_service_uses_participant_cursor_for_community_unread(self):
        source = read('app/services/chat_read_state_service.py')

        self.assertIn('def get_community_participant', source)
        self.assertIn('def can_access_conversation', source)
        self.assertIn('def get_active_conversation_participant_ids', source)
        self.assertIn('def mark_community_conversation_read', source)
        self.assertIn('def get_community_unread_counts', source)
        self.assertIn('def get_total_unread_count', source)
        self.assertIn('ConversationParticipant.last_read_message_id', source)
        self.assertIn('last_read_at', source)
        self.assertIn('CommunityMember.status == \'active\'', source)
        community_count_source = source.split('def get_community_unread_counts')[1].split('def get_total_unread_count')[0]
        self.assertIn('ConversationParticipant.last_read_message_id.is_(None)', community_count_source)
        self.assertIn('Message.id > ConversationParticipant.last_read_message_id', community_count_source)
        self.assertNotIn('Message.created_at > ConversationParticipant.last_read_at', community_count_source)
        self.assertNotIn('Message.is_read == False', community_count_source)

    def test_http_mark_read_branches_community_to_participant_cursor(self):
        source = read('app/routers/chat.py')
        mark_source = source.split('async def mark_conversation_as_read')[1].split('@router.get("/users/online")')[0]

        self.assertIn('mark_community_conversation_read', mark_source)
        self.assertIn('get_total_unread_count', mark_source)
        self.assertIn("if conversation.type == 'community'", mark_source)
        read_branch = mark_source.split("if conversation.type == 'community'")[2].split('else:')[0]
        self.assertIn('mark_community_conversation_read', read_branch)
        self.assertNotIn('Message.is_read', read_branch)
        direct_branch = mark_source.split("if conversation.type == 'community'")[2].split('else:')[1]
        self.assertIn('Message.is_read == False', direct_branch)

    def test_unread_count_and_ws_sessions_use_shared_read_state(self):
        chat_source = read('app/routers/chat.py')
        unread_source = chat_source.split('def get_unread_count')[1].split('# ============================================================')[0]
        sessions_source = chat_source.split('@router.get("/sessions")')[1].split('@router.get("/conversations")')[0]
        ws_source = read('app/routers/ws.py')
        ws_session_source = ws_source.split('def _build_session_list')[1].split('# ================================================================')[0]
        ws_read_source = ws_source.split('async def _handle_conversation_read')[1].split('async def _handle_notifications_read')[0]

        self.assertIn('get_total_unread_count', unread_source)
        self.assertIn('get_community_unread_counts', sessions_source)
        self.assertIn('get_community_unread_counts', ws_session_source)
        self.assertIn('mark_community_conversation_read', ws_read_source)
        community_branch = ws_read_source.split("if conversation.type == 'community'")[1].split('else:')[0]
        self.assertIn('mark_community_conversation_read', community_branch)
        self.assertNotIn('Message.is_read', community_branch)
        self.assertNotIn('.update({"is_read": True})', community_branch)
        self.assertNotIn(".update({'is_read': True})", community_branch)

    def test_ws_community_paths_require_active_membership_and_cursor_unread(self):
        source = read('app/routers/ws.py')
        session_source = source.split('def _build_session_list')[1].split('# ================================================================')[0]
        send_source = source.split('async def _handle_send_message')[1].split('async def _handle_conversation_read')[0]
        join_source = source.split('async def _handle_join')[1].split('async def _handle_leave')[0]

        self.assertIn('get_active_conversation_participant_ids', send_source)
        self.assertIn('get_community_unread_counts', send_source)
        self.assertIn('can_access_conversation', join_source)
        self.assertNotIn('ConversationParticipant.user_id == user_id)\n            |', session_source)
        community_push_source = send_source.split('participant_ids = get_active_conversation_participant_ids')[1]
        self.assertNotIn('Message.is_read == False', community_push_source)

    def test_http_message_endpoints_authorize_active_community_members(self):
        source = read('app/routers/chat.py')

        for marker, next_marker in [
            ('def get_messages(', '@router.get("/messages/batch")'),
            ('def get_messages_batch(', '# 合并为单次 UNION ALL 查询'),
            ('def get_messages_v2(', '@router.post("/conversations/{conversation_id}/messages")'),
            ('def send_message(', '@router.post("/conversations/{conversation_id}/mark-read")'),
            ('def get_messages_around(', 'target = db.query(Message)'),
        ]:
            endpoint_source = source.split(marker)[1].split(next_marker)[0]
            self.assertIn('can_access_conversation', endpoint_source)
            self.assertNotIn('conversation.user1_id != user.id and conversation.user2_id != user.id', endpoint_source)


if __name__ == '__main__':
    unittest.main()
