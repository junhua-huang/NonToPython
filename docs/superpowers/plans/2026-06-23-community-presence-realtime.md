# Realtime Community Presence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make community chat online member count update in real time when app-level WebSocket presence changes.

**Architecture:** Backend emits a sequenced `community_member_presence` WebSocket event when a user transitions between offline and online at the app connection level. Flutter routes that event through a dedicated stream and `CommunityChatScreen` updates the already-loaded member list idempotently.

**Tech Stack:** FastAPI WebSocket backend, SQLAlchemy ORM, Python unittest contract tests, Flutter/Dart, Riverpod screen state, broadcast `StreamController`.

---

## File Structure

- Modify: `D:/NanTuPy/tests/test_community_chat_contracts.py`
  - Adds backend contract coverage for the community presence event, recipient source, and final-device disconnect behavior.
- Modify: `D:/NanTuPy/app/ws_manager.py`
  - Adds durable community membership target lookup and `notify_community_presence()` delivery via `send_with_seq`.
  - Calls offline notification only after the last connection for a user is removed.
- Modify: `D:/NanTuPy/app/routers/ws.py`
  - Detects first app-level connection before auth registration and emits online community presence after auth initialization has joined rooms.
- Modify: `D:/FlutterProject/nonto/test/chat_message_types_contract_test.dart`
  - Adds source contract tests for the new WebSocket stream and community chat subscription/update behavior.
- Modify: `D:/FlutterProject/nonto/lib/services/websocket_service.dart`
  - Adds `communityPresenceStream` and routes `community_member_presence` events.
- Modify: `D:/FlutterProject/nonto/lib/models/community.dart`
  - Adds `CommunityMember.copyWith()` for safe nested user state updates.
- Modify: `D:/FlutterProject/nonto/lib/screens/community/community_chat_screen.dart`
  - Subscribes to community presence and updates `_members` for the current community.

---

### Task 1: Backend RED Contract Tests

**Files:**
- Modify: `D:/NanTuPy/tests/test_community_chat_contracts.py`

- [ ] **Step 1: Add failing backend contract tests**

Append these tests inside `CommunityChatContractsTest`, after `test_community_members_include_online_status_for_chat_header` and before `test_community_membership_maintains_conversation_participants_without_migration`:

```python
    def test_ws_emits_community_member_presence_event(self):
        with open('app/routers/ws.py', 'r', encoding='utf-8') as f:
            ws_source = f.read()
        with open('app/ws_manager.py', 'r', encoding='utf-8') as f:
            manager_source = f.read()

        self.assertIn('community_member_presence', ws_source + manager_source)
        self.assertIn('notify_community_presence', manager_source)
        self.assertIn('send_with_seq', manager_source)
        self.assertIn('"is_online"', manager_source)

    def test_community_presence_targets_active_members_from_durable_membership(self):
        with open('app/ws_manager.py', 'r', encoding='utf-8') as f:
            source = f.read()

        self.assertIn('CommunityMember', source)
        self.assertIn("CommunityMember.status == 'active'", source)
        self.assertIn('Conversation.type == \'community\'', source)
        self.assertIn('Conversation.community_id', source)
        self.assertIn('recipient_ids', source)

    def test_community_presence_offline_only_after_final_connection_disconnects(self):
        with open('app/ws_manager.py', 'r', encoding='utf-8') as f:
            source = f.read()
        disconnect_source = source.split('async def disconnect')[1].split('def heartbeat')[0]

        self.assertIn('if not self._connections[user_id]:', disconnect_source)
        self.assertIn('notify_community_presence(user_id, False)', disconnect_source)
        self.assertIn('_connections[user_id].pop', disconnect_source)
```

- [ ] **Step 2: Run backend focused test and verify RED**

Run from `D:/NanTuPy`:

```bash
.venv/Scripts/python.exe -m unittest tests.test_community_chat_contracts
```

Expected: FAIL with assertions mentioning missing `community_member_presence` / `notify_community_presence`.

- [ ] **Step 3: Commit RED tests if the execution mode allows commits**

```bash
git add tests/test_community_chat_contracts.py
git commit -m "test: cover realtime community presence contract"
```

Expected: commit succeeds if committing is authorized. If commits are not authorized, leave the change staged or unstaged according to the executor's workflow and continue.

---

### Task 2: Backend Community Presence Broadcast

**Files:**
- Modify: `D:/NanTuPy/app/ws_manager.py`
- Modify: `D:/NanTuPy/app/routers/ws.py`
- Test: `D:/NanTuPy/tests/test_community_chat_contracts.py`

- [ ] **Step 1: Add durable target lookup and broadcaster to `ws_manager.py`**

In `D:/NanTuPy/app/ws_manager.py`, add these methods after `_notify_friends_offline()` and before the `# 会话列表缓存` section:

```python
    def _get_community_presence_targets_sync(self, user_id: int) -> list[dict]:
        """Return active community recipient groups for a user's presence change."""
        db = SessionLocal()
        try:
            from app.models.community import CommunityMember
            from app.models.models import Conversation

            community_rows = (
                db.query(CommunityMember.community_id)
                .filter(
                    CommunityMember.user_id == user_id,
                    CommunityMember.status == 'active',
                )
                .all()
            )
            community_ids = [row[0] for row in community_rows]
            if not community_ids:
                return []

            conversations = (
                db.query(Conversation)
                .filter(
                    Conversation.type == 'community',
                    Conversation.community_id.in_(community_ids),
                )
                .all()
            )
            conversation_by_community = {
                conv.community_id: conv.id
                for conv in conversations
                if conv.community_id is not None
            }

            member_rows = (
                db.query(CommunityMember.community_id, CommunityMember.user_id)
                .filter(
                    CommunityMember.community_id.in_(community_ids),
                    CommunityMember.status == 'active',
                )
                .all()
            )
            recipient_ids_by_community: dict[int, list[int]] = {
                community_id: [] for community_id in community_ids
            }
            for community_id, member_user_id in member_rows:
                recipient_ids_by_community.setdefault(community_id, []).append(member_user_id)

            return [
                {
                    "community_id": community_id,
                    "conversation_id": conversation_by_community.get(community_id),
                    "recipient_ids": recipient_ids_by_community.get(community_id, []),
                }
                for community_id in community_ids
            ]
        finally:
            db.close()

    async def notify_community_presence(self, user_id: int, is_online: bool):
        """Notify active community members that a member's app-level presence changed."""
        loop = asyncio.get_event_loop()
        targets = await loop.run_in_executor(None, self._get_community_presence_targets_sync, user_id)
        for target in targets:
            payload = {
                "community_id": target["community_id"],
                "conversation_id": target.get("conversation_id"),
                "user_id": user_id,
                "is_online": is_online,
            }
            for recipient_id in target.get("recipient_ids", []):
                if self.is_connected(recipient_id):
                    await self.send_with_seq(recipient_id, "community_member_presence", payload)
```

- [ ] **Step 2: Emit offline event on final disconnect**

In `D:/NanTuPy/app/ws_manager.py`, inside `disconnect()`, update the final-disconnect block to include community presence before friend offline notification. The block should read:

```python
        if not self._connections[user_id]:
            del self._connections[user_id]
            self.remove_user_from_all_conversations(user_id)
            self.invalidate_user_caches(user_id)

            # 用户全部设备离线 → 通知相关社群成员和在线好友该用户已下线
            asyncio.ensure_future(self.notify_community_presence(user_id, False))
            asyncio.ensure_future(self._notify_friends_offline(user_id))
```

- [ ] **Step 3: Detect first connection in auth init**

In `D:/NanTuPy/app/routers/ws.py`, inside `_do_auth_init()`, replace:

```python
    conn_id = await ws_manager.connect(user_id, websocket)
    loop = asyncio.get_event_loop()
```

with:

```python
    was_offline = not ws_manager.is_connected(user_id)
    conn_id = await ws_manager.connect(user_id, websocket)
    loop = asyncio.get_event_loop()
```

- [ ] **Step 4: Emit online community presence after rooms are joined**

In `D:/NanTuPy/app/routers/ws.py`, inside `_do_auth_init()`, after:

```python
    for cid in conv_ids:
        ws_manager.join_conversation(user_id, cid)
```

insert:

```python
    if was_offline:
        await ws_manager.notify_community_presence(user_id, True)
```

- [ ] **Step 5: Run backend focused test and verify GREEN**

Run from `D:/NanTuPy`:

```bash
.venv/Scripts/python.exe -m unittest tests.test_community_chat_contracts
```

Expected: OK. The focused community chat contract suite passes.

- [ ] **Step 6: Run related backend contract suite**

Run from `D:/NanTuPy`:

```bash
.venv/Scripts/python.exe -m unittest tests.test_community_chat_contracts tests.test_ws_validation_unit tests.test_chat_message_type_contracts
```

Expected: OK. All selected backend tests pass.

- [ ] **Step 7: Commit backend implementation if the execution mode allows commits**

```bash
git add app/ws_manager.py app/routers/ws.py tests/test_community_chat_contracts.py
git commit -m "feat: broadcast community member presence"
```

Expected: commit succeeds if committing is authorized. If commits are not authorized, keep changes in the working tree and continue.

---

### Task 3: Flutter RED Contract Tests

**Files:**
- Modify: `D:/FlutterProject/nonto/test/chat_message_types_contract_test.dart`

- [ ] **Step 1: Add failing Flutter source contract tests**

In `D:/FlutterProject/nonto/test/chat_message_types_contract_test.dart`, append these tests inside the `group('chat message type contracts', () { ... })`, after `test('community chat source contains post/system render hooks', () { ... });`:

```dart
    test('websocket service exposes community presence stream', () {
      final source = File('lib/services/websocket_service.dart').readAsStringSync();
      expect(source, contains('_communityPresenceController'));
      expect(source, contains('communityPresenceStream'));
      expect(source, contains("case 'community_member_presence':"));
      expect(source, contains('_communityPresenceController.add'));
    });

    test('community chat subscribes to presence and updates member online state', () {
      final source = File('lib/screens/community/community_chat_screen.dart').readAsStringSync();
      expect(source, contains('_presenceSub'));
      expect(source, contains('communityPresenceStream.listen'));
      expect(source, contains('_applyCommunityPresence'));
      expect(source, contains("event['community_id']"));
      expect(source, contains('widget.communityId'));
      expect(source, contains('copyWith(isOnline: isOnline)'));
      expect(source, contains('_presenceSub?.cancel'));
    });

    test('community member supports copyWith for nested user updates', () {
      final source = File('lib/models/community.dart').readAsStringSync();
      expect(source, contains('CommunityMember copyWith'));
      expect(source, contains('user: user ?? this.user'));
    });
```

- [ ] **Step 2: Run Flutter focused test and verify RED**

Run from `D:/FlutterProject/nonto`:

```bash
flutter test test/chat_message_types_contract_test.dart
```

Expected: FAIL with missing `_communityPresenceController`, `_presenceSub`, or `CommunityMember copyWith`.

- [ ] **Step 3: Commit RED tests if the execution mode allows commits**

```bash
git add test/chat_message_types_contract_test.dart
git commit -m "test: cover community presence UI contract"
```

Expected: commit succeeds if committing is authorized. If commits are not authorized, leave the change staged or unstaged according to the executor's workflow and continue.

---

### Task 4: Flutter Community Presence Stream and UI Update

**Files:**
- Modify: `D:/FlutterProject/nonto/lib/services/websocket_service.dart`
- Modify: `D:/FlutterProject/nonto/lib/models/community.dart`
- Modify: `D:/FlutterProject/nonto/lib/screens/community/community_chat_screen.dart`
- Test: `D:/FlutterProject/nonto/test/chat_message_types_contract_test.dart`

- [ ] **Step 1: Add presence stream to `WebSocketService`**

In `D:/FlutterProject/nonto/lib/services/websocket_service.dart`, add the controller after `_onlineFriendsController`:

```dart
  final _communityPresenceController = StreamController<Map<String, dynamic>>.broadcast();
```

Add the public getter after `onlineFriendsStream`:

```dart
  /// 社群成员 App 在线状态变化流
  Stream<Map<String, dynamic>> get communityPresenceStream => _communityPresenceController.stream;
```

- [ ] **Step 2: Route `community_member_presence` events**

In `D:/FlutterProject/nonto/lib/services/websocket_service.dart`, inside `_onMessage()` switch, add this case after `online_friends` and before notification cases:

```dart
      case 'community_member_presence':
        final presenceData = innerData is Map<String, dynamic>
            ? innerData
            : (innerData is Map ? Map<String, dynamic>.from(innerData) : payload);
        _communityPresenceController.add(presenceData);
        break;
```

- [ ] **Step 3: Close the controller on dispose**

In `D:/FlutterProject/nonto/lib/services/websocket_service.dart`, inside `dispose()`, after `_onlineFriendsController.close();`, add:

```dart
    _communityPresenceController.close();
```

- [ ] **Step 4: Add `CommunityMember.copyWith()`**

In `D:/FlutterProject/nonto/lib/models/community.dart`, add this method after `toJson()` in `CommunityMember`:

```dart
  CommunityMember copyWith({User? user}) {
    return CommunityMember(
      id: id,
      communityId: communityId,
      userId: userId,
      role: role,
      status: status,
      joinedAt: joinedAt,
      user: user ?? this.user,
    );
  }
```

- [ ] **Step 5: Add presence subscription field**

In `D:/FlutterProject/nonto/lib/screens/community/community_chat_screen.dart`, add the field after `_messageSub`:

```dart
  StreamSubscription<Map<String, dynamic>>? _presenceSub;
```

- [ ] **Step 6: Subscribe in `initState()`**

In `D:/FlutterProject/nonto/lib/screens/community/community_chat_screen.dart`, update `initState()` to include the presence listener after the message listener:

```dart
    _presenceSub = WebSocketService()
        .communityPresenceStream
        .listen(_applyCommunityPresence);
```

The start of `initState()` should become:

```dart
  @override
  void initState() {
    super.initState();
    _messageSub =
        WebSocketService().messageStream.listen(_appendRealtimeMessage);
    _presenceSub = WebSocketService()
        .communityPresenceStream
        .listen(_applyCommunityPresence);
    _loadMessages();
    unawaited(_loadMembersForHeader());
  }
```

- [ ] **Step 7: Cancel subscription in `dispose()`**

In `D:/FlutterProject/nonto/lib/screens/community/community_chat_screen.dart`, inside `dispose()`, after `_messageSub?.cancel();`, add:

```dart
    _presenceSub?.cancel();
```

- [ ] **Step 8: Add presence parsing and update methods**

In `D:/FlutterProject/nonto/lib/screens/community/community_chat_screen.dart`, add these methods after `_writeMessagesCache()` and before `dispose()`:

```dart
  void _applyCommunityPresence(Map<String, dynamic> event) {
    final communityId = _parsePresenceInt(event['community_id']);
    final userId = _parsePresenceInt(event['user_id']);
    final isOnline = event['is_online'];
    if (communityId != widget.communityId || userId == null || isOnline is! bool) {
      return;
    }

    final index = _members.indexWhere((member) => member.userId == userId);
    if (index < 0) return;
    final member = _members[index];
    final user = member.user;
    if (user == null || user.isOnline == isOnline) return;

    if (!mounted) return;
    setState(() {
      _members[index] = member.copyWith(
        user: user.copyWith(isOnline: isOnline),
      );
    });
  }

  int? _parsePresenceInt(dynamic value) {
    if (value is int) return value;
    return int.tryParse(value?.toString() ?? '');
  }
```

- [ ] **Step 9: Run Flutter focused test and verify GREEN**

Run from `D:/FlutterProject/nonto`:

```bash
flutter test test/chat_message_types_contract_test.dart
```

Expected: All tests in `chat_message_types_contract_test.dart` pass.

- [ ] **Step 10: Run Flutter analyzer**

Run from `D:/FlutterProject/nonto`:

```bash
flutter analyze
```

Expected: `No issues found!`

- [ ] **Step 11: Commit Flutter implementation if the execution mode allows commits**

```bash
git add lib/services/websocket_service.dart lib/models/community.dart lib/screens/community/community_chat_screen.dart test/chat_message_types_contract_test.dart
git commit -m "feat: update community presence in realtime"
```

Expected: commit succeeds if committing is authorized. If commits are not authorized, keep changes in the working tree and continue.

---

### Task 5: Cross-Repo Verification

**Files:**
- Verify backend repo: `D:/NanTuPy`
- Verify Flutter repo: `D:/FlutterProject/nonto`

- [ ] **Step 1: Run backend presence/chat contract suite**

Run from `D:/NanTuPy`:

```bash
.venv/Scripts/python.exe -m unittest tests.test_community_chat_contracts tests.test_ws_validation_unit tests.test_chat_message_type_contracts tests.test_identity_role_contracts tests.test_push_device_state_contracts tests.test_notification_service_unit
```

Expected: OK. The selected backend contract suite passes.

- [ ] **Step 2: Run Flutter focused regression suite**

Run from `D:/FlutterProject/nonto`:

```bash
flutter test test/chat_message_types_contract_test.dart test/role_identity_contract_test.dart test/permissions_and_unread_regression_test.dart
```

Expected: All selected Flutter tests pass.

- [ ] **Step 3: Run Flutter analyzer**

Run from `D:/FlutterProject/nonto`:

```bash
flutter analyze
```

Expected: `No issues found!`

- [ ] **Step 4: Inspect working trees**

Run:

```bash
git -C D:/NanTuPy status --short --branch
git -C D:/FlutterProject/nonto status --short --branch
```

Expected: only intentional files are modified, or both trees are clean if commits were authorized and completed.

- [ ] **Step 5: Final report**

Report these facts exactly:

```text
Backend: community_member_presence added for app-level online/offline transitions.
Flutter: CommunityChatScreen listens for community presence and updates the existing online count in real time.
Verification: list each command run and whether it passed or failed.
Commits: list commit hashes if commits were authorized; otherwise state that commits were not created.
```

---

## Self-Review

- Spec coverage: backend online/offline event, app-level online definition, no polling, Flutter stream, UI update, idempotency, malformed event ignore behavior, and verification are all covered by tasks.
- Placeholder scan: no deferred sections or unspecified implementation steps remain.
- Type consistency: event name is consistently `community_member_presence`; payload fields are consistently `community_id`, `conversation_id`, `user_id`, and `is_online`; Flutter stream name is consistently `communityPresenceStream`.
