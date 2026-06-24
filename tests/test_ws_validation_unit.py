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


if __name__ == "__main__":
    unittest.main()
