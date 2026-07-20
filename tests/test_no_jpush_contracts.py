import inspect
import pathlib
import unittest
from unittest.mock import Mock, patch

import app.main as main_module
from app.core.config import Config
from app.routers import chat, ws
from app.services.notification_service import NotificationService


class NoJPushContracts(unittest.TestCase):
    def test_main_does_not_register_legacy_jpush_routes(self):
        routes = [getattr(route, "path", "") for route in main_module.app.routes]
        self.assertNotIn("/api/push/register", routes)
        self.assertNotIn("/api/push/device-state", routes)
        self.assertNotIn("/api/push/unregister", routes)
        self.assertIn("/api/push/devices/register", routes)
        self.assertIn("/api/push/devices/unregister", routes)
        self.assertIn("/api/push/devices/status", routes)
        source = inspect.getsource(main_module)
        self.assertNotIn("jpush", source.lower())

    def test_config_has_no_jpush_settings(self):
        attrs = dir(Config)
        for name in attrs:
            self.assertNotIn("JPUSH", name.upper())

    def test_env_has_no_jpush_settings(self):
        env_path = pathlib.Path(__file__).resolve().parents[1] / ".env"
        if env_path.exists():
            env_text = env_path.read_text(encoding="utf-8")
            self.assertNotIn("JPUSH", env_text.upper())
            self.assertNotIn("JPush", env_text)

    def test_models_have_no_legacy_jpush_device_registration_model(self):
        models_path = pathlib.Path(__file__).resolve().parents[1] / "app" / "models" / "models.py"
        models_source = models_path.read_text(encoding="utf-8")
        self.assertNotIn("class UserDevice", models_source)
        self.assertNotIn("registration_id", models_source)
        self.assertIn("class PushDevice", models_source)
        self.assertIn("app_state_updated_at", models_source)
        self.assertNotIn("JPUSH", models_source.upper())

    def test_latest_migration_drops_legacy_push_device_table(self):
        migration_path = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions" / "2026_06_28_1200-drop_user_devices_push_table.py"
        migration_source = migration_path.read_text(encoding="utf-8")
        self.assertIn("op.drop_table('user_devices')", migration_source)

    def test_notification_service_schedules_current_delivery_paths(self):
        db = Mock()

        def refresh(notification):
            notification.id = 1

        db.refresh.side_effect = refresh
        with (
            patch.object(NotificationService, "_push_new_notification") as ws_push,
            patch(
                "app.services.notification_service.AliyunPushService.schedule_notification_push"
            ) as mobile_push,
        ):
            result = NotificationService.create_notification(
                user_id=1,
                notification_type="system",
                title="title",
                content="content",
                db=db,
            )

        payload = result.to_dict()
        ws_push.assert_called_once_with(1, payload)
        mobile_push.assert_called_once_with(1, payload)

    def test_chat_and_ws_do_not_treat_push_target_as_online_signal(self):
        chat_source = inspect.getsource(chat)
        ws_source = inspect.getsource(ws)
        for source in (chat_source, ws_source):
            self.assertNotIn("PushService", source)
            self.assertNotIn("has_push_target", source)
            self.assertNotIn("registration_id", source)
        self.assertIn("ws_manager.is_connected(user_id)", chat_source)
        self.assertIn("ws_manager.is_connected(user_id)", ws_source)

    def test_ws_still_accepts_access_token_query_param(self):
        source = inspect.getsource(ws.websocket_endpoint)
        self.assertIn('websocket.query_params.get("access_token", "")', source)
        self.assertIn("token_from_url", source)


if __name__ == "__main__":
    unittest.main()
