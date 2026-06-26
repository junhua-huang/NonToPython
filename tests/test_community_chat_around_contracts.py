"""群聊 around 接口 + before_id 分页契约测试。

采用源码内省 + 行为双轨：行为测试覆盖关键路径，源码内测覆盖路由声明。
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
    msg.sender = SimpleNamespace(
        id=1, username="u", to_dict=lambda: {"id": 1, "username": "u"}
    )

    def to_dict(self=None, m=msg):
        return {
            "id": m.id,
            "conversation_id": conv_id,
            "content": f"msg-{m.id}",
            "message_type": mtype,
            "created_at": m.created_at.isoformat(),
        }

    msg.to_dict = to_dict
    return msg


def _first(returning):
    q = Mock()
    q.filter.return_value.first.return_value = returning
    return q


def _list(returning):
    q = Mock()
    q.filter.return_value.order_by.return_value.limit.return_value.all.return_value = returning
    return q


class CommunityChatAroundSourceTest(unittest.TestCase):
    def test_routes_declared(self):
        with open('app/routers/communities.py', 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('@router.get("/{community_id}/chat/messages/around")', source)
        self.assertIn('def get_community_message_around', source)
        self.assertIn('target_id: int = Query(..., ge=1)', source)
        self.assertIn('before: int = Query(20, ge=1, le=50)', source)
        self.assertIn('after: int = Query(20, ge=1, le=50)', source)

    def test_before_id_pagination_supported_in_get_chat(self):
        with open('app/routers/communities.py', 'r', encoding='utf-8') as f:
            source = f.read()
        chat_section = source.split('def get_community_chat')[1].split('def get_community_message_around')[0]
        self.assertIn('before_id: int = Query(None, ge=1)', chat_section)
        self.assertIn('Message.id < before_id', chat_section)
        self.assertIn('"has_more": has_more', chat_section)
        # limit+1 模式判断 has_more
        self.assertIn('limit(limit + 1)', chat_section)


class CommunityChatAroundBehaviorTest(unittest.TestCase):
    def _build(self, target_id=5):
        base = datetime(2026, 6, 25, 10, 0, 0)
        self.all_msgs = [_msg(i, 100, base + timedelta(minutes=i)) for i in range(1, 10)]
        self.target = self.all_msgs[target_id - 1]
        self.community = SimpleNamespace(id=7, status='active')
        self.member = SimpleNamespace(community_id=7, user_id=1, status='active')
        self.conv = SimpleNamespace(id=100, type='community', community_id=7, to_dict=lambda: {"id": 100})
        self.user = SimpleNamespace(id=1)
        before_list = list(reversed([m for m in self.all_msgs if m.created_at < self.target.created_at]))
        after_list = [m for m in self.all_msgs if m.created_at > self.target.created_at]
        self.db = Mock()
        self.db.query.side_effect = [
            _first(self.community),     # Community
            _first(self.member),        # CommunityMember
            _first(self.conv),          # Conversation
            _first(self.target),        # target Message
            _list(before_list),         # before
            _list(after_list),          # after
        ]

    def test_normal_window(self):
        from app.routers import communities
        self._build()
        result = communities.get_community_message_around(
            community_id=7, target_id=5,
            before=2, after=2, user=self.user, db=self.db,
        )
        ids = [m["id"] for m in result["messages"]]
        self.assertEqual(ids, [3, 4, 5, 6, 7])
        self.assertEqual(result["target_id"], 5)

    def test_non_member_403(self):
        from app.routers import communities
        self._build()
        other_user = SimpleNamespace(id=99)
        # member 查询返回 None
        self.db.query.side_effect = [
            _first(self.community),
            _first(None),
        ]
        with self.assertRaises(HTTPException) as ctx:
            communities.get_community_message_around(
                community_id=7, target_id=5,
                before=20, after=20, user=other_user, db=self.db,
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_target_in_other_conversation_404(self):
        from app.routers import communities
        self._build()
        self.db.query.side_effect = [
            _first(self.community),
            _first(self.member),
            _first(self.conv),
            _first(None),  # target 跨会话
        ]
        with self.assertRaises(HTTPException) as ctx:
            communities.get_community_message_around(
                community_id=7, target_id=999,
                before=20, after=20, user=self.user, db=self.db,
            )
        self.assertEqual(ctx.exception.status_code, 404)


class CommunityChatPaginationBehaviorTest(unittest.TestCase):
    def _build_pagination(self, returning):
        self.community = SimpleNamespace(id=7, status='active')
        self.member = SimpleNamespace(community_id=7, user_id=1, status='active')
        self.conv = SimpleNamespace(id=100, type='community', community_id=7, to_dict=lambda: {"id": 100})
        self.user = SimpleNamespace(id=1)
        self.db = Mock()
        # messages query：支持 filter 链式多次调用（is_recalled + 可选 before_id）
        msg_q = Mock()
        terminal = Mock()
        terminal.limit.return_value.all.return_value = returning
        terminal.order_by.return_value.limit.return_value.all.return_value = returning
        # 不论 filter 调几次都返回同一个可继续链的对象
        msg_q.filter.return_value = msg_q
        msg_q.order_by.return_value = terminal
        msg_q.limit.return_value = terminal
        self.db.query.side_effect = [
            _first(self.community),
            _first(self.member),
            _first(self.conv),
            msg_q,
        ]

    def test_before_id_filters_older_messages(self):
        from app.routers import communities
        base = datetime(2026, 6, 25, 10, 0, 0)
        # 服务器用 created_at.desc() 拉，再 reversed() 转升序返回
        # 4 条 older messages（id 4,3,2,1），DB 降序返回顺序应为 4,3,2,1
        msgs_desc = [_msg(i, 100, base + timedelta(minutes=i)) for i in [4, 3, 2, 1]]
        self._build_pagination(msgs_desc)
        result = communities.get_community_chat(
            community_id=7, limit=50, before_id=5, user=self.user, db=self.db,
        )
        ids = [m["id"] for m in result["messages"]]
        self.assertEqual(ids, [1, 2, 3, 4])
        self.assertFalse(result["has_more"])

    def test_has_more_true_when_more_than_limit_returned(self):
        from app.routers import communities
        base = datetime(2026, 6, 25, 10, 0, 0)
        # 返回 limit+1 = 51 条 → has_more=True，截断为 50
        msgs_desc = [_msg(i, 100, base + timedelta(minutes=i)) for i in range(51, 0, -1)]
        self._build_pagination(msgs_desc)
        result = communities.get_community_chat(
            community_id=7, limit=50, before_id=None, user=self.user, db=self.db,
        )
        self.assertTrue(result["has_more"])
        self.assertEqual(len(result["messages"]), 50)


if __name__ == "__main__":
    unittest.main()
