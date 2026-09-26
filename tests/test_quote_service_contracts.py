"""聊天回复（quote）服务契约测试。

覆盖：
- 校验：被引消息存在 / 同会话 / 非 system / 已撤回。
- 预览：按 message_type 生成对应文案。
- 批量注入：N+1 防御 + None 处理。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import HTTPException

from app.services import quote_service as qs


def _stub_message(
    msg_id=10,
    conversation_id=1,
    message_type="text",
    content="hello",
    is_recalled=False,
    deleted_by_admin=False,
):
    return SimpleNamespace(
        id=msg_id,
        conversation_id=conversation_id,
        message_type=message_type,
        content=content,
        is_recalled=is_recalled,
        deleted_by_admin=deleted_by_admin,
    )


class QuoteValidateTest(unittest.TestCase):
    def _build_db(self, returned_message):
        db = Mock()
        query = Mock()
        query.filter.return_value.first.return_value = returned_message
        db.query.return_value = query
        return db

    def test_no_quote_returns_none(self):
        db = Mock()
        self.assertIsNone(qs.validate_quote(db, conversation_id=1, quote_message_id=None))
        db.query.assert_not_called()

    def test_quote_not_found_400(self):
        db = self._build_db(returned_message=None)
        with self.assertRaises(HTTPException) as ctx:
            qs.validate_quote(db, conversation_id=1, quote_message_id=99)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_cross_conversation_rejected(self):
        db = self._build_db(_stub_message(conversation_id=2))
        with self.assertRaises(HTTPException) as ctx:
            qs.validate_quote(db, conversation_id=1, quote_message_id=10)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_system_message_cannot_be_quoted(self):
        db = self._build_db(_stub_message(message_type="system"))
        with self.assertRaises(HTTPException) as ctx:
            qs.validate_quote(db, conversation_id=1, quote_message_id=10)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_recalled_message_is_allowed(self):
        msg = _stub_message(is_recalled=True)
        db = self._build_db(msg)
        result = qs.validate_quote(db, conversation_id=1, quote_message_id=10)
        self.assertIs(result, msg)


class QuotePreviewTest(unittest.TestCase):
    def test_none_returns_none(self):
        self.assertIsNone(qs.build_quote_preview(None))

    def test_recalled_text(self):
        msg = _stub_message(is_recalled=True, content="ignored")
        self.assertEqual(qs.build_quote_preview(msg), "消息已撤回")

    def test_admin_deleted_text(self):
        msg = _stub_message(deleted_by_admin=True)
        self.assertEqual(qs.build_quote_preview(msg), "消息已撤回")

    def test_text_truncates_at_50_chars(self):
        long_text = "你" * 80
        msg = _stub_message(content=long_text)
        preview = qs.build_quote_preview(msg)
        self.assertTrue(preview.endswith("..."))
        # 50 字符 + "..." 三位
        self.assertEqual(len(preview), 53)

    def test_text_short_returned_as_is(self):
        msg = _stub_message(content="嗨")
        self.assertEqual(qs.build_quote_preview(msg), "嗨")

    def test_image_label(self):
        msg = _stub_message(message_type="image", content="anything")
        self.assertEqual(qs.build_quote_preview(msg), "[图片]")

    def test_video_label(self):
        msg = _stub_message(message_type="video")
        self.assertEqual(qs.build_quote_preview(msg), "[视频]")

    def test_post_label_with_content(self):
        msg = _stub_message(message_type="post", content="今天好热")
        preview = qs.build_quote_preview(msg)
        self.assertTrue(preview.startswith("[帖子]"))
        self.assertIn("今天好热", preview)


class QuoteInjectTest(unittest.TestCase):
    def test_inject_none_when_no_quote_id(self):
        db = Mock()
        d = {"id": 1}
        out = qs.inject_quote_preview(db, d)
        self.assertIsNone(out["quote_preview"])
        db.query.assert_not_called()

    def test_inject_realtime_overrides_input_preview(self):
        msg = _stub_message(msg_id=99, content="实时")
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = msg
        out = qs.inject_quote_preview(db, {
            "id": 1,
            "conversation_id": 1,
            "quote_message_id": 99,
            "quote_preview": "陈旧的快照",  # 应被覆盖
        })
        self.assertEqual(out["quote_preview"], "实时")

    def test_inject_missing_quote_target_yields_recalled_placeholder(self):
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = None
        out = qs.inject_quote_preview(db, {
            "conversation_id": 1,
            "quote_message_id": 99,
        })
        self.assertEqual(out["quote_preview"], "消息已撤回")

    def test_inject_cross_conversation_quote_does_not_leak_preview(self):
        foreign_msg = _stub_message(
            msg_id=99,
            conversation_id=2,
            content="other conversation secret",
        )
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = foreign_msg
        out = qs.inject_quote_preview(db, {
            "conversation_id": 1,
            "quote_message_id": 99,
            "quote_preview": "陈旧的快照",
        })
        self.assertEqual(out["quote_preview"], "消息已撤回")
        self.assertNotIn("secret", out["quote_preview"])

    def test_batch_single_query_for_multiple_ids(self):
        msg10 = _stub_message(msg_id=10, conversation_id=1, content="a")
        msg11 = _stub_message(msg_id=11, conversation_id=1, content="b")
        db = Mock()
        db.query.return_value.filter.return_value.all.return_value = [msg10, msg11]
        items = [
            {"id": 1, "conversation_id": 1, "quote_message_id": 10},
            {"id": 2, "conversation_id": 1, "quote_message_id": 11},
            {"id": 3, "conversation_id": 1, "quote_message_id": 10},
            {"id": 4, "conversation_id": 1},
        ]
        out = qs.inject_quote_preview_batch(db, items)
        # 仅触发一次 db.query
        self.assertEqual(db.query.call_count, 1)
        self.assertEqual(out[0]["quote_preview"], "a")
        self.assertEqual(out[1]["quote_preview"], "b")
        self.assertEqual(out[2]["quote_preview"], "a")
        self.assertIsNone(out[3]["quote_preview"])

    def test_batch_missing_and_cross_conversation_quotes_use_safe_placeholder(self):
        valid_msg = _stub_message(msg_id=10, conversation_id=1, content="safe")
        foreign_msg = _stub_message(
            msg_id=11,
            conversation_id=2,
            content="other conversation secret",
        )
        db = Mock()
        db.query.return_value.filter.return_value.all.return_value = [
            valid_msg,
            foreign_msg,
        ]
        items = [
            {"id": 1, "conversation_id": 1, "quote_message_id": 10},
            {"id": 2, "conversation_id": 1, "quote_message_id": 11},
            {"id": 3, "conversation_id": 1, "quote_message_id": 12},
        ]
        out = qs.inject_quote_preview_batch(db, items)
        self.assertEqual(out[0]["quote_preview"], "safe")
        self.assertEqual(out[1]["quote_preview"], "消息已撤回")
        self.assertEqual(out[2]["quote_preview"], "消息已撤回")
        self.assertNotIn("secret", out[1]["quote_preview"])


if __name__ == "__main__":
    unittest.main()
