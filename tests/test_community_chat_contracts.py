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
        self.assertIn('CommunityMember', join_source)
        self.assertIn("Conversation.type == 'community'", join_source)

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


if __name__ == '__main__':
    unittest.main()
