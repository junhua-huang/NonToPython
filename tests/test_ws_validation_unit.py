import unittest
from unittest.mock import patch

from app.routers import ws


class WebSocketValidationTests(unittest.TestCase):
    def test_text_send_payload_rejects_empty_content(self):
        payload = {"conversation_id": 10, "message_type": "text", "content": "   "}

        error = ws._validate_send_message_payload(payload)

        self.assertEqual(error, {"code": 400, "error": "Content cannot be empty"})

    def test_send_error_status_mapping_uses_client_error_codes(self):
        self.assertEqual(ws._send_error_status("Conversation not found"), 404)
        self.assertEqual(ws._send_error_status("Not a conversation participant"), 403)
        self.assertEqual(ws._send_error_status("Cannot send message to this user"), 403)
        self.assertEqual(ws._send_error_status("Message send failed"), 500)

    def test_send_payload_rejects_unknown_message_type(self):
        payload = {"conversation_id": 10, "message_type": "file", "content": "x"}

        error = ws._validate_send_message_payload(payload)

        self.assertEqual(error, {"code": 400, "error": "不支持的消息类型"})

    def test_send_payload_rejects_user_created_system_message(self):
        payload = {"conversation_id": 10, "message_type": "system", "content": "fake"}

        error = ws._validate_send_message_payload(payload)

        self.assertEqual(error, {"code": 403, "error": "系统消息不能由用户发送"})

    def test_existing_conversation_send_checks_block_state_between_participants(self):
        with patch.object(ws.ws_manager, "get_blocked_user_ids", side_effect=lambda uid: {1} if uid == 2 else set()):
            allowed = ws._can_send_to_participants(1, [1, 2])

        self.assertFalse(allowed)

    def test_existing_conversation_send_allows_unblocked_participants(self):
        with patch.object(ws.ws_manager, "get_blocked_user_ids", return_value=set()):
            allowed = ws._can_send_to_participants(1, [1, 2])

        self.assertTrue(allowed)

    def test_conversation_read_handler_checks_participant_before_update(self):
        import inspect

        source = inspect.getsource(ws._handle_conversation_read)

        self.assertIn("ConversationParticipant", source)
        self.assertIn("Not a conversation participant", source)
        self.assertLess(source.index("ConversationParticipant"), source.index(".update("))

    def test_websocket_endpoint_ignores_duplicate_auth_after_authenticated(self):
        import inspect

        source = inspect.getsource(ws.websocket_endpoint)
        normal_loop_source = source.split("# ── 阶段 3: 正常收发 ──")[1]
        unknown_source = normal_loop_source.split("# 未知类型")[0]

        self.assertIn('if msg_type == "auth":', unknown_source)
        self.assertIn('logger.debug(f"[WS AUTH] duplicate auth ignored uid={user_id}")', unknown_source)
        self.assertLess(
            unknown_source.index('if msg_type == "auth":'),
            normal_loop_source.index('# 未知类型'),
        )

    def test_ws_session_list_uses_message_to_dict_for_last_message(self):
        import inspect

        source = inspect.getsource(ws._build_session_list)
        last_message_source = source.split('last_messages = {}')[1].split('all_partner_ids = set()')[0]

        self.assertIn('last_messages[conv_id] = msg.to_dict()', last_message_source)
        self.assertNotIn('msg.created_at.isoformat()', last_message_source)

    def test_ws_send_persists_client_msg_id_and_uses_message_to_dict(self):
        import inspect

        source = inspect.getsource(ws._handle_send_message)
        persist_source = source.split('def _persist():')[1].split('loop = asyncio.get_event_loop()')[0]

        self.assertIn('client_msg_id=client_msg_id', persist_source)
        self.assertIn('msg_dict = inject_quote_preview(db, msg.to_dict())', persist_source)
        self.assertIn('"msg": msg_dict', persist_source)
        self.assertNotIn('msg.created_at.isoformat()', persist_source)


if __name__ == "__main__":
    unittest.main()
