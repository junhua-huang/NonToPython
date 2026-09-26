# Aliyun Push Backend Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect all existing backend notifications to Alibaba Cloud Mobile Push and bind Flutter Android Aliyun DeviceIds to authenticated users.

**Architecture:** Add `push_devices` and `push_logs` backend tables, a `/api/push/devices` router for client binding, and an `AliyunPushService` that sends Android notification pushes by device ID. Hook mobile push at the existing unified `NotificationService.create_notification()` point so every station notification can trigger a mobile push while respecting `notify_push` and block rules. On Flutter, register the Aliyun DeviceId after push SDK init/login/session restore and unregister on logout.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Python stdlib HMAC/HTTP, Flutter/Dart, Dio, existing `aliyun_push_flutter` diagnostics.

---

### Task 1: Backend models, migration, and contracts

**Files:**
- Modify: `D:/NanTuPy/app/models/models.py`
- Create: `D:/NanTuPy/alembic/versions/2026_07_02_0200-add_aliyun_push_devices.py`
- Modify: `D:/NanTuPy/tests/test_no_jpush_contracts.py`
- Create: `D:/NanTuPy/tests/test_aliyun_push_contracts.py`

Steps:
- Add `PushDevice` model with user_id/platform/provider/device_id/manufacturer/model/app_version/enabled/last_seen_at/created_at/updated_at.
- Add `PushLog` model with user_id/device_id/notification_id/notification_type/title/status/request_id/message_id/error_code/error_message/created_at.
- Add Alembic migration creating `push_devices` and `push_logs` plus indexes/unique device_id.
- Update legacy no-JPush tests so they still reject JPush/registration_id but allow new Aliyun `PushDevice`.
- Add source contract test asserting new models, migration, and no secrets are hard-coded.

### Task 2: Backend push router and Aliyun service

**Files:**
- Create: `D:/NanTuPy/app/routers/push.py`
- Create: `D:/NanTuPy/app/services/aliyun_push_service.py`
- Modify: `D:/NanTuPy/app/main.py`
- Modify: `D:/NanTuPy/app/core/config.py`
- Modify: `D:/NanTuPy/tests/test_aliyun_push_contracts.py`

Steps:
- Add config env names: `ALIYUN_ACCESS_KEY_ID`, `ALIYUN_ACCESS_KEY_SECRET`, `ALIYUN_PUSH_APP_KEY_ANDROID`, `ALIYUN_PUSH_ANDROID_ACTIVITY`, `ALIYUN_PUSH_ANDROID_CHANNEL_ID`, endpoint.
- Implement `AliyunPushService` with request signing, push notice payload building, disabled-on-missing-config behavior, and log creation.
- Add authenticated endpoints:
  - `POST /api/push/devices/register`
  - `POST /api/push/devices/unregister`
  - `GET /api/push/devices/status`
- Include router in `app/main.py` as `/api/push`.
- Tests assert routes/config/service payload/signing markers.

### Task 3: Hook all notifications to mobile push

**Files:**
- Modify: `D:/NanTuPy/app/services/notification_service.py`
- Modify: `D:/NanTuPy/tests/test_aliyun_push_contracts.py`

Steps:
- After notification commit and WebSocket schedule in `create_notification`, schedule mobile push.
- Reuse existing block and notify_push behavior.
- Send notification title/content/type/related metadata through Aliyun push service.
- Ensure failures are logged/skipped and never rollback notification creation.

### Task 4: Flutter backend device binding

**Files:**
- Create: `D:/FlutterProject/nonto/lib/services/api/push_device_service.dart`
- Modify: `D:/FlutterProject/nonto/lib/services/aliyun_push_service.dart`
- Modify: `D:/FlutterProject/nonto/lib/providers/auth_notifier.dart`
- Modify: `D:/FlutterProject/nonto/lib/services/push_diagnostics_service.dart`
- Modify: `D:/FlutterProject/nonto/lib/screens/profile/push_diagnostics_screen.dart`
- Create/modify Flutter tests for source contracts.

Steps:
- Add API client wrapper for register/unregister/status.
- Extend `AliyunPushService` with backend bind diagnostics and `registerBackendDevice()` / `unregisterBackendDevice()`.
- Call register after Aliyun init when token exists, after login/register/session validation, and call unregister before logout token clear.
- Add diagnostics rows for backend bind status/time/error.

### Task 5: Verification and APK

Commands:
- Backend targeted pytest for push contracts.
- Flutter targeted tests/analyze for push changes.
- Build arm64 APK and copy to desktop as `nonto测试v9.0.apk` if implementation succeeds.

Self-review:
- Scope covers all notifications via unified notification service.
- Secrets remain environment-only.
- Push failures do not block core notification creation.
- Client binding is idempotent and safe across login/logout.
