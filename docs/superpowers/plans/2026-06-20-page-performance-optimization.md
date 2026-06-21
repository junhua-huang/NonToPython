# Page Performance Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve first-paint and API performance for Home, Explore/Search, Conversations, and Profile pages without changing authentication transport or user-visible data semantics.

**Architecture:** Implement in reviewable batches: observability first, frontend first-paint reduction second, backend query/pagination/N+1 fixes third, index/migration support fourth. Use focused regression tests before each behavioral change and keep optional slow modules from blocking page-level rendering.

**Tech Stack:** FastAPI, SQLAlchemy, MySQL/MariaDB via PyMySQL, Python unittest, Flutter/Dart, Dio API client, Riverpod providers, DataLayer cache, Drift/SQLite local chat cache.

---

## File Map

### Backend

- Modify: `D:\NanTuPy\app\main.py`
  - Add safe request timing middleware before route registration.
- Modify: `D:\NanTuPy\app\routers\chat.py`
  - Add pagination to `/api/chat/sessions`.
  - Bulk-update mark-read.
  - Bound `/api/chat/messages/batch` work.
- Modify: `D:\NanTuPy\app\routers\posts.py`
  - Optimize `/api/posts/user/{user_id}/liked` to avoid row-by-row post loading.
- Modify: `D:\NanTuPy\app\routers\topics.py`
  - Batch follow-state lookup for `/api/topics/trending`.
- Modify: `D:\NanTuPy\app\routers\comic.py`
  - Batch follow-state lookup for `/api/comic/events`.
- Inspect/possibly modify: `D:\NanTuPy\app\routers\recommendations.py`, `D:\NanTuPy\app\services\recommendation_service.py`
  - Add bounded/short-TTL handling only after tests reveal safe insertion point.
- Create: `D:\NanTuPy\tests\test_performance_observability.py`
- Create: `D:\NanTuPy\tests\test_chat_performance_contracts.py`
- Create: `D:\NanTuPy\tests\test_posts_liked_performance_contract.py`
- Create: `D:\NanTuPy\tests\test_explore_batching_contracts.py`
- Create: `D:\NanTuPy\scripts\performance_indexes.sql`
  - SQL script for candidate indexes using idempotent checks where supported by deployed DB version.

### Frontend

- Modify: `D:\FlutterProject\nonto\lib\services\api\chat_service.dart`
  - Add optional page/perPage parameters to `getConversations()`.
- Modify: `D:\FlutterProject\nonto\lib\services\local_db_service.dart`
  - Replace all-conversation eager preload with limited/deferred recent preload.
  - Use one message cache key convention.
- Modify: `D:\FlutterProject\nonto\lib\providers\chat_notifiers.dart`
  - Ensure conversations loader does not synchronously wait on message preload.
- Modify: `D:\FlutterProject\nonto\lib\screens\messages\messages_tab.dart` and/or `D:\FlutterProject\nonto\lib\screens\chat\conversations_tab.dart`
  - Ensure first paint is conversations-first.
- Modify: `D:\FlutterProject\nonto\lib\screens\profile\profile_tab.dart`
  - Remove duplicate `_loadUserPosts()` calls and make stats/liked/posts loads parallel-safe.
- Modify: `D:\FlutterProject\nonto\lib\screens\profile\user_profile_screen.dart`
  - Apply same duplicate-load/parallelization rules for other users' profiles.
- Modify: `D:\FlutterProject\nonto\lib\screens\search\search_tab.dart`, `D:\FlutterProject\nonto\lib\providers\explore_notifier.dart`, `D:\FlutterProject\nonto\lib\widgets\search_suggestions.dart`
  - Decouple optional module loading and reduce duplicate suggestion requests.
- Modify: `D:\FlutterProject\nonto\lib\services\comic_service.dart`
  - Route GET calls through `ApiClient.getDeduped` if they currently bypass it.
- Create: `D:\FlutterProject\nonto\test\page_performance_regression_test.dart`

---

## Task 1: Backend Request Timing Observability

**Files:**
- Test: `D:\NanTuPy\tests\test_performance_observability.py`
- Modify: `D:\NanTuPy\app\main.py`

- [ ] **Step 1: Write failing backend observability tests**

Create `D:\NanTuPy\tests\test_performance_observability.py` with:

```python
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


class PerformanceObservabilitySourceTest(unittest.TestCase):
    def test_main_registers_request_timing_middleware(self):
        with open('app/main.py', 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('async def log_request_timing', source)
        self.assertIn('PERF_SLOW_REQUEST_MS', source)
        self.assertIn('elapsed_ms', source)
        self.assertIn('logger.warning', source)

    def test_security_headers_middleware_still_exists(self):
        with open('app/main.py', 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('async def add_security_headers', source)
        self.assertIn('Cross-Origin-Opener-Policy', source)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_performance_observability
```

Expected: FAIL because `log_request_timing` does not exist yet.

- [ ] **Step 3: Add request timing middleware**

Modify `D:\NanTuPy\app\main.py`:

1. Change imports near the top:

```python
import logging
import os
import time
```

2. Insert this middleware after CORS registration and before `add_security_headers`:

```python
_perf_slow_request_ms = int(os.environ.get('PERF_SLOW_REQUEST_MS', '500'))
_perf_logger = logging.getLogger('app.performance')


@app.middleware("http")
async def log_request_timing(request, call_next):
    start = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        path = request.url.path
        if path.startswith('/api/'):
            log_payload = {
                'method': request.method,
                'path': path,
                'status_code': status_code,
                'elapsed_ms': elapsed_ms,
            }
            if elapsed_ms >= _perf_slow_request_ms:
                _perf_logger.warning('slow_request %s', log_payload)
            else:
                _perf_logger.info('request_timing %s', log_payload)
```

- [ ] **Step 4: Run observability test**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_performance_observability
```

Expected: OK.

- [ ] **Step 5: Compile backend entrypoint**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m py_compile app/main.py
```

Expected: exit code 0.

---

## Task 2: Frontend Performance Regression Guard Tests

**Files:**
- Test: `D:\FlutterProject\nonto\test\page_performance_regression_test.dart`

- [ ] **Step 1: Write source regression tests**

Create `D:\FlutterProject\nonto\test\page_performance_regression_test.dart` with:

```dart
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

String read(String relativePath) => File(relativePath).readAsStringSync();

void main() {
  group('page performance regressions', () {
    test('chat service supports paginated conversations', () {
      final source = read('lib/services/api/chat_service.dart');
      expect(source, contains('getConversations({int page = 1, int perPage = 30})'));
      expect(source, contains("'/chat/sessions'"));
      expect(source, contains("'page': page"));
      expect(source, contains("'per_page': perPage"));
    });

    test('local db preload is bounded and does not fetch every conversation by default', () {
      final source = read('lib/services/local_db_service.dart');
      expect(source, contains('preloadRecentConversationMessages'));
      expect(source, contains('maxConversations'));
      expect(source, isNot(contains('preloadAllConversationMessages({int perPage = 50})')));
      expect(source, isNot(contains('final allConvIds = conversations.map((c) => c.id).toList();')));
    });

    test('profile tab does not duplicate initial post loading through stats loader', () {
      final source = read('lib/screens/profile/profile_tab.dart');
      expect(source, contains('Future.wait'));
      expect(source, contains('_loadInitialProfileData'));
      expect(source, isNot(contains('_loadStats();\n    // 并行触发帖子/喜欢列表加载')));
      expect(source, isNot(contains('_loadLikedPosts();\n    _loadUserPosts();')));
    });

    test('explore/search modules are allowed to settle independently', () {
      final exploreSource = read('lib/providers/explore_notifier.dart');
      final searchTabSource = read('lib/screens/search/search_tab.dart');
      expect(exploreSource + searchTabSource, contains('Future.wait'));
      expect(exploreSource + searchTabSource, contains('eagerError: false'));
    });

    test('comic service GET requests use request dedupe', () {
      final source = read('lib/services/comic_service.dart');
      expect(source, contains('getDeduped'));
      expect(source, isNot(contains('_dio.get')));
    });
  });
}
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
cd /d/FlutterProject/nonto && flutter test test/page_performance_regression_test.dart
```

Expected: FAIL on at least paginated conversations, bounded preload, and profile duplicate-load checks.

---

## Task 3: Frontend Conversation First-Paint Optimization

**Files:**
- Modify: `D:\FlutterProject\nonto\lib\services\api\chat_service.dart`
- Modify: `D:\FlutterProject\nonto\lib\services\local_db_service.dart`
- Modify: `D:\FlutterProject\nonto\lib\providers\chat_notifiers.dart`
- Test: `D:\FlutterProject\nonto\test\page_performance_regression_test.dart`

- [ ] **Step 1: Update ChatService conversation pagination**

Replace the existing method in `D:\FlutterProject\nonto\lib\services\api\chat_service.dart`:

```dart
Future<ApiResponse> getConversations() => _api.getDeduped('/chat/sessions');
```

with:

```dart
Future<ApiResponse> getConversations({int page = 1, int perPage = 30}) =>
    _api.getDeduped('/chat/sessions', params: {'page': page, 'per_page': perPage});
```

- [ ] **Step 2: Replace all-conversation preload with bounded recent preload**

In `D:\FlutterProject\nonto\lib\services\local_db_service.dart`, replace the whole `preloadAllConversationMessages` method with:

```dart
Future<void> preloadRecentConversationMessages({int perPage = 30, int maxConversations = 5}) async {
  final db = _db;
  if (db == null) return;

  final userId = _currentUserId;
  if (userId == null) return;

  final conversations = await getConversations();
  final convIds = conversations.map((c) => c.id).take(maxConversations).toList();
  if (convIds.isEmpty) return;

  for (final convId in convIds) {
    final localMessages = await getMessages(convId, limit: perPage);
    if (localMessages.isNotEmpty) {
      final localJson = localMessages.map((m) => m.toJson()).toList();
      await DataLayer().write(CacheKeys.msgRecent(convId), localJson);
      await DataLayer().write(CacheKeys.msgRecentByUser(convId, userId), localJson);
    }
  }

  try {
    debugPrint('[Preload] recent batch HTTP: ${convIds.length} convs, perPage=$perPage');
    final resp = await ChatService()
        .getBatchMessages(convIds, perPage: perPage)
        .timeout(const Duration(seconds: 12));
    if (resp.success && resp.data != null) {
      final data = resp.data is String
          ? (() { try { return jsonDecode(resp.data as String); } catch (_) { return {}; } })()
          : resp.data;
      final batchConversations =
          data['data']?['conversations'] ?? data['conversations'] ?? <dynamic>[];

      for (final c in batchConversations) {
        if (c is! Map<String, dynamic>) continue;
        final convId = c['conversation_id'] ?? c['id'];
        final messages = c['messages'];
        if (convId is int && messages is List && messages.isNotEmpty) {
          final localMessages = await getMessages(convId, limit: perPage);
          await _mergeAndPersist(
            db: db,
            userId: userId,
            convId: convId,
            perPage: perPage,
            localMessages: localMessages,
            serverJsonList: messages.cast<Map<String, dynamic>>(),
          );
        }
      }
    }
  } catch (e) {
    debugPrint('[Preload] recent preload skipped: $e');
  }

  DataLayer().invalidate(CacheKeys.convPattern);
}
```

- [ ] **Step 3: Keep compatibility wrapper if callers still exist**

Search for `preloadAllConversationMessages`. If any caller remains and cannot be updated in the same step, add this wrapper below `preloadRecentConversationMessages`:

```dart
@Deprecated('Use preloadRecentConversationMessages to avoid blocking messages first paint.')
Future<void> preloadAllConversationMessages({int perPage = 50}) =>
    preloadRecentConversationMessages(perPage: perPage, maxConversations: 5);
```

If the source regression test requires removal, update callers and do not keep the wrapper.

- [ ] **Step 4: Update preload callers**

In `D:\FlutterProject\nonto\lib\providers\chat_notifiers.dart` and any messages/conversations tab caller, replace:

```dart
preloadAllConversationMessages
```

with:

```dart
preloadRecentConversationMessages
```

Make sure the preload call is not awaited before the conversation list is rendered. Use unawaited-style scheduling already present in the file, or:

```dart
Future.microtask(() => LocalDbService().preloadRecentConversationMessages(perPage: 30, maxConversations: 5));
```

- [ ] **Step 5: Run frontend regression test**

Run:

```bash
cd /d/FlutterProject/nonto && flutter test test/page_performance_regression_test.dart
```

Expected: conversation-related checks pass; other checks may still fail until later tasks.

---

## Task 4: Frontend Profile Duplicate-Load Optimization

**Files:**
- Modify: `D:\FlutterProject\nonto\lib\screens\profile\profile_tab.dart`
- Modify: `D:\FlutterProject\nonto\lib\screens\profile\user_profile_screen.dart`
- Test: `D:\FlutterProject\nonto\test\page_performance_regression_test.dart`

- [ ] **Step 1: Replace ProfileTab init loading with a single coordinator**

In `D:\FlutterProject\nonto\lib\screens\profile\profile_tab.dart`, replace the body of `initState()` after listeners are registered:

```dart
_loadStats();
// 并行触发帖子/喜欢列表加载，避免串行等待 stats 期间命中「没有帖子」缺省页。
// _loadStats() 内部也会再调一次（命中缓存读取，开销极小，且自带重入语义）。
_loadUserPosts();
```

with:

```dart
_loadInitialProfileData();
```

- [ ] **Step 2: Add initial profile data coordinator**

Add this method above `_loadStats()`:

```dart
Future<void> _loadInitialProfileData() async {
  await Future.wait([
    _loadStats(),
    _loadUserPosts(),
    _loadLikedPosts(),
  ], eagerError: false);
}
```

- [ ] **Step 3: Stop stats loader from triggering more loaders**

In `_loadStats()`, remove the final two calls:

```dart
_loadLikedPosts();
_loadUserPosts();
```

The method should end after its `setState` block.

- [ ] **Step 4: Avoid duplicated refresh work**

In `_onRefresh()`, replace:

```dart
await Future.wait([
  _refreshUserProfile(),
  _loadStats(),
  _loadUserPostsForceRefresh(),
  _loadLikedPostsForceRefresh(),
]);
```

with:

```dart
await Future.wait([
  _refreshUserProfile(),
  _loadStats(),
  _loadUserPostsForceRefresh(),
  _loadLikedPostsForceRefresh(),
], eagerError: false);
```

- [ ] **Step 5: Apply same pattern to user_profile_screen**

In `D:\FlutterProject\nonto\lib\screens\profile\user_profile_screen.dart`, ensure any initial load block uses one coordinator with `Future.wait(..., eagerError: false)` and does not call the same posts or liked-posts loader from both the coordinator and stats loader. Use this pattern:

```dart
Future<void> _loadInitialUserProfileData() async {
  await Future.wait([
    _loadUserProfile(),
    _loadUserPosts(),
    _loadFriendStatus(),
  ], eagerError: false);
}
```

Keep existing method names if the file already has equivalent methods; the required behavior is one initial coordinator and no duplicate post loader call.

- [ ] **Step 6: Run frontend regression test**

Run:

```bash
cd /d/FlutterProject/nonto && flutter test test/page_performance_regression_test.dart
```

Expected: profile checks pass; explore/comic checks may still fail until later tasks.

---

## Task 5: Frontend Explore/Search Decoupling and GET Dedupe

**Files:**
- Modify: `D:\FlutterProject\nonto\lib\providers\explore_notifier.dart`
- Modify: `D:\FlutterProject\nonto\lib\screens\search\search_tab.dart`
- Modify: `D:\FlutterProject\nonto\lib\widgets\search_suggestions.dart`
- Modify: `D:\FlutterProject\nonto\lib\services\comic_service.dart`
- Test: `D:\FlutterProject\nonto\test\page_performance_regression_test.dart`

- [ ] **Step 1: Make explore module loading non-fatal**

Where explore/search code currently does:

```dart
await Future.wait([
```

for independent modules, change it to:

```dart
await Future.wait([
```

with the closing call:

```dart
], eagerError: false);
```

If each module updates state separately, prefer this pattern:

```dart
Future<void> loadOptionalModule(Future<void> Function() loader, String label) async {
  try {
    await loader();
  } catch (e) {
    debugPrint('Explore optional module $label failed: $e');
  }
}

await Future.wait([
  loadOptionalModule(_loadTrendingTopics, 'topics'),
  loadOptionalModule(_loadComicEvents, 'comic'),
  loadOptionalModule(_loadRecommendedUsers, 'users'),
], eagerError: false);
```

Use existing method names in the file; the important contract is `eagerError: false` and per-module error isolation.

- [ ] **Step 2: Reduce search suggestions fan-out**

In `D:\FlutterProject\nonto\lib\widgets\search_suggestions.dart`, for short queries below 2 trimmed characters, return local/history suggestions only and skip global network search:

```dart
final trimmed = query.trim();
if (trimmed.length < 2) {
  setState(() {
    _suggestions = const [];
    _isLoading = false;
  });
  return;
}
```

For network suggestions, prefer a single lightweight endpoint already present in `SearchService`, such as `suggestUsers` or `searchTopics`, instead of global search plus multiple category searches. Keep global search for explicit search submission, not every suggestion update.

- [ ] **Step 3: Route comic GETs through ApiClient.getDeduped**

In `D:\FlutterProject\nonto\lib\services\comic_service.dart`, replace direct Dio GET calls such as:

```dart
_api.dio.get('/comic/events')
```

or:

```dart
_dio.get('/comic/events')
```

with ApiClient dedupe calls matching existing API service style:

```dart
ApiClient().getDeduped('/comic/events', params: params)
```

Keep POST/PUT/DELETE calls unchanged.

- [ ] **Step 4: Run frontend performance regression test**

Run:

```bash
cd /d/FlutterProject/nonto && flutter test test/page_performance_regression_test.dart
```

Expected: all checks in `page_performance_regression_test.dart` pass.

---

## Task 6: Backend Chat Pagination and Bulk Mark-Read

**Files:**
- Test: `D:\NanTuPy\tests\test_chat_performance_contracts.py`
- Modify: `D:\NanTuPy\app\routers\chat.py`

- [ ] **Step 1: Write backend chat source contract tests**

Create `D:\NanTuPy\tests\test_chat_performance_contracts.py` with:

```python
import unittest


class ChatPerformanceContractsTest(unittest.TestCase):
    def test_sessions_endpoint_is_paginated(self):
        with open('app/routers/chat.py', 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('page: int = Query(1, ge=1)', source)
        self.assertIn('per_page: int = Query(30, ge=1, le=100)', source)
        self.assertIn('.offset(offset)', source)
        self.assertIn('.limit(per_page)', source)

    def test_mark_read_uses_bulk_update(self):
        with open('app/routers/chat.py', 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('.update({Message.is_read: True}', source)
        self.assertNotIn('for m in unread:\n        m.is_read = True', source)

    def test_batch_messages_is_bounded_to_twenty_conversations(self):
        with open('app/routers/chat.py', 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('最多 20 个', source)
        self.assertIn('conv_id_list = conv_id_list[:20]', source)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_chat_performance_contracts
```

Expected: FAIL because `/sessions` is not paginated and mark-read loops rows.

- [ ] **Step 3: Add pagination parameters to sessions endpoint**

In `D:\NanTuPy\app\routers\chat.py`, change `get_sessions` signature to:

```python
def get_sessions(
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
```

Ensure `Query` is imported at the top of the file. If it already is imported, do not duplicate it.

- [ ] **Step 4: Apply pagination before `.all()`**

Replace the conversations query block in `get_sessions` with:

```python
offset = (page - 1) * per_page
base_query = (
    db.query(Conversation)
    .filter(
        (Conversation.user1_id == user.id) | (Conversation.user2_id == user.id)
    )
    .order_by(func.coalesce(Conversation.last_message_at, Conversation.created_at).desc())
)
total = base_query.count()
conversations = base_query.offset(offset).limit(per_page).all()
```

At the final return, replace:

```python
return {"sessions": result, "total": len(result)}
```

with:

```python
return {"sessions": result, "total": total, "page": page, "per_page": per_page}
```

For blocked users, keep filtering in memory for now to preserve existing block behavior; return the original `total` as total before block filtering only if frontend uses it for pagination. If product semantics require hidden blocked sessions not counted, add a later SQL-level block filter.

- [ ] **Step 5: Bound batch messages conversation count**

In `get_messages_batch`, after parsing `conv_id_list`, add:

```python
conv_id_list = conv_id_list[:20]
```

Keep `per_page` bounded by existing `Query(30, ge=1, le=100)`.

- [ ] **Step 6: Replace row-by-row mark-read with bulk update**

In `mark_conversation_as_read`, replace:

```python
unread = (
    db.query(Message)
    .filter(
        Message.conversation_id == conversation_id,
        Message.is_read == False,
        Message.sender_id != user.id,
    )
    .all()
)

for m in unread:
    m.is_read = True
```

with:

```python
updated_count = (
    db.query(Message)
    .filter(
        Message.conversation_id == conversation_id,
        Message.is_read == False,
        Message.sender_id != user.id,
    )
    .update({Message.is_read: True}, synchronize_session=False)
)
```

If the response currently uses `len(unread)`, replace it with `updated_count`.

- [ ] **Step 7: Run backend chat tests**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_chat_performance_contracts
```

Expected: OK.

- [ ] **Step 8: Compile chat router**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m py_compile app/routers/chat.py
```

Expected: exit code 0.

---

## Task 7: Backend Profile Liked Posts N+1 Reduction

**Files:**
- Test: `D:\NanTuPy\tests\test_posts_liked_performance_contract.py`
- Modify: `D:\NanTuPy\app\routers\posts.py`

- [ ] **Step 1: Write liked-post source contract test**

Create `D:\NanTuPy\tests\test_posts_liked_performance_contract.py` with:

```python
import unittest


class PostsLikedPerformanceContractTest(unittest.TestCase):
    def test_liked_posts_query_joins_posts_in_one_query(self):
        with open('app/routers/posts.py', 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('db.query(Post, Like.created_at.label("liked_at"))', source)
        self.assertIn('.join(Like, Like.post_id == Post.id)', source)
        self.assertNotIn('liked_posts = [like.post for like in likes]', source)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_posts_liked_performance_contract
```

Expected: FAIL if the current implementation walks `like.post` row by row.

- [ ] **Step 3: Replace liked posts loading query**

In `D:\NanTuPy\app\routers\posts.py`, inside `get_user_liked_posts`, replace the liked-post loading section with this query shape:

```python
offset = (page - 1) * per_page
liked_rows = (
    db.query(Post, Like.created_at.label("liked_at"))
    .join(Like, Like.post_id == Post.id)
    .filter(Like.user_id == user_id)
    .order_by(Like.created_at.desc())
    .offset(offset)
    .limit(per_page)
    .all()
)

total = db.query(Like).filter(Like.user_id == user_id).count()
posts = [post.to_dict(current_user_id=user.id) for post, liked_at in liked_rows]
```

Preserve existing privacy/block checks before this query. Preserve the existing response shape:

```python
return {
    "posts": posts,
    "total": total,
    "page": page,
    "per_page": per_page,
}
```

- [ ] **Step 4: Run liked posts test and compile**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_posts_liked_performance_contract && ./.venv/Scripts/python.exe -m py_compile app/routers/posts.py
```

Expected: OK and compile success.

---

## Task 8: Backend Explore Side-Module Batching

**Files:**
- Test: `D:\NanTuPy\tests\test_explore_batching_contracts.py`
- Modify: `D:\NanTuPy\app\routers\topics.py`
- Modify: `D:\NanTuPy\app\routers\comic.py`

- [ ] **Step 1: Write batching source contract tests**

Create `D:\NanTuPy\tests\test_explore_batching_contracts.py` with:

```python
import unittest


class ExploreBatchingContractsTest(unittest.TestCase):
    def test_trending_topics_batches_follow_lookup(self):
        with open('app/routers/topics.py', 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('followed_topic_ids', source)
        self.assertIn('TopicFollower.topic_id.in_(topic_ids)', source)
        self.assertNotIn('TopicService.is_user_following_topic(db, user.id, topic["id"])', source)

    def test_comic_events_batches_follow_lookup(self):
        with open('app/routers/comic.py', 'r', encoding='utf-8') as f:
            source = f.read()
        self.assertIn('followed_event_ids', source)
        self.assertIn('event_id IN :event_ids', source)
        self.assertNotIn('SELECT 1 FROM comic_event_follows WHERE event_id = :eid AND user_id = :uid', source)


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_explore_batching_contracts
```

Expected: FAIL because follow checks are per-row.

- [ ] **Step 3: Batch trending topic follow lookup**

In `D:\NanTuPy\app\routers\topics.py`, add imports if missing:

```python
from app.models.community import TopicFollower
```

In `get_trending_topics`, replace the loop that calls `TopicService.is_user_following_topic` with:

```python
topic_ids = [topic["id"] for topic in trending]
followed_topic_ids = set()
if topic_ids:
    followed_rows = (
        db.query(TopicFollower.topic_id)
        .filter(
            TopicFollower.user_id == user.id,
            TopicFollower.topic_id.in_(topic_ids),
        )
        .all()
    )
    followed_topic_ids = {row[0] for row in followed_rows}

topics_with_follow = []
for topic in trending:
    topic_data = dict(topic)
    topic_data["is_following"] = topic["id"] in followed_topic_ids
    topics_with_follow.append(topic_data)
```

- [ ] **Step 4: Batch comic event follow lookup**

In `D:\NanTuPy\app\routers\comic.py`, after `rows = db.execute(...).fetchall()` and before building `result`, add:

```python
event_ids = [r[0] for r in rows]
followed_event_ids = set()
if user_id is not None and event_ids:
    followed_rows = db.execute(
        text("SELECT event_id FROM comic_event_follows WHERE user_id = :uid AND event_id IN :event_ids"),
        {'uid': user_id, 'event_ids': tuple(event_ids)}
    ).fetchall()
    followed_event_ids = {row[0] for row in followed_rows}
```

Inside the result loop, replace the per-event follow query with:

```python
is_followed = event_id in followed_event_ids
```

If SQLAlchemy text binding for tuple `IN :event_ids` fails under the project's SQLAlchemy version, use an expanding bindparam:

```python
from sqlalchemy import bindparam

follow_sql = text(
    "SELECT event_id FROM comic_event_follows WHERE user_id = :uid AND event_id IN :event_ids"
).bindparams(bindparam('event_ids', expanding=True))
followed_rows = db.execute(follow_sql, {'uid': user_id, 'event_ids': event_ids}).fetchall()
```

- [ ] **Step 5: Run batching tests and compile**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_explore_batching_contracts && ./.venv/Scripts/python.exe -m py_compile app/routers/topics.py app/routers/comic.py
```

Expected: OK and compile success.

---

## Task 9: Database Index Script

**Files:**
- Create: `D:\NanTuPy\scripts\performance_indexes.sql`

- [ ] **Step 1: Create SQL script**

Create `D:\NanTuPy\scripts\performance_indexes.sql` with:

```sql
-- Performance indexes for Home, Explore, Conversations, and Profile pages.
-- Review existing indexes before applying. MySQL 8.0+ supports CREATE INDEX IF NOT EXISTS only in some compatible engines;
-- if unsupported, check INFORMATION_SCHEMA.STATISTICS before running each CREATE INDEX.

CREATE INDEX idx_posts_public_created ON posts (is_public, created_at);
CREATE INDEX idx_posts_user_public_created ON posts (user_id, is_public, created_at);
CREATE INDEX idx_posts_visibility_created ON posts (visibility, created_at);

CREATE INDEX idx_friendships_sender_status_receiver ON friendships (sender_id, status, receiver_id);
CREATE INDEX idx_friendships_receiver_status_sender ON friendships (receiver_id, status, sender_id);

CREATE INDEX idx_likes_user_created ON likes (user_id, created_at);
CREATE INDEX idx_likes_post_user ON likes (post_id, user_id);

CREATE INDEX idx_conversations_user1_last_message ON conversations (user1_id, last_message_at);
CREATE INDEX idx_conversations_user2_last_message ON conversations (user2_id, last_message_at);
CREATE INDEX idx_messages_conversation_created ON messages (conversation_id, created_at);
CREATE INDEX idx_messages_conversation_read_sender ON messages (conversation_id, is_read, sender_id);

CREATE INDEX idx_post_topics_topic_post ON post_topics (topic_id, post_id);
CREATE INDEX idx_topic_followers_topic_user ON topic_followers (topic_id, user_id);
CREATE INDEX idx_topic_followers_user_topic ON topic_followers (user_id, topic_id);

CREATE INDEX idx_comic_events_status_start ON comic_events (status, start_date);
CREATE INDEX idx_comic_events_city_status_start ON comic_events (city_id, status, start_date);
CREATE INDEX idx_comic_event_follows_event_user ON comic_event_follows (event_id, user_id);
CREATE INDEX idx_comic_event_follows_user_event ON comic_event_follows (user_id, event_id);
CREATE INDEX idx_comic_event_images_event_cover_sort ON comic_event_images (event_id, is_cover, sort_order);
```

- [ ] **Step 2: Do not apply script automatically**

Do not run this SQL against production from the agent. Report that the script is ready and must be applied after checking existing indexes. If a local development DB is available and the user explicitly approves, apply there only.

---

## Task 10: Search and Home Feed Guardrails

**Files:**
- Modify: `D:\NanTuPy\app\routers\search.py`
- Inspect/possibly modify: `D:\NanTuPy\app\routers\recommendations.py`
- Inspect/possibly modify: `D:\NanTuPy\app\services\recommendation_service.py`

- [ ] **Step 1: Bound very short search queries**

In search endpoints that accept `q`, normalize input:

```python
q = (q or '').strip()
if len(q) < 2:
    return {"results": [], "total": 0, "page": page, "per_page": per_page}
```

For endpoints that return category maps, preserve their response shape with empty lists:

```python
return {
    "users": [],
    "posts": [],
    "topics": [],
    "total": 0,
    "page": page,
    "per_page": per_page,
}
```

- [ ] **Step 2: Keep global search result limits bounded**

Ensure `/api/search/global` enforces `per_page <= 10` or uses the existing FastAPI `Query(..., le=10)` limit. If the public API currently allows larger values, clamp internally:

```python
per_page = min(per_page, 10)
```

- [ ] **Step 3: Inspect recommendation feed before changing SQL**

Read `D:\NanTuPy\app\routers\recommendations.py` and `D:\NanTuPy\app\services\recommendation_service.py`. If recommendation feed already has a service boundary, add only one of these low-risk guardrails:

```python
per_page = min(per_page, 20)
```

or a short in-process cache keyed by `(user.id, page, per_page)` with a TTL no longer than 30 seconds. Do not cache if the response includes private visibility data that could leak across users.

- [ ] **Step 4: Compile changed backend files**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m py_compile app/routers/search.py app/routers/recommendations.py app/services/recommendation_service.py
```

Expected: exit code 0.

---

## Task 11: Final Verification

**Files:**
- All changed files from tasks above.

- [ ] **Step 1: Run backend focused tests**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_performance_observability tests.test_chat_performance_contracts tests.test_posts_liked_performance_contract tests.test_explore_batching_contracts tests.test_ws_validation_unit tests.test_ws_manager_scheduling tests.test_notification_service_unit tests.test_cors_config tests.test_community_service_sql
```

Expected: all tests pass.

- [ ] **Step 2: Compile changed backend files**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m py_compile app/main.py app/routers/chat.py app/routers/posts.py app/routers/topics.py app/routers/comic.py app/routers/search.py app/routers/recommendations.py app/services/recommendation_service.py
```

Expected: exit code 0.

- [ ] **Step 3: Run frontend focused tests**

Run:

```bash
cd /d/FlutterProject/nonto && flutter test --dart-define=API_BASE_URL=https://www.nonto.online/api --dart-define=WS_URL=wss://www.nonto.online/ws test/page_performance_regression_test.dart test/notification_ux_regression_test.dart test/chat_reliability_regression_test.dart test/app_config_test.dart test/image_utils_test.dart
```

Expected: all tests pass.

- [ ] **Step 4: Build Flutter web debug**

Run:

```bash
cd /d/FlutterProject/nonto && flutter build web --debug --dart-define=API_BASE_URL=https://www.nonto.online/api --dart-define=WS_URL=wss://www.nonto.online/ws
```

Expected: `√ Built build\web`. Existing Flutter loader deprecation and wasm dry-run warnings are acceptable if unchanged from previous verification.

- [ ] **Step 5: Report results and skipped work**

Report:

- Tests that passed.
- Any tests that failed with exact error output.
- Whether `scripts/performance_indexes.sql` was created but not applied.
- Whether recommendation feed SQL was changed or only bounded/observed.
- That HTTP/WS query-string token transport was intentionally unchanged.

Do not commit unless the user explicitly asks for a commit.
