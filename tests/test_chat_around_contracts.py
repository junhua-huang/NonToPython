"""私聊 around 接口契约测试。

覆盖：
- 正常窗口返回
- 跨会话目标 → 404
- 非参与者 → 403
- before/after 边界
"""
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import HTTPException


def _msg(i, conv_id, created_at, mtype="text"):
    msg = SimpleNamespace(
        id=i,
        conversation_id=conv_id,
        sender_id=1,
        content=f"msg-{i}",
        message_type=mtype,
        media_url=None,
        related_id=None,
        quote_message_id=None,
        quote_preview=None,
        is_read=True,
        is_recalled=False,
        recalled_at=None,
        deleted_by_admin=False,
        created_at=created_at,
    )

    def to_dict(self=None, m=msg):
        return {
            "id": m.id,
            "conversation_id": conv_id,
            "sender_id": 1,
            "content": f"msg-{m.id}",
            "message_type": mtype,
            "created_at": m.created_at.isoformat(),
        }

    msg.to_dict = to_dict
    return msg


def _first_query(returning):
    """构造返回单条（.filter(...).first()）的 query 链。"""
    q = Mock()
    q.filter.return_value.first.return_value = returning
    return q


def _list_query(returning):
    """构造返回列表（.filter(...).order_by(...).limit(...).all()）的 query 链。"""
    q = Mock()
    q.filter.return_value.order_by.return_value.limit.return_value.all.return_value = returning
    return q


class ChatAroundContractsTest(unittest.TestCase):
    def _build(self, target_conv=10, target_msg_id=5):
        base = datetime(2026, 6, 25, 10, 0, 0)
        self.all_msgs = [
            _msg(i, target_conv, base + timedelta(minutes=i))
            for i in range(1, 10)
        ]
        self.target = self.all_msgs[target_msg_id - 1]
        self.conv = SimpleNamespace(id=target_conv, user1_id=1, user2_id=2)
        self.user = SimpleNamespace(id=1)
        before_list = list(reversed([m for m in self.all_msgs if m.created_at < self.target.created_at]))
        after_list = [m for m in self.all_msgs if m.created_at > self.target.created_at]
        self.db = Mock()
        self.db.query.side_effect = [
            _first_query(self.conv),
            _first_query(None),  # 无双向屏蔽关系
            _first_query(self.target),
            _list_query(before_list),
            _list_query(after_list),
        ]

    def test_normal_window_returns_target_with_context(self):
        from app.routers import chat
        self._build()
        result = chat.get_messages_around(
            conversation_id=10, target_id=5,
            before=2, after=2, user=self.user, db=self.db,
        )
        ids = [m["id"] for m in result["messages"]]
        self.assertEqual(ids, [3, 4, 5, 6, 7])
        self.assertEqual(result["target_id"], 5)

    def test_target_in_other_conversation_404(self):
        from app.routers import chat
        self._build()
        self.db.query.side_effect = [
            _first_query(self.conv),
            _first_query(None),  # 无双向屏蔽关系
            _first_query(None),  # 跨会话找不到
        ]
        with self.assertRaises(HTTPException) as ctx:
            chat.get_messages_around(
                conversation_id=10, target_id=999,
                before=20, after=20, user=self.user, db=self.db,
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_non_participant_403(self):
        from app.routers import chat
        self._build()
        other_user = SimpleNamespace(id=99)
        with self.assertRaises(HTTPException) as ctx:
            chat.get_messages_around(
                conversation_id=10, target_id=5,
                before=20, after=20, user=other_user, db=self.db,
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_earliest_target_has_no_more_before(self):
        from app.routers import chat
        self._build(target_msg_id=1)  # 1 是最早
        after_list = [m for m in self.all_msgs if m.id > 1]
        self.db.query.side_effect = [
            _first_query(self.conv),
            _first_query(None),  # 无双向屏蔽关系
            _first_query(self.target),
            _list_query([]),  # before 为空
            _list_query(after_list),
        ]
        result = chat.get_messages_around(
            conversation_id=10, target_id=1,
            before=20, after=20, user=self.user, db=self.db,
        )
        self.assertFalse(result["has_more_before"])

    def test_before_window_triggers_has_more(self):
        from app.routers import chat
        self._build()
        # before 返回 21 条（before+1） → 触发 has_more_before
        extra_before = [_msg(i, 10, datetime(2026, 6, 25, 9, 0, 0) - timedelta(minutes=i))
                        for i in range(50)]
        # 截断到 before+1 = 21 条以触发 has_more
        self.db.query.side_effect = [
            _first_query(self.conv),
            _first_query(None),  # 无双向屏蔽关系
            _first_query(self.target),
            _list_query(extra_before[:21]),  # 21 条
            _list_query([]),
        ]
        result = chat.get_messages_around(
            conversation_id=10, target_id=5,
            before=20, after=20, user=self.user, db=self.db,
        )
        self.assertTrue(result["has_more_before"])


if __name__ == "__main__":
    unittest.main()
