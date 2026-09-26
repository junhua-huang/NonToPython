# Realtime Community Presence Design

## Background

Community chat currently shows online member count from the members API. The backend now serializes `CommunityMember.user.is_online` from `ws_manager.is_connected(user_id)`, so the count is correct when the member list is fetched. It does not update after a member comes online or goes offline because there is no community presence WebSocket event and the Flutter community chat screen only subscribes to chat messages.

## Goal

Make the community chat header online count update in real time using app-level WebSocket presence. A user is considered online when they have at least one active WebSocket connection, even if they are not currently viewing that specific community chat.

## Non-goals

- Do not redefine online count as "currently inside this chat page".
- Do not add polling for member lists.
- Do not add a second UI counter for "currently viewing chat".
- Do not change notification delivery or message delivery semantics.

## Event Contract

Backend emits a sequenced WebSocket event:

```json
{
  "event": "community_member_presence",
  "data": {
    "community_id": 123,
    "conversation_id": 456,
    "user_id": 789,
    "is_online": false
  }
}
```

Fields:

- `community_id`: community whose membership makes this event relevant.
- `conversation_id`: community conversation id when available.
- `user_id`: member whose app-level online state changed.
- `is_online`: `true` when the user transitions from offline to online; `false` when the user's last WebSocket connection disconnects.

The event is sent only to online users who are active members of the affected community. Recipients may include the changed user on online events if convenient, but the frontend must treat the event idempotently.

## Backend Design

Add community presence broadcast logic near the existing WebSocket presence flow:

1. On authenticated connect, after `ws_manager.connect(user_id, websocket)` registers the connection, detect whether this is the user's first active connection.
2. If it is the first active connection, broadcast `community_member_presence` with `is_online: true` to active members of the user's communities.
3. On disconnect, after the specific connection is removed, only broadcast offline if the user has no remaining active connections.
4. Before clearing conversation/user caches on final disconnect, collect affected communities from durable membership data, not from transient `ws_manager._conversation_users`, so offline broadcasts still work even if the user is not currently in a room map.
5. Reuse `send_with_seq` for recipient delivery so the event follows the same envelope as existing `friend_online`, `friend_offline`, and `online_friends` events.

Recipient selection:

- Query `CommunityMember` rows where changed user is `status == 'active'`.
- For each affected community, query active member user ids and the community chat conversation id.
- Send only to recipients where `ws_manager.is_connected(recipient_id)` is true.
- Do not send to inactive, pending, muted-only-if-not-active, banned, or non-member users.

Multi-device behavior:

- Opening a second device must not emit another online transition.
- Closing one of several devices must not emit offline.
- Closing the last active device emits offline once.

## Flutter Design

Add a dedicated stream for community presence:

- `WebSocketService` owns `_communityPresenceController` and exposes `communityPresenceStream`.
- `_onMessage` handles `community_member_presence` by normalizing `innerData` to `Map<String, dynamic>` and adding it to the stream.
- `dispose()` closes the new controller.

Update community chat screen:

- Add `_presenceSub` subscription in `CommunityChatScreen.initState()`.
- On each presence payload, parse `community_id`, `user_id`, and `is_online`.
- Ignore events for other communities.
- Update the matching `CommunityMember` by copying its nested `User` with `isOnline` changed.
- If the member is not loaded yet, ignore the event; the next member fetch remains the source of truth.
- Cancel `_presenceSub` in `dispose()`.

Model support:

- Add `CommunityMember.copyWith({User? user})` or rebuild the member directly. Prefer `copyWith` because it keeps mutation logic readable and consistent with existing model patterns.

## Error Handling and Consistency

- Presence events are hints for real-time UI; the members API remains the source of truth.
- Events are idempotent: receiving duplicate online/offline events should leave the same final UI state.
- If a WebSocket event is missed, reopening the community chat or refreshing members corrects the count.
- If `community_id`, `user_id`, or `is_online` is missing or malformed, Flutter ignores the event.

## Testing

Backend tests:

- Contract test confirms `community_member_presence` is present in WebSocket/backend source.
- Test/contract confirms online broadcast uses active community membership and `send_with_seq`.
- Test/contract confirms disconnect final-device path broadcasts `is_online: false` only when no active connections remain.

Flutter tests:

- Contract test confirms `WebSocketService` exposes `communityPresenceStream` and handles `community_member_presence`.
- Contract test confirms `CommunityChatScreen` subscribes to the stream and cancels the subscription.
- Contract test confirms the screen updates member `User.isOnline` with `copyWith` and filters by current `communityId`.

Verification commands:

- Backend focused community chat contract tests.
- Backend existing chat/presence contract suite.
- Flutter focused chat message/community contract tests.
- `flutter analyze`.

## Implementation Notes

- Keep event naming singular and explicit: `community_member_presence`.
- Keep backend notification persistence and push logic unchanged.
- Keep current app-level online definition aligned with `ws_manager.is_connected(user_id)`.
- Avoid polling and avoid UI changes beyond making the existing online count reactive.
