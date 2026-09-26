# V7 Native Foreground WebSocket + Remove JPush Design

Date: 2026-06-28

## Goal

Replace the current JPush/vendor-push delivery path with an Android native foreground-service WebSocket path. When the app is in the foreground, Flutter WebSocket remains responsible for UI updates. When the app moves to the background and the visible "南图消息服务运行中" foreground service is active, Android native code maintains its own WebSocket and posts system notifications directly.

This deliberately removes vendor-push fallback. If the app process is fully killed, the user swipes it away and the service stops, or the phone reboots and the service is not restarted, the app does not receive messages until it is opened again.

## Non-goals

- Do not keep JPush, HMS, AGConnect, or other vendor-push fallback paths.
- Do not implement a silent/killed-process push mechanism.
- Do not redesign chat persistence, read-state, or message ordering beyond what Native WS needs.
- Do not make iOS background push work in this V7 scope. Removing `jpush_flutter` removes the current iOS JPush path too.

## Current State

Frontend project: `D:\FlutterProject\nonto`.

Relevant current frontend behavior:

- `pubspec.yaml` depends on `jpush_flutter` and `flutter_local_notifications`.
- `android/app/build.gradle.kts` contains AGConnect and JPush vendor plugin dependencies.
- `android/app/src/main/AndroidManifest.xml` contains JPush and vendor metadata.
- `MessageKeepAliveService.kt` currently only starts a foreground notification and does not maintain a native WebSocket.
- `ForegroundServiceManager` currently starts the native service but still relies on Flutter `WebSocketService` for the actual socket.
- `PushService` wraps JPush registration, alias, app-state reporting, and `/api/push/*` HTTP calls.
- Push diagnostics currently display JPush/Huawei fields.

Backend project: `D:\NanTuPy`.

Relevant current backend behavior:

- `/ws` accepts `access_token` in the URL query string.
- `ws_manager.is_connected(user_id)` treats any active device connection as online.
- `/api/push/*` stores JPush `registration_id` records in `UserDevice`.
- `app.services.push_service.PushService` sends JPush REST API requests.
- Notification creation sends WebSocket `new_notification` and then calls JPush.
- Chat notification target logic can treat a registered push target as a reason to create notification-center rows even if the user is WebSocket-online.

## Delivery Semantics After V7

```text
App foreground:
  Flutter WebSocket receives messages and updates UI.

App background, foreground service alive:
  Native Android foreground service keeps an OkHttp WebSocket connected.
  Backend sees the user online through that native WebSocket.
  Native Android code receives `new_message` / `new_notification` frames and posts system notifications.

App killed, service stopped, or phone rebooted without service running:
  No vendor push fallback exists.
  Backend sees the user offline.
  Messages and notification-center rows remain in the database and are fetched/synced next time the app opens.
```

## Android Native Architecture

### Service Responsibilities

Upgrade `MessageKeepAliveService` into the owner of background messaging on Android.

The service will:

1. Start as a foreground service with the existing persistent notification:
   - title: `南图消息服务运行中`
   - text: `正在保持消息连接`
   - channel: `nonto_keepalive`
2. Read the latest token and WebSocket URL from native-accessible storage.
3. Connect to the backend using OkHttp WebSocket:
   - URL shape: `ws://.../ws?access_token=<token>` or `wss://.../ws?access_token=<token>`
4. Track connection state.
5. Reconnect with bounded backoff after abnormal close or network errors.
6. Parse backend envelopes and handle at least:
   - `type: "message"`, `payload.event: "new_message"`
   - `type: "message"`, `payload.event: "new_notification"`
   - `type: "message"`, `payload.event: "session_list"` by ignoring or recording diagnostics
   - `type: "auth_result"` by recording success/failure diagnostics
7. Post Android notifications for background messages and interactions.
8. Stop and cleanly close the native WebSocket when Flutter returns to foreground or user logs out.

### Native Storage Contract

Flutter writes these values before starting the service:

- `native_ws_url`: canonical WebSocket URL from `AppConfig.wsUrl`, converted to `ws://` or `wss://`.
- `native_access_token`: current access token.
- `native_current_user_id`: current user id if available.
- `native_keepalive_enabled`: user setting for background service.

The native service treats a missing or blank token/URL as a non-retryable start failure and stops itself after updating diagnostics.

### Native Notification Channels

Use two channels:

- `nonto_keepalive`: low-importance persistent foreground-service notification.
- `nonto_message`: normal/default-importance user-visible message and interaction notifications.

The native service should create both channels. Existing Flutter local notifications may keep using `nonto_message` while foreground/background transition is being refactored.

### Notification Routing

Native notifications should open `MainActivity` with extras:

- message notification:
  - `nonto_route`: chat room route when `conversation_id` is present, otherwise chat list route
  - `conversation_id`: string form of the conversation id
- interaction notification:
  - `nonto_route`: notifications route by default
  - `notification_type`, `related_id`, `related_type` when present

`MainActivity` should forward initial notification extras to Flutter through an existing or new method channel. If implementing route-forwarding takes too long, the first V7 can open the app home and let Flutter sync; this is acceptable only for the first working Android build, not the final V7 acceptance target.

## Flutter Architecture

### Lifecycle Ownership

Replace the old "native service keeps process alive, Flutter WS still owns background socket" model.

Foreground behavior:

1. Stop `MessageKeepAliveService`.
2. Mark Flutter WebSocket foreground.
3. Force reconnect Flutter `WebSocketService`.
4. Force refresh/sync conversations and current chat data so messages received by Native WS are visible and duplicates are reconciled by server ids/client ids.

Background behavior:

1. Persist `native_ws_url`, `native_access_token`, and user id.
2. Mark Flutter WebSocket background.
3. Start `MessageKeepAliveService` if the user enabled the foreground keepalive setting and a token exists.
4. Do not depend on Flutter WebSocket for background delivery.

Logout behavior:

1. Stop native service.
2. Clear native token/ws diagnostic state.
3. Disconnect Flutter WebSocket.

### PushService Removal

Remove JPush-specific `PushService` behavior entirely:

- No SDK setup.
- No registration id fetch.
- No alias.
- No `/api/push/register`, `/api/push/device-state`, or `/api/push/unregister` calls.
- No app-state reporting for JPush.

If call sites need a facade during migration, replace `PushService` with a small `BackgroundMessageService` or remove the calls directly. The replacement must not mention JPush, registrationId, HMS, or vendor channels.

### Diagnostics

Rename push diagnostics conceptually to background message diagnostics. It should report:

- foreground service enabled setting
- native service running state if available
- native WS URL host/path, without token
- native WS connected/authenticated state
- last native WS event and timestamp
- last native notification attempt/success/error
- Android notification permission and channel importance
- Flutter WS foreground/connected state
- Flutter local notification state while it still exists

It must not show JPush registration id, app key, channel, HMS availability, Huawei app id, or vendor-push fields.

## Backend Architecture

### Remove JPush API and Service

Remove backend JPush code paths:

- Do not include `app.routers.push` in `app.main`.
- Remove or stop using `app.services.push_service.PushService`.
- Remove JPush config fields from `Config` and production config if no longer used.
- Remove tests that assert JPush payload/vendor behavior or rewrite them as Native WS/no-JPush contract tests.

Existing database `UserDevice` rows can remain for compatibility/migration, but new V7 runtime must not require registration ids. Dropping DB columns is not part of this V7 unless a migration system and rollback plan already exist.

### Notification Creation

Notification-center records remain the durable source for likes, comments, friend requests, mentions, and other interaction notifications.

After V7:

1. Create notification row as before.
2. Send `new_notification` via `ws_manager.send_with_seq`.
3. Do not call JPush.
4. If the user is offline, the notification row remains and will be fetched later.

### Chat Message Delivery

Chat message persistence remains unchanged. When a message is saved:

1. Broadcast `new_message` through `ws_manager.send_with_seq` to active connections.
2. If the receiver has Native WS connected in the background, the backend sees them online and the native service receives the frame.
3. If the receiver is fully offline, the message stays in the database and WS sequence log. The user syncs on next open.

The helper that decides whether to create message notification rows must no longer depend on JPush target presence. It should be based on the desired product behavior:

- Create notification-center rows when the receiver is offline, or
- Create them for all non-self messages if notification center should mirror chat alerts.

For V7, choose the minimal behavior consistent with current semantics: offline receivers get notification-center rows; online receivers rely on WebSocket/local state.

### WebSocket Compatibility

The existing URL-query token path is sufficient for Native WS. The native service does not need to send a separate auth frame for V7.

Backend should continue to accept:

```text
/ws?access_token=<jwt>
```

No server-side special case is required for native Android connections. They count as normal user device connections in `ws_manager`.

## Removal Inventory

### Frontend Remove or Replace

- `pubspec.yaml`: remove `jpush_flutter`.
- `pubspec.lock`: update through `flutter pub get`.
- `lib/services/push_service.dart`: delete or replace with non-JPush background messaging facade.
- `lib/main.dart`: remove JPush init.
- `lib/providers/auth_notifier.dart`: remove registration id upload/unregister calls.
- `lib/screens/home/home_screen.dart`: remove JPush permission/report calls; keep Android notification permission handling through local/native notification path.
- `lib/services/app_lifecycle_keepalive_service.dart`: remove JPush app-state reporting; start/stop native service instead.
- `lib/screens/profile/push_diagnostics_screen.dart`: rename/reword to background messaging diagnostics or remove JPush fields.
- `lib/services/push_diagnostics_service.dart`: remove JPush/Huawei fields.
- Android manifest: remove JPush/vendor metadata and unused vendor permissions if not needed by native service.
- Android Gradle: remove AGConnect and JPush vendor dependencies/plugins/repositories that are no longer needed.
- Android `agconnect-services.json`: remove from project if it was only for Huawei push.

### Backend Remove or Replace

- `app/main.py`: remove push router registration.
- `app/routers/push.py`: delete or leave unregistered; preferred final state is deletion.
- `app/services/push_service.py`: delete or leave unused; preferred final state is deletion.
- `app/core/config.py` and `config_production.py`: remove JPush config fields.
- `app/services/notification_service.py`: remove `PushService.send_to_user` call.
- `app/routers/chat.py` and `app/routers/ws.py`: remove `PushService.has_push_target` dependency.
- Tests/docs that describe JPush vendor delivery: update to Native WS/no-vendor-push behavior.

## Error Handling

Native service errors are recorded in diagnostics and handled as follows:

- Missing token or URL: stop service; no reconnect.
- Authentication failure: close socket, update diagnostics, stop service so Flutter can force login on next foreground.
- Network error: reconnect with backoff while the service remains active.
- Server close: reconnect unless the close code indicates authentication failure.
- Notification permission denied: keep WS connected, record notification failure, do not crash.
- Malformed payload: record parse error and continue receiving future messages.

## Testing Strategy

### Frontend Static/Unit Tests

- Assert `pubspec.yaml` no longer contains `jpush_flutter`.
- Assert Android Gradle no longer contains AGConnect/JPush vendor dependencies or AGConnect plugin application.
- Assert Android manifest no longer contains `JPUSH_`, Xiaomi/OPPO/vivo/Meizu push metadata, or Huawei push metadata.
- Assert lifecycle code starts native service on background and stops it on foreground.
- Assert diagnostics output contains Native WS/background service fields and no JPush/Huawei fields.

### Android Build Verification

- Run Flutter/Gradle build for Android debug or release variant.
- Confirm Kotlin compiles with OkHttp dependency and Android SDK target 36.
- Confirm no unresolved JPush imports remain.

### Backend Tests

- Tests assert `app.main` no longer includes the push router.
- Tests assert `notification_service` no longer imports/calls `PushService`.
- Tests assert chat/ws notification-target logic no longer depends on `PushService.has_push_target`.
- Existing created_at/WebSocket contract tests should still pass.

### Manual Device Acceptance

1. Install V7 APK.
2. Login.
3. With app foreground, send message from another account: UI updates through Flutter WS.
4. Press Home; confirm persistent notification says `南图消息服务运行中`.
5. Check backend logs/online endpoint: user remains online through Native WS.
6. Send message from another account: Android system notification appears.
7. Tap notification: app opens to the relevant chat or syncs and shows the message.
8. Return to foreground: Flutter WS reconnects and message list contains one copy.
9. Stop/kill app/service: backend eventually shows offline; new messages do not notify until app opens.
10. Open app: messages sync from backend.

## Rollout and Risk

This is a deliberate tradeoff: reliability while the foreground service is alive, simpler architecture, and no JPush/vendor complexity. The cost is no killed-process/reboot push delivery.

Main risks:

- Android OEMs may still stop the foreground service under aggressive battery policies.
- Token refresh currently lives in Flutter; native service only has the last saved token. If the token expires while backgrounded, Native WS authentication can fail until the app foregrounds and refreshes.
- Native notification deep-link routing needs careful route handoff to Flutter.
- Removing `jpush_flutter` may affect iOS push behavior by design.

Mitigations:

- Keep the foreground service visible and user-controlled.
- Record native WS auth failures clearly in diagnostics.
- Force Flutter sync on foreground to recover missed background notifications.
- Keep implementation incremental: remove JPush, get native connect/auth stable, then notification routing.

## Acceptance Criteria

V7 is accepted when:

- The app and backend build/test without JPush, HMS, AGConnect, vendor push metadata, or registration id runtime calls.
- Android foreground service maintains an authenticated Native WebSocket in background.
- Backend online status reflects the Native WS connection.
- Background `new_message` frames produce Android system notifications while the service is alive.
- Foreground Flutter WS still updates chat UI in real time.
- Returning foreground stops or downgrades Native WS and forces Flutter sync.
- Killing/stopping the app results in no notification delivery, and next app open syncs missed data.
