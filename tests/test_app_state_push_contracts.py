import inspect
import pathlib
import unittest

from app.services.aliyun_push_service import AliyunPushService


ROOT = pathlib.Path(__file__).resolve().parents[1]


class AppStatePushContracts(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_push_device_model_tracks_app_lifecycle_state(self):
        source = self.read("app/models/models.py")

        self.assertIn("app_state", source)
        self.assertIn("app_state_updated_at", source)
        self.assertIn("last_foreground_at", source)
        self.assertIn("last_background_at", source)
        self.assertIn("'app_state'", source)

    def test_migration_adds_push_device_app_state_columns(self):
        migration = self.read("alembic/versions/2026_07_03_0100-add_push_device_app_state.py")

        self.assertIn("op.add_column('push_devices', sa.Column('app_state'", migration)
        self.assertIn("op.add_column('push_devices', sa.Column('app_state_updated_at'", migration)
        self.assertIn("op.add_column('push_devices', sa.Column('last_foreground_at'", migration)
        self.assertIn("op.add_column('push_devices', sa.Column('last_background_at'", migration)
        self.assertIn("op.drop_column('push_devices', 'app_state')", migration)

    def test_push_router_exposes_device_state_endpoint(self):
        source = self.read("app/routers/push.py")

        self.assertIn('@router.post("/devices/state")', source)
        self.assertIn('app_state', source)
        self.assertIn('foreground', source)
        self.assertIn('background', source)
        self.assertIn('inactive', source)
        self.assertIn('unknown', source)

    def test_push_router_emits_online_for_active_device_transition_and_defers_offline(self):
        source = self.read("app/routers/push.py")
        register_source = source.split('def register_device')[1].split('@router.post("/devices/unregister")')[0]
        state_source = source.split('def update_device_state')[1].split('@router.get("/devices/status")')[0]

        self.assertIn('def _capture_product_presence', source)
        self.assertIn('def _notify_product_presence_transition', source)
        self.assertIn('def _after_device_state_change', source)
        self.assertIn('was_product_online = _capture_product_presence(db, current_user.id)', register_source)
        self.assertIn('_after_device_state_change(current_user.id, was_product_online, app_state)', register_source)
        self.assertIn('was_product_online = _capture_product_presence(db, current_user.id)', state_source)
        self.assertIn('_after_device_state_change(current_user.id, was_product_online, app_state)', state_source)
        self.assertIn('presence_generation = ws_manager.bump_presence_generation(user_id)', source)
        self.assertIn('ws_manager.schedule_presence_online_from_product_state(user_id, presence_generation)', source)
        self.assertIn('ws_manager.schedule_offline_presence_check(user_id, BACKGROUND_ACTIVE_SECONDS, presence_generation)', source)
        self.assertIn('delay_seconds=0', source)
        self.assertNotIn('friend_offline', source)

    def test_chat_notifications_are_not_suppressed_by_websocket_online_state(self):
        ws_source = self.read("app/routers/ws.py")
        chat_source = self.read("app/routers/chat.py")

        self.assertNotIn("return not ws_manager.is_connected(user_id)", ws_source)
        self.assertNotIn("return not ws_manager.is_connected(user_id)", chat_source)
        self.assertIn("return True", ws_source)
        self.assertIn("return True", chat_source)

    def test_aliyun_push_does_not_suppress_delivery_from_stored_lifecycle_state(self):
        source = inspect.getsource(AliyunPushService)

        self.assertNotIn("FOREGROUND_PUSH_SKIP_SECONDS", source)
        self.assertNotIn("_should_skip_device_for_app_state", source)
        self.assertNotIn("APP_FOREGROUND_RECENT", source)

        router_source = self.read("app/routers/push.py")
        presence_source = self.read("app/services/presence_service.py")
        self.assertIn("device.app_state = app_state", router_source)
        self.assertIn("device.app_state_updated_at = now", router_source)
        self.assertIn("BACKGROUND_ACTIVE_SECONDS = 300", presence_source)
        self.assertIn("PushDevice.app_state.in_(ACTIVE_APP_STATES)", presence_source)


if __name__ == "__main__":
    unittest.main()
