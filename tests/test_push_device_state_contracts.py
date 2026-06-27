import inspect
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from app.core.config import Config
from app.models.models import UserDevice
from app.routers import push as push_router
from app.routers import ws as ws_router
from app.services.push_service import PushService


class FakeDevice:
    def __init__(self, registration_id="rid", app_state="unknown", seconds_ago=None, platform="android", active=True):
        self.registration_id = registration_id
        self.app_state = app_state
        self.platform = platform
        self.is_active = active
        if seconds_ago is None:
            self.app_state_updated_at = None
        else:
            self.app_state_updated_at = datetime.utcnow() - timedelta(seconds=seconds_ago)


class PushDeviceStateContractTests(unittest.TestCase):
    def test_user_device_model_exposes_persisted_app_state(self):
        attrs = set(UserDevice.__table__.columns.keys())
        self.assertIn("app_state", attrs)
        self.assertIn("app_state_updated_at", attrs)

        source = inspect.getsource(UserDevice.to_dict)
        self.assertIn("app_state", source)
        self.assertIn("app_state_updated_at", source)

    def test_push_router_defines_device_state_endpoint(self):
        source = inspect.getsource(push_router)
        self.assertIn("class PushDeviceStateRequest", source)
        self.assertIn("app_state: str", source)
        self.assertIn('@router.post("/device-state")', source)
        self.assertIn("registration_id", source)
        self.assertIn("foreground", source)
        self.assertIn("background", source)
        self.assertIn("unknown", source)

    def test_foreground_active_requires_recent_foreground_state(self):
        recent = FakeDevice(app_state="foreground", seconds_ago=30)
        stale = FakeDevice(app_state="foreground", seconds_ago=999)
        background = FakeDevice(app_state="background", seconds_ago=5)
        unknown = FakeDevice(app_state="unknown", seconds_ago=5)
        missing_timestamp = FakeDevice(app_state="foreground", seconds_ago=None)

        with patch.object(Config, "PUSH_FOREGROUND_ACTIVE_SECONDS", 120):
            self.assertTrue(PushService.is_foreground_active(recent))
            self.assertFalse(PushService.is_foreground_active(stale))
            self.assertFalse(PushService.is_foreground_active(background))
            self.assertFalse(PushService.is_foreground_active(unknown))
            self.assertFalse(PushService.is_foreground_active(missing_timestamp))

    def test_push_target_filter_skips_recent_foreground_android_only(self):
        devices = [
            FakeDevice("foreground-rid", app_state="foreground", seconds_ago=10),
            FakeDevice("background-rid", app_state="background", seconds_ago=10),
            FakeDevice("stale-rid", app_state="foreground", seconds_ago=999),
            FakeDevice("unknown-rid", app_state="unknown", seconds_ago=None),
            FakeDevice("ios-rid", app_state="background", seconds_ago=10, platform="ios"),
            FakeDevice("inactive-rid", app_state="background", seconds_ago=10, active=False),
        ]

        with patch.object(Config, "PUSH_FOREGROUND_ACTIVE_SECONDS", 120):
            targets = PushService.filter_push_registration_ids(devices)

        self.assertEqual(targets, ["background-rid", "stale-rid", "unknown-rid"])

    def test_ws_message_notification_target_selection_has_db_scope(self):
        source = inspect.getsource(ws_router._handle_send_message)

        self.assertIn("notify_targets", source)
        self.assertNotIn("_should_create_message_notification(db, p)", source)
        self.assertIn("_get_message_notification_targets", source)

    def test_config_defaults_enable_android_vendor_channels(self):
        source = inspect.getsource(Config)

        self.assertIn("JPUSH_ENABLE_THIRD_PARTY_CHANNEL", source)
        self.assertIn("'true'", source)
        self.assertIn("huawei,xiaomi,oppo,vivo,meizu", source)

    def test_payload_third_party_channel_is_config_gated(self):
        with patch.object(Config, "JPUSH_ENABLE_THIRD_PARTY_CHANNEL", False):
            payload = PushService.build_android_payload(
                reg_ids=["rid-1"],
                alert_title="标题",
                alert_content="正文",
                extras={"type": "comment"},
            )
        self.assertNotIn("third_party_channel", payload["options"])

        with patch.object(Config, "JPUSH_ENABLE_THIRD_PARTY_CHANNEL", True), \
                patch.object(Config, "JPUSH_THIRD_PARTY_CHANNELS", {"oppo", "vivo"}):
            payload = PushService.build_android_payload(
                reg_ids=["rid-1"],
                alert_title="标题",
                alert_content="正文",
                extras={"type": "comment"},
            )
        channels = payload["options"]["third_party_channel"]
        self.assertEqual(set(channels.keys()), {"oppo", "vivo"})
        self.assertNotIn("xiaomi", channels)
        self.assertEqual(channels["oppo"]["channel_id"], "nonto_message")
        self.assertEqual(channels["vivo"]["classification"], 1)

    def test_jpush_send_logs_include_third_party_channel_diagnostics(self):
        source = inspect.getsource(PushService.send_to_user)

        self.assertIn('channels=%s', source)
        self.assertIn('Config.JPUSH_THIRD_PARTY_CHANNELS', source)
        self.assertIn('third_party=%s', source)
        self.assertIn('resp.text[:300]', source)


if __name__ == "__main__":
    unittest.main()
