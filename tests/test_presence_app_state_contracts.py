import inspect
import pathlib
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.models import PushDevice, User


ROOT = pathlib.Path(__file__).resolve().parents[1]


class PresenceAppStateContracts(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_presence_service_defines_product_presence_from_recent_active_devices(self):
        from app.services import presence_service

        source = inspect.getsource(presence_service)

        self.assertEqual(presence_service.BACKGROUND_ACTIVE_SECONDS, 300)
        self.assertEqual(presence_service.ACTIVE_APP_STATES, {"foreground", "background"})
        self.assertIn("def has_recent_active_device", source)
        self.assertIn("def is_user_product_online", source)
        self.assertIn("PushDevice", source)
        self.assertIn("PushDevice.enabled == True", source)
        self.assertIn("PushDevice.app_state.in_(ACTIVE_APP_STATES)", source)
        self.assertIn("PushDevice.last_seen_at.isnot(None)", source)
        self.assertIn("PushDevice.last_seen_at >= cutoff", source)
        self.assertIn("timedelta(seconds=BACKGROUND_ACTIVE_SECONDS)", source)

    def test_product_presence_prefers_raw_websocket_online_before_device_query(self):
        from app.services.presence_service import is_user_product_online

        class ExplodingDb:
            def query(self, *args, **kwargs):
                raise AssertionError("raw websocket online should not query PushDevice")

        self.assertTrue(is_user_product_online(ExplodingDb(), user_id=123, raw_ws_online=True))

    def test_product_presence_excludes_stale_disabled_and_unknown_devices(self):
        from app.services.presence_service import is_user_product_online

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        now = datetime.utcnow()
        try:
            db.add_all([
                User(id=1, username="stale", email="stale@example.com", password_hash="x"),
                User(id=2, username="disabled", email="disabled@example.com", password_hash="x"),
                User(id=3, username="unknown", email="unknown@example.com", password_hash="x"),
                User(id=4, username="active", email="active@example.com", password_hash="x"),
                PushDevice(
                    user_id=1,
                    device_id="stale-device",
                    enabled=True,
                    app_state="background",
                    last_seen_at=now - timedelta(seconds=301),
                ),
                PushDevice(
                    user_id=2,
                    device_id="disabled-device",
                    enabled=False,
                    app_state="foreground",
                    last_seen_at=now,
                ),
                PushDevice(
                    user_id=3,
                    device_id="unknown-device",
                    enabled=True,
                    app_state="unknown",
                    last_seen_at=now,
                ),
                PushDevice(
                    user_id=4,
                    device_id="active-device",
                    enabled=True,
                    app_state="background",
                    last_seen_at=now,
                ),
            ])
            db.commit()

            self.assertFalse(is_user_product_online(db, 1))
            self.assertFalse(is_user_product_online(db, 2))
            self.assertFalse(is_user_product_online(db, 3))
            self.assertTrue(is_user_product_online(db, 4))
        finally:
            db.close()

    def test_ws_manager_product_presence_async_checks_use_executor_and_recheck_after_ttl(self):
        source = self.read("app/ws_manager.py")

        self.assertIn("async def is_product_online_async", source)
        self.assertIn("run_in_executor(None, self.is_product_online, user_id)", source)
        self.assertIn("async def _delayed_offline_presence_check", source)
        self.assertIn("BACKGROUND_ACTIVE_SECONDS", source)
        self.assertIn("await asyncio.sleep(delay_seconds)", source)
        self.assertIn("self.schedule_offline_presence_check(user_id, BACKGROUND_ACTIVE_SECONDS", source)

    def test_presence_sequence_sending_uses_async_sequence_helper(self):
        source = self.read("app/ws_manager.py")
        send_presence_source = source.split("async def _send_presence_with_seq")[1].split("async def send_error")[0]
        guarded_sync_helper_source = source.split("def _next_seq_if_presence_generation_current_sync")[1].split("async def _next_seq_if_presence_generation_current")[0]
        guarded_async_helper_source = source.split("async def _next_seq_if_presence_generation_current")[1].split("async def _next_seq")[0]

        self.assertIn("seq = await self._next_seq_if_presence_generation_current(", send_presence_source)
        self.assertIn("recipient_id, full_payload, subject_user_id, expected_generation", send_presence_source)
        self.assertNotIn("seq = await self._next_seq(recipient_id, full_payload)", send_presence_source)
        self.assertNotIn("_next_seq_sync", send_presence_source)
        self.assertIn("await self._delete_message_log(recipient_id, seq)", send_presence_source)
        self.assertIn("run_in_executor", guarded_async_helper_source)
        self.assertIn("_next_seq_if_presence_generation_current_sync", guarded_async_helper_source)
        self.assertIn("with self._presence_generation_lock:", guarded_sync_helper_source)
        self.assertIn("self._presence_generation.get(subject_user_id, 0) != expected_generation", guarded_sync_helper_source)
        self.assertIn("seq = self._next_seq_sync(user_id, payload)", guarded_sync_helper_source)
        self.assertIn("self._delete_message_log_sync(user_id, seq)", guarded_sync_helper_source)

    def test_presence_generation_lock_not_held_during_guarded_sequence_persistence(self):
        from app.ws_manager import WSManager

        manager = WSManager()
        manager._presence_generation = {42: 7}
        observed = {}

        def fake_next_seq(user_id, payload):
            observed["locked_during_seq"] = manager._presence_generation_lock.locked()
            return 99

        manager._next_seq_sync = fake_next_seq

        seq = manager._next_seq_if_presence_generation_current_sync(
            1,
            {"event": "community_member_presence", "data": {"user_id": 42}},
            42,
            7,
        )

        self.assertEqual(seq, 99)
        self.assertFalse(observed["locked_during_seq"])

    def test_push_explicit_inactive_paths_recheck_offline_immediately(self):
        source = self.read("app/routers/push.py")
        transition_source = source.split("def _notify_product_presence_transition")[1].split("def _after_device_state_change")[0]
        active_branch = transition_source.split("if app_state in ACTIVE_APP_STATES:")[1].split("elif was_product_online")[0]
        inactive_branch = transition_source.split("elif was_product_online")[1]
        unregister_source = source.split('def unregister_device')[1].split('@router.post("/devices/state")')[0]
        state_source = source.split('def update_device_state')[1].split('@router.get("/devices/status")')[0]

        self.assertIn('"inactive"', source)
        self.assertIn('"inactive"', source.split("VALID_APP_STATES = ")[1].split("\n")[0])
        self.assertIn("presence_generation = ws_manager.bump_presence_generation(user_id)", transition_source)
        self.assertIn("ws_manager.schedule_presence_online_from_product_state(user_id, presence_generation)", active_branch)
        self.assertIn("ws_manager.schedule_offline_presence_check(user_id, BACKGROUND_ACTIVE_SECONDS, presence_generation)", active_branch)
        self.assertNotIn("delay_seconds=0", active_branch)
        self.assertIn("delay_seconds=0", inactive_branch)
        self.assertIn("expected_generation=presence_generation", inactive_branch)
        self.assertIn('_after_device_state_change(current_user.id, was_product_online, "unknown")', unregister_source)
        self.assertIn("_after_device_state_change(current_user.id, was_product_online, app_state)", state_source)

    def test_ws_router_removes_unused_raw_friend_online_helper(self):
        source = self.read("app/routers/ws.py")

        self.assertNotIn("async def _notify_friends_online(websocket", source)

    def test_chat_status_endpoint_uses_product_presence(self):
        source = self.read("app/routers/chat.py")
        status_source = source.split('def get_user_status')[1].split('@router.get("/unread-count")')[0]

        self.assertIn("is_user_product_online", source)
        self.assertIn("raw_ws_online=ws_manager.is_connected(user_id)", status_source)
        self.assertIn('"is_online": is_user_product_online(', status_source)

    def test_chat_session_online_flags_use_product_presence(self):
        source = self.read("app/routers/chat.py")
        sessions_source = source.split('@router.get("/sessions")')[1].split('@router.get("/conversations")')[0]
        conversations_source = source.split('def get_conversations')[1].split('@router.get("/conversations/{user_id}")')[0]
        conversation_user_source = source.split('def get_conversation_with_user')[1].split('@router.get("/conversations/{conversation_id}/messages")')[0]

        self.assertIn("is_user_product_online(", sessions_source)
        self.assertIn("other_user.id", sessions_source)
        self.assertIn("raw_ws_online=ws_manager.is_connected(other_user.id)", sessions_source)
        self.assertIn("is_user_product_online(", conversations_source)
        self.assertIn("other_user.id", conversations_source)
        self.assertIn("raw_ws_online=ws_manager.is_connected(other_user.id)", conversations_source)
        self.assertIn("is_user_product_online(", conversation_user_source)
        self.assertIn("user_id", conversation_user_source)
        self.assertIn("raw_ws_online=ws_manager.is_connected(user_id)", conversation_user_source)

    def test_ws_manager_skips_offline_presence_broadcast_when_product_online(self):
        source = self.read("app/ws_manager.py")
        disconnect_source = source.split('async def disconnect')[1].split('def heartbeat')[0]
        friend_offline_source = source.split('async def _notify_friends_offline')[1].split('def _get_community_presence_targets_sync')[0]
        community_presence_source = source.split('async def notify_community_presence')[1].split('# ================================================================')[0]

        self.assertIn("is_user_product_online", source)
        self.assertIn("await self.is_product_online_async(user_id)", disconnect_source)
        self.assertIn("await self.is_product_online_async(user_id)", friend_offline_source)
        self.assertIn("await self.is_product_online_async(user_id) != is_online", community_presence_source)

    def test_ws_auth_does_not_emit_online_when_user_was_already_product_online(self):
        source = self.read("app/routers/ws.py")
        auth_source = source.split('async def _do_auth_init')[1].split('# ================================================================\n# 业务处理器')[0]

        self.assertIn('was_product_online = await ws_manager.is_product_online_async(user_id)', auth_source)
        self.assertIn('if not was_product_online:', auth_source)
        self.assertNotIn('if was_offline:', auth_source)

    def test_online_users_endpoint_includes_product_online_friends(self):
        import app.routers.chat as chat

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine)
        db = Session()
        now = datetime.utcnow()
        try:
            requester = User(id=10, username="requester", email="requester@example.com", password_hash="x")
            product_online = User(id=20, username="product", email="product@example.com", password_hash="x")
            raw_online = User(id=30, username="raw", email="raw@example.com", password_hash="x")
            unrelated = User(id=40, username="unrelated", email="unrelated@example.com", password_hash="x")
            db.add_all([requester, product_online, raw_online, unrelated])
            from app.models.models import Friendship
            db.add_all([
                Friendship(sender_id=10, receiver_id=20, status="accepted"),
                Friendship(sender_id=10, receiver_id=30, status="accepted"),
            ])
            db.add(PushDevice(
                user_id=20,
                device_id="product-online-device",
                enabled=True,
                app_state="background",
                last_seen_at=now,
            ))
            db.commit()

            original_ws_manager = chat.ws_manager
            chat.ws_manager = SimpleNamespace(
                get_online_user_ids=lambda: [30, 40],
                is_connected=lambda user_id: user_id in {30, 40},
            )
            try:
                result = chat.get_online_users_list(user=requester, db=db)
            finally:
                chat.ws_manager = original_ws_manager

            online_ids = {u["id"] for u in result["online_users"]}
            self.assertEqual(online_ids, {20, 30})
            self.assertEqual(result["total"], 2)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
