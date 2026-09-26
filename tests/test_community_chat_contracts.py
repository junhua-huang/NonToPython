import unittest


class CommunityChatContractsTest(unittest.TestCase):
    def test_sessions_endpoint_includes_community_conversations_for_members(self):
        with open('app/routers/chat.py', 'r', encoding='utf-8') as f:
            source = f.read()
        sessions_source = source.split('@router.get("/sessions")')[1].split('@router.get("/conversations")')[0]

        self.assertIn('CommunityMember', sessions_source)
        self.assertIn("Conversation.type == 'community'", sessions_source)
        self.assertIn('CommunityMember.user_id == user.id', sessions_source)
        self.assertIn("\"type\": conv.type", sessions_source)
        self.assertIn("\"community_id\": conv.community_id", sessions_source)
        self.assertIn("\"community_name\": conv.community.name", sessions_source)
        self.assertIn("\"community_avatar\": conv.community.avatar_url", sessions_source)

    def test_ws_session_list_includes_community_conversations_and_rooms(self):
        with open('app/routers/ws.py', 'r', encoding='utf-8') as f:
            source = f.read()
        builder_source = source.split('def _build_session_list')[1].split('# ================================================================')[0]
        auth_rooms_source = source.split('# ── 自动加入会话房间 ──')[1].split('# ── 通知在线好友该用户已上线 ──')[0]

        self.assertIn('CommunityMember', builder_source)
        self.assertIn("Conversation.type == 'community'", builder_source)
        self.assertIn('CommunityMember.user_id == user_id', builder_source)
        self.assertIn("'type': conv.type", builder_source)
        self.assertIn("'community_id': conv.community_id", builder_source)
        self.assertIn("'community_name': conv.community.name", builder_source)
        self.assertIn("'community_avatar': conv.community.avatar_url", builder_source)
        self.assertIn('CommunityMember', auth_rooms_source)
        self.assertIn("Conversation.type == 'community'", auth_rooms_source)
        join_source = source.split('async def _handle_join')[1].split('async def _handle_leave')[0]
        self.assertIn('can_access_conversation', join_source)
        read_state_source = open('app/services/chat_read_state_service.py', 'r', encoding='utf-8').read()
        self.assertIn('CommunityMember', read_state_source)
        self.assertIn("Conversation.type == 'community'", read_state_source)

    def test_community_chat_routes_are_single_source_of_truth(self):
        with open('app/routers/communities.py', 'r', encoding='utf-8') as f:
            source = f.read()

        self.assertEqual(source.count('@router.get("/{community_id}/chat"'), 1)
        self.assertEqual(source.count('@router.post("/{community_id}/chat/messages"'), 1)
        self.assertEqual(source.count('@router.delete("/{community_id}/chat/messages/{message_id}"'), 1)
        self.assertIn('"new_message"', source)
        self.assertNotIn('"new_community_message"', source)

    def test_community_chat_messages_include_sender_profile_for_ui(self):
        with open('app/routers/communities.py', 'r', encoding='utf-8') as f:
            source = f.read()

        self.assertIn('def _community_message_to_dict', source)
        self.assertIn('message.sender.to_dict() if message.sender else None', source)
        self.assertIn('"sender":', source)
        self.assertIn('_community_message_to_dict(msg)', source)

    def test_community_chat_accepts_image_and_video_media_payloads(self):
        with open('app/routers/communities.py', 'r', encoding='utf-8') as f:
            source = f.read()

        self.assertIn('def _normalize_community_message_payload', source)
        self.assertIn('normalize_user_message_payload', source)
        with open('app/services/message_type_service.py', 'r', encoding='utf-8') as f:
            service_source = f.read()
        self.assertIn('"post"', service_source)
        self.assertIn('message_type=message_type', source)
        self.assertIn('media_url=media_url', source)
        self.assertIn('"media_url"', source)
        self.assertIn('"message_type"', source)

    def test_community_members_include_online_status_for_chat_header(self):
        with open('app/models/community.py', 'r', encoding='utf-8') as f:
            source = f.read()
        member_source = source.split('class CommunityMember')[1].split('class CommunityJoinRequest')[0]

        self.assertIn("'is_online'", member_source)
        self.assertIn('ws_manager.is_connected(self.user_id)', member_source)

    def test_ws_emits_community_member_presence_event(self):
        with open('app/routers/ws.py', 'r', encoding='utf-8') as f:
            ws_source = f.read()
        with open('app/ws_manager.py', 'r', encoding='utf-8') as f:
            manager_source = f.read()

        self.assertIn('community_member_presence', ws_source + manager_source)
        self.assertIn('notify_community_presence', manager_source)
        self.assertIn('send_with_seq', manager_source)
        self.assertIn('"community_id"', manager_source)
        self.assertIn('"conversation_id"', manager_source)
        self.assertIn('"user_id"', manager_source)
        self.assertIn('"is_online"', manager_source)
        self.assertIn('await self.is_product_online_async(user_id) != is_online', manager_source)
        self.assertIn('except Exception as e:', manager_source)
        self.assertIn('[WS PRESENCE] community presence failed', manager_source)

    def test_community_presence_online_only_on_first_app_connection(self):
        with open('app/routers/ws.py', 'r', encoding='utf-8') as f:
            source = f.read()
        auth_source = source.split('async def _do_auth_init')[1].split('# ================================================================\n# 业务处理器')[0]

        self.assertIn('was_raw_ws_offline = not ws_manager.is_connected(user_id)', auth_source)
        self.assertIn('was_product_online = await ws_manager.is_product_online_async(user_id)', auth_source)
        self.assertIn('conn_id = await ws_manager.connect(user_id, websocket)', auth_source)
        self.assertIn('if not was_product_online:', auth_source)
        self.assertIn('presence_generation = ws_manager.bump_presence_generation(user_id)', auth_source)
        self.assertIn('await ws_manager.notify_community_presence(user_id, True, presence_generation)', auth_source)

    def test_community_presence_targets_active_members_from_durable_membership(self):
        with open('app/ws_manager.py', 'r', encoding='utf-8') as f:
            source = f.read()

        self.assertIn('CommunityMember', source)
        self.assertIn("CommunityMember.status == 'active'", source)
        self.assertIn("Conversation.type == 'community'", source)
        self.assertIn('Conversation.community_id', source)
        self.assertIn('recipient_ids', source)
        self.assertIn('online_user_ids', source)
        self.assertIn('CommunityMember.user_id.in_(online_user_ids)', source)
        self.assertIn('member_user_id != user_id', source)
        self.assertIn('if self.is_connected(recipient_id):', source)
        self.assertIn('await self._send_presence_with_seq(recipient_id, "community_member_presence", payload, user_id, expected_generation)', source)

    def test_community_presence_offline_only_after_final_connection_disconnects(self):
        with open('app/ws_manager.py', 'r', encoding='utf-8') as f:
            source = f.read()
        disconnect_source = source.split('async def disconnect')[1].split('def heartbeat')[0]

        self.assertIn('if not self._connections[user_id]:', disconnect_source)
        self.assertIn('notify_community_presence(user_id, False, presence_generation)', disconnect_source)
        self.assertIn('self.schedule_offline_presence_check(user_id, BACKGROUND_ACTIVE_SECONDS, presence_generation)', disconnect_source)
        self.assertIn('_connections[user_id].pop', disconnect_source)

    def test_friend_offline_presence_ignores_stale_reconnect_tasks(self):
        with open('app/ws_manager.py', 'r', encoding='utf-8') as f:
            source = f.read()
        friend_offline_source = source.split('async def _notify_friends_offline')[1].split('def _get_community_presence_targets_sync')[0]

        self.assertIn('self.is_connected(user_id) or not self._is_presence_generation_current', friend_offline_source)
        self.assertIn('return', friend_offline_source)

    def test_presence_notifications_use_generation_to_drop_stale_tasks(self):
        with open('app/ws_manager.py', 'r', encoding='utf-8') as f:
            manager_source = f.read()
        with open('app/routers/ws.py', 'r', encoding='utf-8') as f:
            ws_source = f.read()

        self.assertIn('_presence_generation', manager_source)
        self.assertIn('def bump_presence_generation', manager_source)
        self.assertIn('expected_generation', manager_source)
        self.assertIn('_is_presence_generation_current(user_id, expected_generation)', manager_source)
        self.assertIn('presence_generation = ws_manager.bump_presence_generation(user_id)', manager_source + ws_source)
        self.assertIn('async def _send_presence_with_seq', manager_source)
        self.assertIn('async def _next_seq_if_presence_generation_current', manager_source)
        self.assertIn('def _next_seq_if_presence_generation_current_sync', manager_source)
        self.assertIn('seq = await self._next_seq_if_presence_generation_current(', manager_source)
        self.assertIn('with self._presence_generation_lock:', manager_source)
        self.assertIn('self._presence_generation.get(subject_user_id, 0) != expected_generation', manager_source)
        self.assertIn('seq = self._next_seq_sync(user_id, payload)', manager_source)
        self.assertIn('self._delete_message_log_sync(user_id, seq)', manager_source)
        self.assertIn('await self._delete_message_log(recipient_id, seq)', manager_source)
        self.assertIn('_send_presence_with_seq(recipient_id, "community_member_presence", payload, user_id, expected_generation)', manager_source)

    def test_my_communities_response_includes_current_membership_for_share_targets(self):
        from types import SimpleNamespace

        from app.routers.communities import my_communities
        from app.services.community_service import CommunityService

        class CommunityStub:
            def __init__(self, community_id, name):
                self.id = community_id
                self.name = name

            def to_dict(self):
                return {"id": self.id, "name": self.name}

        class QueryStub:
            def __init__(self, members):
                self.members = members

            def filter(self, *args):
                return self

            def all(self):
                return self.members

        class DbStub:
            def __init__(self, members):
                self.members = members

            def query(self, model):
                return QueryStub(self.members)

        communities = [
            CommunityStub(101, "摄影群"),
            CommunityStub(202, "活动群"),
        ]
        members = [
            SimpleNamespace(community_id=202, role="member", status="active"),
            SimpleNamespace(community_id=101, role="admin", status="active"),
        ]
        original = CommunityService.list_my_communities
        CommunityService.list_my_communities = staticmethod(
            lambda db, user_id, manage_only=False: communities
        )
        try:
            result = my_communities(
                user=SimpleNamespace(id=9),
                db=DbStub(members),
            )
        finally:
            CommunityService.list_my_communities = original

        self.assertEqual(result, {
            "communities": [
                {"id": 101, "name": "摄影群", "my_role": "admin", "my_status": "active"},
                {"id": 202, "name": "活动群", "my_role": "member", "my_status": "active"},
            ]
        })

    def test_community_membership_maintains_conversation_participants_without_migration(self):
        with open('app/services/community_service.py', 'r', encoding='utf-8') as f:
            source = f.read()

        self.assertIn('ConversationParticipant', source)
        self.assertIn('def _ensure_chat_participant', source)
        self.assertIn('Conversation.community_id == community_id', source)
        self.assertIn('ConversationParticipant.user_id == user_id', source)
        self.assertIn('CommunityService._ensure_chat_participant(db, community.id, owner_id)', source)
        self.assertIn('CommunityService._ensure_chat_participant(db, community_id, user_id)', source)
        self.assertIn('CommunityService._ensure_chat_participant(db, community_id, req.user_id)', source)

    def test_batch_messages_preserves_quote_fields_for_reply_display(self):
        with open('app/routers/chat.py', 'r', encoding='utf-8') as f:
            source = f.read()
        batch_source = source.split('def get_messages_batch')[1].split('# ============================================================')[0]

        self.assertIn('quote_message_id', batch_source)
        self.assertIn('quote_preview', batch_source)
        self.assertIn('is_recalled', batch_source)
        self.assertIn('inject_quote_preview_batch(db,', batch_source)

    def test_batch_messages_preserves_client_msg_id_and_utc_z_timestamps(self):
        with open('app/routers/chat.py', 'r', encoding='utf-8') as f:
            source = f.read()
        batch_source = source.split('def get_messages_batch')[1].split('# ============================================================')[0]

        self.assertIn('client_msg_id', batch_source)
        self.assertIn('"client_msg_id": msg.client_msg_id', batch_source)
        self.assertIn('"created_at": _utc_z(msg.created_at)', batch_source)
        self.assertIn('"updated_at": _utc_z(msg.created_at)', batch_source)
        self.assertNotIn('msg.created_at.isoformat()', batch_source)

    def test_mark_read_authorizes_community_participants(self):
        with open('app/routers/chat.py', 'r', encoding='utf-8') as f:
            source = f.read()
        mark_read_source = source.split('async def mark_conversation_as_read')[1].split('@router.get("/users/online")')[0]
        with open('app/services/chat_read_state_service.py', 'r', encoding='utf-8') as f:
            read_state_source = f.read()
        participant_source = read_state_source.split('def get_community_participant')[1].split('def mark_community_conversation_read')[0]

        self.assertIn('get_community_participant(db, conversation_id, user.id)', mark_read_source)
        self.assertIn("conversation.type == 'community'", mark_read_source)
        self.assertIn('ConversationParticipant', participant_source)
        self.assertIn('ConversationParticipant.conversation_id == conversation_id', participant_source)
        self.assertIn('ConversationParticipant.user_id == user_id', participant_source)

    def test_mark_read_total_unread_includes_community_membership(self):
        with open('app/routers/chat.py', 'r', encoding='utf-8') as f:
            source = f.read()
        mark_read_source = source.split('async def mark_conversation_as_read')[1].split('@router.get("/users/online")')[0]
        with open('app/services/chat_read_state_service.py', 'r', encoding='utf-8') as f:
            read_state_source = f.read()
        participant_source = read_state_source.split('def get_community_participant')[1].split('def mark_community_conversation_read')[0]

        self.assertIn('get_total_unread_count(db, user.id)', mark_read_source)
        self.assertIn('CommunityMember', participant_source)
        self.assertIn("Conversation.type == 'community'", participant_source)
        self.assertIn('CommunityMember.user_id == ConversationParticipant.user_id', participant_source)
        self.assertIn('CommunityMember.status == \'active\'', participant_source)

    def test_message_to_dict_includes_client_msg_id_and_utc_z_timestamps(self):
        from datetime import datetime

        from app.models.models import Message

        msg = Message(
            id=10,
            conversation_id=20,
            sender_id=30,
            content='hello',
            message_type='text',
            created_at=datetime(2026, 6, 26, 13, 0, 0),
        )
        msg.client_msg_id = 'community-client-1'
        msg.recalled_at = datetime(2026, 6, 26, 13, 1, 2)

        data = msg.to_dict()

        self.assertEqual(data['client_msg_id'], 'community-client-1')
        self.assertEqual(data['created_at'], '2026-06-26T13:00:00Z')
        self.assertEqual(data['recalled_at'], '2026-06-26T13:01:02Z')

    def test_community_send_accepts_persists_and_broadcasts_client_msg_id(self):
        with open('app/routers/communities.py', 'r', encoding='utf-8') as f:
            source = f.read()
        send_source = source.split('async def send_community_message')[1].split('@router.delete("/{community_id}/chat/messages/{message_id}")')[0]

        self.assertIn('client_msg_id = payload.get("client_msg_id")', send_source)
        self.assertIn('client_msg_id=client_msg_id', send_source)
        self.assertIn('_community_message_to_dict(msg),', send_source)
        self.assertIn('viewer_user_id=user.id', send_source)
        self.assertIn('"message": delivered_message', send_source)


if __name__ == '__main__':
    unittest.main()
