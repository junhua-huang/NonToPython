# App State Push Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure all notification-center records are created, while Aliyun mobile push is sent only when the target device is background/unknown or foreground state has expired.

**Architecture:** Extend `push_devices` with app lifecycle state fields and a `/api/push/devices/state` endpoint. Remove chat message notification suppression based on WebSocket connection. Keep NotificationService as the single notification entry point; let AliyunPushService decide per-device whether mobile push should be sent based on app_state freshness.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Flutter lifecycle observer, existing Aliyun push binding APIs.

---

## Files

- Backend model/migration: `app/models/models.py`, `alembic/versions/2026_07_03_0100-add_push_device_app_state.py`
- Backend endpoint: `app/routers/push.py`
- Backend push policy: `app/services/aliyun_push_service.py`
- Backend chat notification gating: `app/routers/ws.py`, `app/routers/chat.py`
- Backend tests: `tests/test_app_state_push_contracts.py`
- Flutter API client: `lib/services/api/push_device_service.dart`
- Flutter push service/lifecycle: `lib/services/aliyun_push_service.dart`, `lib/services/app_lifecycle_keepalive_service.dart`
- Flutter source contract test: `test/push_app_state_regression_test.dart`

## Tasks

1. Add backend tests proving:
   - `PushDevice` exposes `app_state`, `app_state_updated_at`, `last_foreground_at`, `last_background_at`.
   - Push router exposes `/api/push/devices/state` and validates `foreground/background/unknown`.
   - Chat routers no longer return `not ws_manager.is_connected(user_id)` for message notification creation.
   - AliyunPushService has foreground TTL skip and still sends stale foreground/background/unknown devices.
2. Add Alembic migration and model fields.
3. Add `/devices/state` endpoint and include app_state in register/status responses.
4. Update AliyunPushService per-device policy.
5. Update chat/ws notification target helpers to always create message notifications for recipients.
6. Add Flutter tests proving lifecycle state reporting methods exist.
7. Implement Flutter `PushDeviceService.updateDeviceState()` and `AliyunPushService.updateBackendDeviceState()`.
8. Call lifecycle state reporting from `AppLifecycleKeepAliveService`: resumed => foreground, background states => background.
9. Run backend and Flutter targeted tests, analyze, and rebuild APK/backend deploy package.
