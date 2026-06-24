import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from fastapi import HTTPException

from app.services import message_type_service as mts


class ChatMessageTypeContractsTest(unittest.TestCase):
    def test_supported_message_type_sets_are_product_scope(self):
        self.assertEqual(mts.USER_MESSAGE_TYPES, {"text", "image", "video", "post"})
        self.assertEqual(mts.SYSTEM_MESSAGE_TYPES, {"system"})
        self.assertEqual(mts.MESSAGE_TYPES, {"text", "image", "video", "post", "system"})
        self.assertNotIn("file", mts.MESSAGE_TYPES)
        self.assertNotIn("comment", mts.MESSAGE_TYPES)

    def test_user_payload_rejects_unknown_and_system_types(self):
        with self.assertRaises(HTTPException) as unknown:
            mts.normalize_user_message_payload({"message_type": "file"}, Mock())
        self.assertEqual(unknown.exception.status_code, 400)

        with self.assertRaises(HTTPException) as system:
            mts.normalize_user_message_payload({"message_type": "system", "content": "fake"}, Mock())
        self.assertEqual(system.exception.status_code, 403)

    def test_text_requires_content_and_media_requires_url(self):
        with self.assertRaises(HTTPException) as empty_text:
            mts.normalize_user_message_payload({"message_type": "text", "content": "   "}, Mock())
        self.assertEqual(empty_text.exception.status_code, 400)

        with self.assertRaises(HTTPException) as empty_image:
            mts.normalize_user_message_payload({"message_type": "image", "content": "   "}, Mock())
        self.assertEqual(empty_image.exception.status_code, 400)

    def test_media_uses_content_as_legacy_url_when_media_url_missing(self):
        result = mts.normalize_user_message_payload(
            {"message_type": "video", "content": "https://cdn.example/video.mp4"},
            Mock(),
        )
        self.assertEqual(result["message_type"], "video")
        self.assertEqual(result["media_url"], "https://cdn.example/video.mp4")
        self.assertEqual(result["content"], "https://cdn.example/video.mp4")

    def test_post_requires_existing_post_and_builds_preview(self):
        post = SimpleNamespace(
            id=42,
            content="这是一个很长的帖子正文，用于生成聊天卡片摘要。",
            images='["https://cdn.example/p1.jpg", "https://cdn.example/p2.jpg"]',
        )
        query = Mock()
        query.filter.return_value.first.return_value = post
        db = Mock()
        db.query.return_value = query

        result = mts.normalize_user_message_payload(
            {"message_type": "post", "related_id": "42"},
            db,
        )

        self.assertEqual(result["message_type"], "post")
        self.assertEqual(result["related_id"], 42)
        self.assertEqual(result["content"], "这是一个很长的帖子正文，用于生成聊天卡片摘要。")
        self.assertEqual(result["media_url"], "https://cdn.example/p1.jpg")

    def test_post_missing_related_id_and_missing_post_are_rejected(self):
        with self.assertRaises(HTTPException) as missing:
            mts.normalize_user_message_payload({"message_type": "post"}, Mock())
        self.assertEqual(missing.exception.status_code, 400)

        query = Mock()
        query.filter.return_value.first.return_value = None
        db = Mock()
        db.query.return_value = query
        with self.assertRaises(HTTPException) as not_found:
            mts.normalize_user_message_payload({"message_type": "post", "related_id": 999}, db)
        self.assertEqual(not_found.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
