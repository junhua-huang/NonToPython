# Notification Delivery Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Android system notifications reliable when the app is backgrounded while preserving notification and device-state consistency.

**Architecture:** Persist app foreground/background state per `UserDevice`, use that state to decide JPush eligibility per device, and stop treating WebSocket connectivity as proof that the user can see in-app notifications. Keep notification rows as the source of truth and make JPush an optional delivery channel with observable failures.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, pytest/unittest, JPush REST API, Flutter/Dart, Riverpod lifecycle observer, jpush_flutter.

---

## File Structure

### Backend (`D:\NanTuPy`)

- Modify `app/models/models.py`: add `UserDevice.app_state`, `UserDevice.app_state_updated_at`, and expose them in `to_dict()`.
- Create `alembic/versions/2026_06_23_1200-add_user_device_app_state.py`: add/drop the two new columns.
- Modify `app/core/config.py`: add `JPUSH_ENABLE_THIRD_PARTY_CHANNEL` and `PUSH_FOREGROUND_ACTIVE_SECONDS`.
- Modify `app/routers/push.py`: add `PushDeviceStateRequest` and `POST /api/push/device-state`.
- Modify `app/services/push_service.py`: add foreground-expiry helpers, per-device JPush target filtering, optional third-party channel payload, and diagnostic logs.
- Modify `app/services/notification_service.py`: schedule JPush after WebSocket send and let `PushService` filter targets.
- Modify `app/routers/chat.py` and `app/routers/ws.py`: create message notifications for background/stale/offline receivers, not only disconnected receivers.
- Create `tests/test_push_device_state_contracts.py`: backend contract/unit tests for schema, route, eligibility, and payload behavior.
- Modify `tests/test_notification_service_unit.py`: assert JPush is no longer gated only by `ws_manager.is_connected`.

### Flutter (`D:\FlutterProject\nonto`)

- Modify `lib/services/push_service.dart`: add bounded registration retry, `reportAppState`, and `PushApiService.reportDeviceState`.
- Modify `lib/screens/home/home_screen.dart`: report foreground/background lifecycle state while preserving WebSocket reconnect.
- Modify `test/permissions_and_unread_regression_test.dart`: add static regression tests for device-state reporting and retry behavior.

---

## Task 1: Backend Contract Tests

**Files:**
- Create: `D:\NanTuPy\tests\test_push_device_state_contracts.py`
- Modify: `D:\NanTuPy\tests\test_notification_service_unit.py`

- [ ] **Step 1: Write failing backend tests**

Create `tests/test_push_device_state_contracts.py` with:

```python
import inspect
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from app.core.config import Config
from app.models.models import UserDevice
from app.routers import push as push_router
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
        self.assertIn('app_state: str', source)
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

    def test_payload_third_party_channel_is_config_gated(self):
        with patch.object(Config, "JPUSH_ENABLE_THIRD_PARTY_CHANNEL", False):
            payload = PushService.build_android_payload(
                reg_ids=["rid-1"],
                alert_title="标题",
                alert_content="正文",
                extras={"type": "comment"},
            )
        self.assertNotIn("third_party_channel", payload["options"])

        with patch.object(Config, "JPUSH_ENABLE_THIRD_PARTY_CHANNEL", True):
            payload = PushService.build_android_payload(
                reg_ids=["rid-1"],
                alert_title="标题",
                alert_content="正文",
                extras={"type": "comment"},
            )
        self.assertIn("third_party_channel", payload["options"])


if __name__ == "__main__":
    unittest.main()
```

Modify `tests/test_notification_service_unit.py` and add:

```python
    def test_push_new_notification_schedules_jpush_without_ws_connected_gate(self):
        source = inspect.getsource(NotificationService._push_new_notification)

        self.assertIn("PushService.schedule_send_to_user", source)
        self.assertNotIn("if not ws_manager.is_connected(user_id):", source)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```cmd
cd /d D:\NanTuPy
python -m pytest tests\test_push_device_state_contracts.py tests\test_notification_service_unit.py -q
```

Expected: failures for missing `app_state`, missing `/device-state`, missing `PushService.is_foreground_active`, missing `build_android_payload`, and old WebSocket gate.

---

## Task 2: Backend Schema, Config, and Device-State Route

**Files:**
- Modify: `D:\NanTuPy\app\models\models.py:1008-1030`
- Create: `D:\NanTuPy\alembic\versions\2026_06_23_1200-add_user_device_app_state.py`
- Modify: `D:\NanTuPy\app\core\config.py:71-76`
- Modify: `D:\NanTuPy\app\routers\push.py:7-89`
- Test: `D:\NanTuPy\tests\test_push_device_state_contracts.py`

- [ ] **Step 1: Add model fields**

In `app/models/models.py`, update `UserDevice` to include:

```python
    app_state = Column(String(20), default='unknown')  # foreground / background / unknown
    app_state_updated_at = Column(DateTime)
```

Place them after `last_active_at`. Update `to_dict()` to include:

```python
            'app_state': self.app_state,
            'app_state_updated_at': self.app_state_updated_at.isoformat() if self.app_state_updated_at else None,
```

- [ ] **Step 2: Add Alembic migration**

Create `alembic/versions/2026_06_23_1200-add_user_device_app_state.py`:

```python
"""add_user_device_app_state

Revision ID: add_user_device_app_state
Revises: 7f3c2a9b8d10
Create Date: 2026-06-23 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'add_user_device_app_state'
down_revision: Union[str, Sequence[str], None] = '7f3c2a9b8d10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user_devices', sa.Column('app_state', sa.String(length=20), nullable=True))
    op.add_column('user_devices', sa.Column('app_state_updated_at', sa.DateTime(), nullable=True))
    op.execute("UPDATE user_devices SET app_state = 'unknown' WHERE app_state IS NULL")


def downgrade() -> None:
    op.drop_column('user_devices', 'app_state_updated_at')
    op.drop_column('user_devices', 'app_state')
```

- [ ] **Step 3: Add config flags**

In `app/core/config.py`, under the JPush config block, add:

```python
    JPUSH_ENABLE_THIRD_PARTY_CHANNEL = os.environ.get('JPUSH_ENABLE_THIRD_PARTY_CHANNEL', 'false').lower() == 'true'
    PUSH_FOREGROUND_ACTIVE_SECONDS = int(os.environ.get('PUSH_FOREGROUND_ACTIVE_SECONDS', '120'))
```

- [ ] **Step 4: Add device-state request and endpoint**

In `app/routers/push.py`, update imports:

```python
import logging
from datetime import datetime
```

Add after `router = APIRouter()`:

```python
logger = logging.getLogger(__name__)

ALLOWED_APP_STATES = {"foreground", "background", "unknown"}
```

Add request model after `PushUnregisterRequest`:

```python
class PushDeviceStateRequest(BaseModel):
    registration_id: str = Field(..., min_length=1, max_length=255)
    app_state: str = Field(..., max_length=20)
```

Add route before `/unregister`:

```python
@router.post("/device-state")
def update_device_state(
    body: PushDeviceStateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """更新当前登录用户某个设备的前后台状态。"""
    app_state = (body.app_state or "unknown").strip().lower()
    if app_state not in ALLOWED_APP_STATES:
        raise HTTPException(status_code=400, detail="app_state must be foreground, background, or unknown")

    device = (
        db.query(UserDevice)
        .filter(
            UserDevice.registration_id == body.registration_id,
            UserDevice.user_id == user.id,
        )
        .first()
    )
    if not device:
        logger.warning("[PUSH STATE] device not found uid=%s state=%s", user.id, app_state)
        return {"success": True, "updated": False}

    now = datetime.utcnow()
    device.app_state = app_state
    device.app_state_updated_at = now
    device.last_active_at = now
    device.is_active = True
    db.commit()
    db.refresh(device)
    logger.info("[PUSH STATE] updated uid=%s device=%s state=%s", user.id, device.id, app_state)
    return {"success": True, "updated": True, "device": device.to_dict()}
```

- [ ] **Step 5: Update register route to initialize state**

In `register_device`, when updating an existing device, add:

```python
        existing.app_state = existing.app_state or "unknown"
        existing.app_state_updated_at = existing.app_state_updated_at or datetime.utcnow()
```

When creating `UserDevice`, add:

```python
            app_state="unknown",
            app_state_updated_at=datetime.utcnow(),
```

- [ ] **Step 6: Run backend contract tests**

Run:

```cmd
cd /d D:\NanTuPy
python -m pytest tests\test_push_device_state_contracts.py -q
```

Expected: route/model/config assertions pass; push service helper tests still fail until Task 3.

---

## Task 3: Backend PushService Eligibility and JPush Payload Fix

**Files:**
- Modify: `D:\NanTuPy\app\services\push_service.py:1-178`
- Test: `D:\NanTuPy\tests\test_push_device_state_contracts.py`

- [ ] **Step 1: Update imports**

Change imports in `app/services/push_service.py` to include datetime and iterable typing:

```python
from datetime import datetime
from typing import Iterable, Optional
```

- [ ] **Step 2: Add foreground and target-filter helpers inside `PushService`**

Add these class methods after `_enabled()`:

```python
    @classmethod
    def is_foreground_active(cls, device: UserDevice, now: Optional[datetime] = None) -> bool:
        """Return True only when a device recently reported foreground state."""
        if getattr(device, "app_state", None) != "foreground":
            return False
        updated_at = getattr(device, "app_state_updated_at", None)
        if updated_at is None:
            return False
        now = now or datetime.utcnow()
        age_seconds = (now - updated_at).total_seconds()
        return 0 <= age_seconds <= Config.PUSH_FOREGROUND_ACTIVE_SECONDS

    @classmethod
    def filter_push_registration_ids(cls, devices: Iterable[UserDevice]) -> list[str]:
        """Return active Android registration IDs that should receive system push."""
        targets: list[str] = []
        for device in devices:
            if not getattr(device, "is_active", False):
                continue
            if (getattr(device, "platform", "android") or "android").lower() != "android":
                continue
            registration_id = getattr(device, "registration_id", None)
            if not registration_id:
                continue
            if cls.is_foreground_active(device):
                continue
            targets.append(registration_id)
        return targets

    @classmethod
    def has_push_target(cls, db: Session, user_id: int) -> bool:
        devices = (
            db.query(UserDevice)
            .filter(UserDevice.user_id == user_id, UserDevice.is_active == True)
            .all()
        )
        return bool(cls.filter_push_registration_ids(devices))
```

- [ ] **Step 3: Replace active registration lookup**

Replace `get_active_registration_ids` with:

```python
    @classmethod
    def get_push_registration_ids(cls, db: Session, user_id: int) -> list[str]:
        """查询该用户应接收系统推送的 Android registration_id。"""
        devices = (
            db.query(UserDevice)
            .filter(UserDevice.user_id == user_id, UserDevice.is_active == True)
            .all()
        )
        return cls.filter_push_registration_ids(devices)
```

- [ ] **Step 4: Add payload builder**

Add before `send_to_user`:

```python
    @classmethod
    def build_android_payload(
        cls,
        reg_ids: list[str],
        alert_title: str,
        alert_content: str,
        extras: Optional[dict] = None,
    ) -> dict:
        android_alert = {"alert": alert_content}
        if alert_title:
            android_alert["title"] = alert_title

        payload = {
            "platform": "android",
            "audience": {"registration_id": reg_ids[:MAX_REGISTRATION_IDS_PER_CALL]},
            "notification": {
                "android": {**android_alert, "extras": extras or {}},
            },
            "options": {"time_to_live": 86400},
        }
        if Config.JPUSH_ENABLE_THIRD_PARTY_CHANNEL:
            payload["options"]["third_party_channel"] = {
                "huawei": {"distribution": "ospush", "importance": "NORMAL"},
                "xiaomi": {"distribution": "ospush", "channel_id": "nonto_message"},
                "oppo": {"distribution": "ospush", "channel_id": "nonto_message"},
                "vivo": {"distribution": "ospush", "classification": 1},
                "meizu": {"distribution": "ospush"},
            }
        return payload
```

- [ ] **Step 5: Use filtered targets and payload builder in `send_to_user`**

Inside `send_to_user`, replace:

```python
            reg_ids = cls.get_active_registration_ids(db, user_id)
            if not reg_ids:
                logger.debug(f"[JPUSH] no active device for user {user_id}")
                return False
```

with:

```python
            reg_ids = cls.get_push_registration_ids(db, user_id)
            if not reg_ids:
                logger.info("[JPUSH] no eligible push target uid=%s", user_id)
                return False
```

Replace the inline `android_alert` and `payload = ...` block with:

```python
        extras = extras or {}
        payload = cls.build_android_payload(
            reg_ids=reg_ids,
            alert_title=alert_title,
            alert_content=alert_content,
            extras=extras,
        )
```

- [ ] **Step 6: Improve JPush logs**

Change success log to:

```python
                logger.info(
                    "[JPUSH] pushed uid=%s targets=%s type=%s third_party=%s",
                    user_id,
                    len(reg_ids),
                    extras.get("type"),
                    Config.JPUSH_ENABLE_THIRD_PARTY_CHANNEL,
                )
```

Change failure log to:

```python
                logger.warning(
                    "[JPUSH] push failed uid=%s targets=%s status=%s body=%s",
                    user_id,
                    len(reg_ids),
                    resp.status_code,
                    resp.text[:300],
                )
```

- [ ] **Step 7: Run PushService tests**

Run:

```cmd
cd /d D:\NanTuPy
python -m pytest tests\test_push_device_state_contracts.py -q
```

Expected: all tests in `test_push_device_state_contracts.py` pass.

---

## Task 4: Backend Notification and Chat Routing

**Files:**
- Modify: `D:\NanTuPy\app\services\notification_service.py:20-82`
- Modify: `D:\NanTuPy\app\routers\chat.py:571-576`
- Modify: `D:\NanTuPy\app\routers\ws.py:555-564`
- Modify: `D:\NanTuPy\tests\test_notification_service_unit.py`

- [ ] **Step 1: Make `NotificationService` schedule JPush without WebSocket gate**

In `app/services/notification_service.py`, replace lines 58-76 with:

```python
                from app.services.push_service import PushService
                notif_type = notification_dict.get("notification_type") or "notification"
                related_id = notification_dict.get("related_id")
                related_type = notification_dict.get("related_type")
                alert_title = (notification_dict.get("title") or "南图")[:40]
                alert_content = (notification_dict.get("content") or "你有一条新通知")[:80]
                PushService.schedule_send_to_user(
                    user_id,
                    alert_title=alert_title,
                    alert_content=alert_content,
                    extras={
                        "type": notif_type,
                        "related_id": str(related_id) if related_id is not None else "",
                        "related_type": related_type or "",
                    },
                )
```

This keeps WS delivery but delegates foreground/background filtering to `PushService`.

- [ ] **Step 2: Add a chat helper in `app/routers/chat.py`**

Near the top-level helper section of `chat.py`, add:

```python
def _should_create_message_notification(db: Session, user_id: int) -> bool:
    """Create message notification when receiver is not recently foreground-active."""
    from app.services.push_service import PushService
    from app.ws_manager import ws_manager

    if not ws_manager.is_connected(user_id):
        return True
    return PushService.has_push_target(db, user_id)
```

If `Session` is not imported at the top of `chat.py`, add:

```python
from sqlalchemy.orm import Session
```

- [ ] **Step 3: Use helper in HTTP chat send path**

Replace `app/routers/chat.py:571-576` with:

```python
        # 后台/离线通知中心：后台用户即使 WS 尚未断开，也需要系统通知入口
        if _should_create_message_notification(db, receiver_id):
            from app.services.notification_service import NotificationService
            preview = content[:50] + '...' if len(content) > 50 else content
            NotificationService.notify_message(receiver_id, user.id, preview, conversation_id)
```

- [ ] **Step 4: Add equivalent helper in `app/routers/ws.py`**

Near the WebSocket send-message helpers, add:

```python
def _should_create_message_notification(db: Session, user_id: int) -> bool:
    """Create message notification when receiver is not recently foreground-active."""
    from app.services.push_service import PushService
    from app.ws_manager import ws_manager

    if not ws_manager.is_connected(user_id):
        return True
    return PushService.has_push_target(db, user_id)
```

If `Session` is not imported in `ws.py`, add:

```python
from sqlalchemy.orm import Session
```

- [ ] **Step 5: Use helper in WebSocket chat send path**

Replace `app/routers/ws.py:555-564` with:

```python
    notify_targets = [
        p for p in result["participant_ids"]
        if p != user_id and _should_create_message_notification(db, p)
    ]
    if notify_targets:
        from app.services.notification_service import NotificationService
        content = result["msg"].get("content") or ""
        for pid in notify_targets:
            NotificationService.notify_message(pid, user_id, content, result["conv_id"])
```

- [ ] **Step 6: Run notification tests**

Run:

```cmd
cd /d D:\NanTuPy
python -m pytest tests\test_notification_service_unit.py tests\test_push_device_state_contracts.py -q
```

Expected: all selected backend tests pass.

---

## Task 5: Flutter Static Regression Tests

**Files:**
- Modify: `D:\FlutterProject\nonto\test\permissions_and_unread_regression_test.dart`

- [ ] **Step 1: Add failing tests for device state reporting and retry**

Append to the existing `permission and unread synchronization regressions` group:

```dart
    test('push service reports app foreground and background state', () {
      final push = read('lib/services/push_service.dart');
      final home = read('lib/screens/home/home_screen.dart');

      expect(push, contains('Future<void> reportAppState(String appState)'));
      expect(push, contains("'/push/device-state'"));
      expect(push, contains("'registration_id': registrationId"));
      expect(push, contains("'app_state': appState"));
      expect(home, contains("PushService().reportAppState('foreground')"));
      expect(home, contains("PushService().reportAppState('background')"));
    });

    test('push registration upload has bounded retry state', () {
      final push = read('lib/services/push_service.dart');

      expect(push, contains('bool _registerRetryScheduled = false;'));
      expect(push, contains('static const List<Duration> _registerRetryDelays'));
      expect(push, contains('Future<bool> _uploadRegistrationId()'));
      expect(push, contains('_scheduleRegisterRetry()'));
      expect(push, contains('const Duration(seconds: 5)'));
      expect(push, contains('const Duration(seconds: 15)'));
      expect(push, contains('const Duration(seconds: 60)'));
    });
```

- [ ] **Step 2: Run Flutter regression test to verify it fails**

Run:

```cmd
cd /d D:\FlutterProject\nonto
flutter test test\permissions_and_unread_regression_test.dart
```

Expected: the two new tests fail because reporting and retry code are not implemented yet.

---

## Task 6: Flutter PushService Registration Retry and Device State Reporting

**Files:**
- Modify: `D:\FlutterProject\nonto\lib\services\push_service.dart:34-307`
- Test: `D:\FlutterProject\nonto\test\permissions_and_unread_regression_test.dart`

- [ ] **Step 1: Add retry state fields**

In `PushService`, after `_regIdCompleter`, add:

```dart
  bool _registerRetryScheduled = false;
  int _registerRetryAttempt = 0;
  static const List<Duration> _registerRetryDelays = [
    Duration(seconds: 5),
    Duration(seconds: 15),
    Duration(seconds: 60),
  ];
```

- [ ] **Step 2: Extract upload implementation**

Replace `registerAfterLogin()` with this method pair:

```dart
  Future<void> registerAfterLogin() async {
    if (!_supported || !_initialized) return;
    final ok = await _uploadRegistrationId();
    if (!ok) _scheduleRegisterRetry();
  }

  Future<bool> _uploadRegistrationId() async {
    final token = ApiClient.token;
    if (token == null || token.isEmpty) return false;
    final userId = LocalDbService().currentUserId;
    try {
      final rid = await getRegistrationId();
      if (rid == null || rid.isEmpty) {
        debugPrint('[Push] no registrationId, schedule retry');
        return false;
      }
      final ok = await PushApiService().register(
        registrationId: rid,
        platform: Platform.isIOS ? 'ios' : 'android',
        appVersion: AppConfig.appVersion,
      );
      debugPrint('[Push] register upload ok=$ok rid=$rid');
      if (!ok) return false;
      _registerRetryAttempt = 0;
      _registerRetryScheduled = false;
      if (userId != null) {
        final uid = int.tryParse(userId);
        if (uid != null) await setAlias(uid);
      }
      await reportAppState('foreground');
      return true;
    } catch (e) {
      debugPrint('[Push] registerAfterLogin error: $e');
      return false;
    }
  }
```

- [ ] **Step 3: Add bounded retry scheduler**

Add after `_uploadRegistrationId()`:

```dart
  void _scheduleRegisterRetry() {
    if (_registerRetryScheduled) return;
    if (_registerRetryAttempt >= _registerRetryDelays.length) {
      debugPrint('[Push] register retry exhausted');
      return;
    }
    final delay = _registerRetryDelays[_registerRetryAttempt++];
    _registerRetryScheduled = true;
    debugPrint('[Push] register retry scheduled in ${delay.inSeconds}s');
    Timer(delay, () async {
      _registerRetryScheduled = false;
      if (ApiClient.token == null || ApiClient.token!.isEmpty) return;
      final ok = await _uploadRegistrationId();
      if (!ok) _scheduleRegisterRetry();
    });
  }
```

`dart:async` is already imported at the top of `push_service.dart`.

- [ ] **Step 4: Add app-state report API in `PushService`**

Add before `unregisterOnLogout()`:

```dart
  Future<void> reportAppState(String appState) async {
    if (!_supported || !_initialized) return;
    final token = ApiClient.token;
    if (token == null || token.isEmpty) return;
    if (appState != 'foreground' && appState != 'background' && appState != 'unknown') {
      debugPrint('[Push] invalid app state: $appState');
      return;
    }
    try {
      final rid = await getRegistrationId();
      if (rid == null || rid.isEmpty) {
        debugPrint('[Push] no registrationId, skip state=$appState');
        return;
      }
      final ok = await PushApiService().reportDeviceState(
        registrationId: rid,
        appState: appState,
      );
      debugPrint('[Push] report app_state=$appState ok=$ok');
    } catch (e) {
      debugPrint('[Push] reportAppState error: $e');
    }
  }
```

- [ ] **Step 5: Reset retry state on logout**

In `unregisterOnLogout()`, before clearing `_registrationId`, add:

```dart
    _registerRetryScheduled = false;
    _registerRetryAttempt = 0;
```

- [ ] **Step 6: Add API method**

In `PushApiService`, add before `unregister()`:

```dart
  Future<bool> reportDeviceState({
    required String registrationId,
    required String appState,
  }) async {
    try {
      final resp = await ApiClient().post(
        '/push/device-state',
        data: {
          'registration_id': registrationId,
          'app_state': appState,
        },
      );
      return resp.success;
    } catch (e) {
      debugPrint('[PushApi] reportDeviceState error: $e');
      return false;
    }
  }
```

- [ ] **Step 7: Run Flutter test**

Run:

```cmd
cd /d D:\FlutterProject\nonto
flutter test test\permissions_and_unread_regression_test.dart
```

Expected: new PushService tests still fail until HomeScreen lifecycle calls are added in Task 7; existing tests continue to pass.

---

## Task 7: Flutter Lifecycle Reporting

**Files:**
- Modify: `D:\FlutterProject\nonto\lib\screens\home\home_screen.dart:61-79`
- Test: `D:\FlutterProject\nonto\test\permissions_and_unread_regression_test.dart`

- [ ] **Step 1: Report initial foreground after first frame**

Update the `addPostFrameCallback` in `initState()` to:

```dart
    WidgetsBinding.instance.addPostFrameCallback((_) {
      PushService().requestPermission();
      PushService().reportAppState('foreground');
    });
```

- [ ] **Step 2: Report lifecycle foreground/background**

Update `didChangeAppLifecycleState` to:

```dart
  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // 应用回到前台时，WS socket 多半已被系统挂起（看似 authenticated 实则僵死）。
    // 用 forceReconnect 强制重建，绕过 connect() 的「已连接」短路——
    // 这是「有网就不断 WS」的关键：回前台必须立即恢复实时通道。
    if (state == AppLifecycleState.resumed) {
      PushService().reportAppState('foreground');
      final ws = WebSocketService();
      if (ApiClient.token != null && ApiClient.token!.isNotEmpty) {
        debugPrint('[Home] app resumed, force reconnecting WebSocket');
        ws.forceReconnect();
      }
      return;
    }

    if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.inactive ||
        state == AppLifecycleState.detached ||
        state == AppLifecycleState.hidden) {
      PushService().reportAppState('background');
    }
  }
```

- [ ] **Step 3: Run Flutter regression test**

Run:

```cmd
cd /d D:\FlutterProject\nonto
flutter test test\permissions_and_unread_regression_test.dart
```

Expected: all tests in `permissions_and_unread_regression_test.dart` pass.

---

## Task 8: Full Verification

**Files:**
- Backend and Flutter files modified in previous tasks.

- [ ] **Step 1: Run focused backend tests**

Run:

```cmd
cd /d D:\NanTuPy
python -m pytest tests\test_push_device_state_contracts.py tests\test_notification_service_unit.py -q
```

Expected: all selected backend tests pass.

- [ ] **Step 2: Run focused Flutter tests**

Run:

```cmd
cd /d D:\FlutterProject\nonto
flutter test test\permissions_and_unread_regression_test.dart
```

Expected: all selected Flutter tests pass.

- [ ] **Step 3: Run backend import smoke test**

Run:

```cmd
cd /d D:\NanTuPy
python -c "from app.services.push_service import PushService; from app.routers.push import router; print('push imports ok')"
```

Expected output contains:

```text
push imports ok
```

- [ ] **Step 4: Run Flutter analyzer**

Run:

```cmd
cd /d D:\FlutterProject\nonto
flutter analyze
```

Expected: no new analyzer errors from `push_service.dart` or `home_screen.dart`.

- [ ] **Step 5: Manual Android verification**

Use one Android test device with notifications allowed:

1. Log in and confirm client log contains `[Push] register upload ok=true rid=...`.
2. Confirm backend `user_devices` row has `app_state='foreground'` after opening Home.
3. Send a notification while the app is foregrounded.
   - Expected: in-app WebSocket update appears.
   - Expected: no JPush notification for the foreground device.
4. Press Home to background the app and wait for backend state to show `background`.
5. Send another notification.
   - Expected: backend logs `[JPUSH] pushed uid=... targets=... third_party=False`.
   - Expected: Android system notification appears.
6. Tap the app.
   - Expected: notification list/unread count matches backend state.

---

## Self-Review Notes

- Spec coverage: schema, route, push filtering, JPush payload gating, notification routing, chat message routing, client lifecycle state, retry, and tests are all covered.
- Placeholder scan: no `TBD`, no deferred behavior, no unnamed test commands.
- Type consistency: backend uses `app_state`, `app_state_updated_at`, `registration_id`; client sends the same field names to `/push/device-state`.
