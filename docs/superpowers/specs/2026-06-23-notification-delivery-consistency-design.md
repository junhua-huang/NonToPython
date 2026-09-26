# Notification Delivery Consistency Design

## Context

Users report that Android devices do not show system notifications while the app is in the background, but notifications become visible after opening the app. Investigation found two concrete issues:

1. Backend push routing treats `ws_manager.is_connected(user_id)` as proof that a user is actively available in the app. On mobile, a backgrounded app may leave WebSocket state alive on the server while Dart processing is paused, so the backend skips JPush and only sends WebSocket frames.
2. JPush requests have failed with HTTP 400 because the backend always sends `options.third_party_channel.*.distribution = ospush`, while the current JPush account does not have permission for that option.

The fix covers the backend (`D:\NanTuPy`) and Android Flutter client (`D:\FlutterProject\nonto`).

## Goals

- Preserve notification data consistency: creation of notification records must not depend on WebSocket or JPush delivery.
- Use persisted device foreground/background state, not only in-memory WebSocket state, to decide whether Android system push is needed.
- Ensure JPush can send basic Android notifications without being rejected by unauthorized `ospush` settings.
- Make registrationId upload and app-state reporting retry-tolerant on the client.
- Add enough tests and logs to diagnose why a notification was or was not sent.

## Non-goals

- Build a durable push delivery table or retry queue.
- Add iOS/APNs payload support.
- Add a new Flutter permission package.
- Redesign the existing WebSocket sync protocol.

## Data Consistency Principles

1. The database notification row is the source of truth. WebSocket and JPush are delivery channels only.
2. A JPush failure never rolls back the notification row.
3. Device state is persisted on `user_devices`, keyed by the authenticated user and `registration_id`.
4. Foreground state expires. Only a recent `foreground` heartbeat means the user is actively in the app.
5. Multi-device delivery is evaluated per device: foreground Android devices are skipped for JPush, background/unknown/stale Android devices are eligible.
6. Idempotent updates are required. Re-registering or re-reporting the same state updates the same device row and timestamp.

## Backend Design

### Schema

Extend `UserDevice` with:

- `app_state`: string, default `unknown`, values `foreground`, `background`, `unknown`.
- `app_state_updated_at`: nullable datetime.

Existing fields keep their meaning:

- `last_active_at`: last time the device registered or reported state.
- `is_active`: whether this device token may be used for push.

### API

Add `POST /api/push/device-state`.

Request:

```json
{
  "registration_id": "jpush-registration-id",
  "app_state": "foreground"
}
```

Allowed states: `foreground`, `background`, `unknown`.

Behavior:

- Requires authentication.
- Finds `UserDevice` by `user.id + registration_id`.
- If found, updates `app_state`, `app_state_updated_at`, `last_active_at`, and `is_active = true`.
- If not found, returns `success: true` with `updated: false` and logs a warning. This keeps client lifecycle calls from breaking normal app use before token registration completes.

### PushService

Add helper methods:

- `is_foreground_active(device, now=None)`: true only when `app_state == 'foreground'` and `app_state_updated_at` is within the configured active window.
- `get_push_registration_ids(db, user_id)`: returns active Android registration IDs that should receive system push. It excludes recent foreground devices and includes background, unknown, stale, and null-state devices.

Add config:

- `JPUSH_ENABLE_THIRD_PARTY_CHANNEL=false` by default.
- `PUSH_FOREGROUND_ACTIVE_SECONDS=120` by default.

Change JPush payload:

- Always include `options.time_to_live`.
- Include `options.third_party_channel` only when `JPUSH_ENABLE_THIRD_PARTY_CHANNEL` is true.

### NotificationService

Change notification push routing:

- Always send WebSocket `new_notification` as today.
- After WebSocket send, ask `PushService` to schedule system push.
- `PushService` itself filters devices based on push preference, active registration IDs, and foreground state.
- If no eligible devices exist, log why and skip JPush.

This removes the incorrect assumption that WebSocket connectivity means no system notification is needed.

### Chat Message Notifications

Update both chat send paths:

- Continue sending WebSocket `new_message`.
- Create a notification only when system push is needed or the receiver is offline/stale, not only when `ws_manager.is_connected(receiver_id)` is false.
- Use the same device-state-based eligibility helper so background users get a message notification even if the WebSocket is still connected.

## Android Client Design

### Device State Reporting

Extend `PushApiService` with:

```dart
Future<bool> reportDeviceState({
  required String registrationId,
  required String appState,
})
```

Add `PushService.reportAppState(String appState)`:

- Requires initialized JPush, a non-empty API token, and a registrationId.
- Uploads `foreground`, `background`, or `unknown` to `/push/device-state`.
- Logs failures and returns without throwing.

Call from `HomeScreen.didChangeAppLifecycleState`:

- `resumed`: report `foreground`, request permission if appropriate, keep force WebSocket reconnect.
- `paused`, `inactive`, `detached`, `hidden`: report `background`.

### Registration Retry

Change `registerAfterLogin()` so that failures are retried with bounded delays:

- Attempt immediately.
- Retry after 5 seconds, 15 seconds, and 60 seconds.
- Stop once registration succeeds or the API token disappears.
- Avoid concurrent retry loops.

On successful registration, immediately report `foreground` when the app is active.

### Permission Observability

Keep using JPush `requestRequiredPermission()` to avoid adding a new dependency. Improve logs so Android 13+ permission issues are diagnosable. Do not repeatedly show blind permission prompts.

## Testing Strategy

### Backend

- Unit/static tests ensure `UserDevice` exposes the new fields.
- Unit tests verify `PushService.is_foreground_active` and device eligibility behavior.
- Unit/static tests verify JPush payload does not include `third_party_channel` unless the config flag is enabled.
- API/static tests verify `/device-state` route and request model exist.
- Notification routing tests verify `NotificationService` no longer gates JPush on `ws_manager.is_connected`.

### Flutter

- Regression tests verify `PushApiService.reportDeviceState` posts to `/push/device-state` with `registration_id` and `app_state`.
- Regression tests verify `HomeScreen.didChangeAppLifecycleState` reports `foreground` on resume and `background` on background lifecycle states.
- Regression tests verify registration retry state exists and failed registration schedules retry without introducing `permission_handler`.

## Rollout Notes

1. Apply backend migration before deploying backend code.
2. Deploy backend with `JPUSH_ENABLE_THIRD_PARTY_CHANNEL=false` unless the JPush account has verified `ospush` permission.
3. Release Android client after backend route is deployed. The server handles missing device-state reports as `unknown`, which is push-eligible, so backend can deploy first safely.
4. Verify with a real Android device: foreground should update in-app only; background should receive system notification and still show the same notification/unread state when opened.
