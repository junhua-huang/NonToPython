import inspect
import unittest
from datetime import datetime

from app.models.models import Message
from app.routers import chat, ws


TIME_ALIAS_KEYS = {"createdAt", "sent_at", "sentAt", "timestamp", "time", "updated_at"}


class ChatMessageTimeContractsTest(unittest.TestCase):
    def test_message_to_dict_exposes_only_canonical_created_at(self):
        message = Message(
            id=1,
            conversation_id=2,
            sender_id=3,
            content="hello",
            created_at=datetime(2026, 6, 26, 13, 0, 0),
        )

        data = message.to_dict()

        self.assertIn("created_at", data)
        self.assertEqual(data["created_at"], "2026-06-26T13:00:00Z")
        for key in TIME_ALIAS_KEYS:
            self.assertNotIn(key, data)

    def test_batch_messages_uses_canonical_created_at_without_time_aliases(self):
        source = inspect.getsource(chat.get_messages_batch)
        message_build_source = source.split("message_dicts = [")[1].split("result_conversations.append")[0]

        self.assertIn('"created_at": _utc_z(msg.created_at)', message_build_source)
        for key in TIME_ALIAS_KEYS - {"updated_at"}:
            self.assertNotIn(f'"{key}"', message_build_source)
            self.assertNotIn(f"'{key}'", message_build_source)

    def test_websocket_message_push_uses_canonical_message_to_dict(self):
        source = inspect.getsource(ws._handle_send_message)
        persist_source = source.split("def _persist():")[1].split("loop = asyncio.get_event_loop()")[0]

        self.assertIn("msg.to_dict()", persist_source)
        for key in TIME_ALIAS_KEYS:
            self.assertNotIn(f'"{key}"', persist_source)
            self.assertNotIn(f"'{key}'", persist_source)

    def test_incremental_history_contract_filters_after_id(self):
        source = inspect.getsource(chat.get_messages)

        self.assertIn("after_id", source)
        self.assertIn("Message.id > after_id", source)
        self.assertIn('"has_more": has_more', source)
        self.assertIn("order_by(Message.id.asc())", source)


if __name__ == "__main__":
    unittest.main()
