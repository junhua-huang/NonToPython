# Chat Message Types Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Standardize chat message types across backend and Flutter, supporting text, image, video, post cards with optional post image, and system-message UI while rejecting file/comment and user-created system messages.

**Architecture:** Add a backend message-type service as the single validation/normalization boundary, then route HTTP, WebSocket, and community chat sends through it. On Flutter, narrow `MessageType` to the product-supported set, add parsing compatibility, video send support, reusable preview/render helpers, post-card UI, and system-message UI.

**Tech Stack:** FastAPI, SQLAlchemy, Python unittest contracts; Flutter/Dart, Riverpod-style notifiers, flutter_test source/contract tests.

---

## File Map

### Backend

- Create: `D:/NanTuPy/app/services/message_type_service.py`
  - Owns message type constants, user payload validation, media normalization, post preview extraction.
- Modify: `D:/NanTuPy/app/models/models.py`
  - Update `Message.message_type` comment and optionally export constants import-safe if needed.
- Modify: `D:/NanTuPy/app/routers/chat.py`
  - Use message normalization for HTTP send and add `media_url` in batch response.
- Modify: `D:/NanTuPy/app/routers/ws.py`
  - Use message normalization for WebSocket validation and persistence.
- Modify: `D:/NanTuPy/app/routers/communities.py`
  - Replace community-specific whitelist with shared normalization.
- Test: `D:/NanTuPy/tests/test_chat_message_type_contracts.py`
  - New focused backend contract tests.
- Modify: `D:/NanTuPy/tests/test_community_chat_contracts.py`
  - Update source contract from `text/image/video` to shared `text/image/video/post`.
- Modify: `D:/NanTuPy/tests/test_ws_validation_unit.py`
  - Add illegal/system message validation tests.

### Flutter

- Modify: `D:/FlutterProject/nonto/lib/models/message.dart`
  - Narrow enum and add robust parsing helpers for `message_type`, `type`, `media_url`, `file_url`.
- Modify: `D:/FlutterProject/nonto/lib/providers/chat_notifiers.dart`
  - Add video send support, relatedId support for post messages, and preview labels.
- Modify: `D:/FlutterProject/nonto/lib/services/chat_send_queue.dart`
  - Pass `relatedId` into `WebSocketService.sendMessage`.
- Modify: `D:/FlutterProject/nonto/lib/screens/chat/chat_room_screen.dart`
  - Split selected image/video sends, add post-card and system-message rendering.
- Modify: `D:/FlutterProject/nonto/lib/screens/community/community_chat_screen.dart`
  - Add post-card and system-message rendering and preview labels.
- Modify: `D:/FlutterProject/nonto/lib/widgets/nonto/nonto_conversation_helpers.dart`
  - Use message type aware preview labels.
- Modify: `D:/FlutterProject/nonto/lib/services/local_db_service.dart`
  - Preserve `lastMessage.messageType` instead of forcing text.
- Test: `D:/FlutterProject/nonto/test/chat_message_types_contract_test.dart`
  - New Flutter contract tests for model parsing, preview, UI source hooks, video send, post card, and system UI.

---

## Task 1: Backend RED tests for the unified contract

**Files:**
- Create: `D:/NanTuPy/tests/test_chat_message_type_contracts.py`
- Modify: `D:/NanTuPy/tests/test_ws_validation_unit.py`
- Modify: `D:/NanTuPy/tests/test_community_chat_contracts.py`

- [ ] **Step 1: Create failing backend contract tests**

Create `D:/NanTuPy/tests/test_chat_message_type_contracts.py` with:

```python
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from fastapi import HTTPException

from app.services import message_type_service as mts


class ChatMessageTypeContractsTest(unittest.TestCase):
    def test_supported_message_type_sets_are_product_scope(self):
        self.assertEqual(mts.USER_MESSAGE_TYPES, {"text", "image", "video", "post"})
        self.assertEqual(mts.SYSTEM_MESSAGE_TYPES, {"system"})
        self.assertEqual(mts.MESSAGE_TYPES, {"text", "image", "video", "post", "system"})
        self.assertNotIn("file", mts.MESSAGE_TYPES)
        self.assertNotIn("comment", mts.MESSAGE_TYPES)

    def test_user_payload_rejects_unknown_and_system_types(self):
        with self.assertRaises(HTTPException) as unknown:
            mts.normalize_user_message_payload({"message_type": "file"}, Mock())
        self.assertEqual(unknown.exception.status_code, 400)

        with self.assertRaises(HTTPException) as system:
            mts.normalize_user_message_payload({"message_type": "system", "content": "fake"}, Mock())
        self.assertEqual(system.exception.status_code, 403)

    def test_text_requires_content_and_media_requires_url(self):
        with self.assertRaises(HTTPException) as empty_text:
            mts.normalize_user_message_payload({"message_type": "text", "content": "   "}, Mock())
        self.assertEqual(empty_text.exception.status_code, 400)

        with self.assertRaises(HTTPException) as empty_image:
            mts.normalize_user_message_payload({"message_type": "image", "content": "   "}, Mock())
        self.assertEqual(empty_image.exception.status_code, 400)

    def test_media_uses_content_as_legacy_url_when_media_url_missing(self):
        result = mts.normalize_user_message_payload(
            {"message_type": "video", "content": "https://cdn.example/video.mp4"},
            Mock(),
        )
        self.assertEqual(result["message_type"], "video")
        self.assertEqual(result["media_url"], "https://cdn.example/video.mp4")
        self.assertEqual(result["content"], "https://cdn.example/video.mp4")

    def test_post_requires_existing_post_and_builds_preview(self):
        post = SimpleNamespace(
            id=42,
            content="这是一个很长的帖子正文，用于生成聊天卡片摘要。",
            images='["https://cdn.example/p1.jpg", "https://cdn.example/p2.jpg"]',
        )
        query = Mock()
        query.filter.return_value.first.return_value = post
        db = Mock()
        db.query.return_value = query

        result = mts.normalize_user_message_payload(
            {"message_type": "post", "related_id": "42"},
            db,
        )

        self.assertEqual(result["message_type"], "post")
        self.assertEqual(result["related_id"], 42)
        self.assertEqual(result["content"], "这是一个很长的帖子正文，用于生成聊天卡片摘要。")
        self.assertEqual(result["media_url"], "https://cdn.example/p1.jpg")

    def test_post_missing_related_id_and_missing_post_are_rejected(self):
        with self.assertRaises(HTTPException) as missing:
            mts.normalize_user_message_payload({"message_type": "post"}, Mock())
        self.assertEqual(missing.exception.status_code, 400)

        query = Mock()
        query.filter.return_value.first.return_value = None
        db = Mock()
        db.query.return_value = query
        with self.assertRaises(HTTPException) as not_found:
            mts.normalize_user_message_payload({"message_type": "post", "related_id": 999}, db)
        self.assertEqual(not_found.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Add WebSocket validation tests**

Append to `D:/NanTuPy/tests/test_ws_validation_unit.py` inside `WebSocketValidationTests`:

```python
    def test_send_payload_rejects_unknown_message_type(self):
        payload = {"conversation_id": 10, "message_type": "file", "content": "x"}

        error = ws._validate_send_message_payload(payload)

        self.assertEqual(error, {"code": 400, "error": "不支持的消息类型"})

    def test_send_payload_rejects_user_created_system_message(self):
        payload = {"conversation_id": 10, "message_type": "system", "content": "fake"}

        error = ws._validate_send_message_payload(payload)

        self.assertEqual(error, {"code": 403, "error": "系统消息不能由用户发送"})
```

- [ ] **Step 3: Update community contract expectation**

Replace the old allowed-types source assertion in `D:/NanTuPy/tests/test_community_chat_contracts.py`:

```python
self.assertIn("allowed_types = {'text', 'image', 'video'}", source)
```

with:

```python
self.assertIn('normalize_user_message_payload', source)
self.assertIn('"post"', source)
```

- [ ] **Step 4: Run backend RED tests**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_chat_message_type_contracts tests.test_ws_validation_unit tests.test_community_chat_contracts
```

Expected: FAIL because `app.services.message_type_service` does not exist and route code still uses old validation.

---

## Task 2: Backend shared message-type service

**Files:**
- Create: `D:/NanTuPy/app/services/message_type_service.py`
- Modify: `D:/NanTuPy/app/models/models.py`

- [ ] **Step 1: Implement message type service**

Create `D:/NanTuPy/app/services/message_type_service.py`:

```python
import json
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import Post

USER_MESSAGE_TYPES = {"text", "image", "video", "post"}
SYSTEM_MESSAGE_TYPES = {"system"}
MESSAGE_TYPES = USER_MESSAGE_TYPES | SYSTEM_MESSAGE_TYPES
MEDIA_MESSAGE_TYPES = {"image", "video"}
CARD_MESSAGE_TYPES = {"post"}


def _clean_text(value: Any) -> str:
    return (value or "").strip()


def _parse_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_post_image(post: Post) -> Optional[str]:
    if not post.images:
        return None
    try:
        images = json.loads(post.images)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(images, list):
        return None
    for item in images:
        url = str(item or "").strip()
        if url:
            return url
    return None


def _post_preview_content(post: Post) -> str:
    content = _clean_text(post.content)
    if not content:
        return f"查看帖子 #{post.id}"
    return content[:60]


def normalize_user_message_payload(payload: dict, db: Session) -> dict:
    message_type = _clean_text(payload.get("message_type") or "text").lower()
    content = _clean_text(payload.get("content"))
    media_url = _clean_text(payload.get("media_url") or payload.get("file_url"))
    related_id = _parse_int(payload.get("related_id"))

    if message_type == "system":
        raise HTTPException(status_code=403, detail="系统消息不能由用户发送")
    if message_type not in USER_MESSAGE_TYPES:
        raise HTTPException(status_code=400, detail="不支持的消息类型")

    if message_type == "text":
        if not content:
            raise HTTPException(status_code=400, detail="消息内容不能为空")
        return {
            "content": content,
            "message_type": message_type,
            "media_url": None,
            "related_id": related_id,
        }

    if message_type in MEDIA_MESSAGE_TYPES:
        if not media_url and content.startswith(("http://", "https://")):
            media_url = content
        if not media_url:
            raise HTTPException(status_code=400, detail="媒体消息不能为空")
        return {
            "content": content or media_url,
            "message_type": message_type,
            "media_url": media_url,
            "related_id": related_id,
        }

    if message_type == "post":
        if related_id is None:
            raise HTTPException(status_code=400, detail="帖子消息缺少 related_id")
        post = db.query(Post).filter(Post.id == related_id).first()
        if not post:
            raise HTTPException(status_code=404, detail="帖子不存在")
        return {
            "content": content or _post_preview_content(post),
            "message_type": message_type,
            "media_url": media_url or _first_post_image(post),
            "related_id": related_id,
        }

    raise HTTPException(status_code=400, detail="不支持的消息类型")
```

- [ ] **Step 2: Update model comment**

In `D:/NanTuPy/app/models/models.py`, change:

```python
message_type = Column(String(20), default='text')  # text, image, system
```

to:

```python
message_type = Column(String(20), default='text')  # text, image, video, post, system
```

- [ ] **Step 3: Run backend service tests**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_chat_message_type_contracts
```

Expected: PASS.

---

## Task 3: Integrate backend routes

**Files:**
- Modify: `D:/NanTuPy/app/routers/chat.py`
- Modify: `D:/NanTuPy/app/routers/ws.py`
- Modify: `D:/NanTuPy/app/routers/communities.py`

- [ ] **Step 1: Integrate HTTP chat send**

In `D:/NanTuPy/app/routers/chat.py`, import:

```python
from app.services.message_type_service import normalize_user_message_payload
```

Replace manual payload extraction in `send_message` with:

```python
    normalized = normalize_user_message_payload(payload, db)
    content = normalized["content"]
    message_type = normalized["message_type"]
    media_url = normalized["media_url"]
    related_id = normalized["related_id"]
```

Remove the old `if not content and message_type == "text"` block.

- [ ] **Step 2: Add `media_url` to batch response**

In the batch messages response object in `D:/NanTuPy/app/routers/chat.py`, add `media_url` while keeping `file_url` compatibility:

```python
"media_url": msg.media_url,
"file_url": msg.media_url,
```

- [ ] **Step 3: Integrate WebSocket validation**

In `D:/NanTuPy/app/routers/ws.py`, import:

```python
from app.services.message_type_service import normalize_user_message_payload
```

Change `_validate_send_message_payload(payload)` to call the shared service after conversation/receiver presence validation:

```python
    try:
        normalize_user_message_payload(payload, db=None)
    except HTTPException as exc:
        return {"code": exc.status_code, "error": exc.detail}
```

Because `post` requires DB, keep `_validate_send_message_payload` as lightweight and also do full validation in `_handle_send_message` where DB exists. If `message_type == "post"`, skip lightweight service validation here and let `_handle_send_message` validate it.

- [ ] **Step 4: Integrate WebSocket persistence validation**

Inside `_handle_send_message`, after DB session and before creating `Message`, call:

```python
            normalized = normalize_user_message_payload(payload, db)
            content = normalized["content"]
            media_url = normalized["media_url"]
            related_id = normalized["related_id"]
            message_type = normalized["message_type"]
```

Use these normalized values in the `Message(...)` constructor.

- [ ] **Step 5: Integrate community chat send**

In `D:/NanTuPy/app/routers/communities.py`, import:

```python
from app.services.message_type_service import normalize_user_message_payload
```

Replace `_normalize_community_message_payload` internals with:

```python
def _normalize_community_message_payload(payload: dict, db: Session):
    normalized = normalize_user_message_payload(payload, db)
    mention_user_ids = payload.get("mention_user_ids", [])
    if not isinstance(mention_user_ids, list):
        mention_user_ids = []
    return (
        normalized["content"],
        normalized["message_type"],
        normalized["media_url"],
        mention_user_ids,
    )
```

Change caller to pass `db`:

```python
    content, message_type, media_url, mention_user_ids = (
        _normalize_community_message_payload(payload, db)
    )
```

- [ ] **Step 6: Run backend route tests**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_chat_message_type_contracts tests.test_ws_validation_unit tests.test_community_chat_contracts
```

Expected: PASS.

---

## Task 4: Flutter RED tests

**Files:**
- Create: `D:/FlutterProject/nonto/test/chat_message_types_contract_test.dart`

- [ ] **Step 1: Create Flutter contract tests**

Create `D:/FlutterProject/nonto/test/chat_message_types_contract_test.dart`:

```dart
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:nonto/models/message.dart';
import 'package:nonto/models/conversation.dart';
import 'package:nonto/widgets/nonto/nonto_conversation_helpers.dart';

void main() {
  group('chat message type contracts', () {
    test('MessageType only exposes supported product types', () {
      expect(MessageType.values.map((e) => e.name),
          ['text', 'image', 'video', 'post', 'system']);
    });

    test('Message parses message type and media url compatibility fields', () {
      final fromFileUrl = Message.fromJson({
        'id': 1,
        'conversation_id': 2,
        'sender_id': 3,
        'message_type': 'image',
        'file_url': 'https://cdn.example/a.jpg',
      });
      expect(fromFileUrl.messageType, MessageType.image);
      expect(fromFileUrl.mediaUrl, 'https://cdn.example/a.jpg');

      final fromLegacyType = Message.fromJson({
        'id': 2,
        'conversation_id': 2,
        'sender_id': 3,
        'type': 'video',
        'media_url': 'https://cdn.example/v.mp4',
      });
      expect(fromLegacyType.messageType, MessageType.video);
    });

    test('unknown legacy file and comment message types fall back to text', () {
      final file = Message.fromJson({
        'id': 1,
        'conversation_id': 2,
        'sender_id': 3,
        'message_type': 'file',
      });
      final comment = Message.fromJson({
        'id': 2,
        'conversation_id': 2,
        'sender_id': 3,
        'message_type': 'comment',
      });

      expect(file.messageType, MessageType.text);
      expect(comment.messageType, MessageType.text);
    });

    test('conversation preview labels media post and system messages', () {
      Conversation convWith(Message message) => Conversation(
            id: 1,
            user1Id: 1,
            user2Id: 2,
            lastMessage: message,
          );

      expect(nontoConversationPreview(convWith(Message(
        id: 1,
        conversationId: 1,
        senderId: 1,
        content: 'https://cdn.example/a.jpg',
        messageType: MessageType.image,
      ))), '[图片]');
      expect(nontoConversationPreview(convWith(Message(
        id: 2,
        conversationId: 1,
        senderId: 1,
        content: 'https://cdn.example/v.mp4',
        messageType: MessageType.video,
      ))), '[视频]');
      expect(nontoConversationPreview(convWith(Message(
        id: 3,
        conversationId: 1,
        senderId: 1,
        content: '帖子摘要',
        messageType: MessageType.post,
      ))), '[帖子] 帖子摘要');
      expect(nontoConversationPreview(convWith(Message(
        id: 4,
        conversationId: 1,
        senderId: 1,
        content: '欢迎加入',
        messageType: MessageType.system,
      ))), '欢迎加入');
    });

    test('private chat source contains video send and post/system render hooks', () {
      final source = File('lib/screens/chat/chat_room_screen.dart').readAsStringSync();
      expect(source, contains('sendVideoMessage'));
      expect(source, contains('_buildPostCardBubble'));
      expect(source, contains('_buildSystemMessage'));
    });

    test('community chat source contains post/system render hooks', () {
      final source = File('lib/screens/community/community_chat_screen.dart').readAsStringSync();
      expect(source, contains('_buildPostCard'));
      expect(source, contains('_buildSystemMessage'));
    });

    test('send queue passes relatedId to websocket', () {
      final source = File('lib/services/chat_send_queue.dart').readAsStringSync();
      expect(source, contains('relatedId: msg.relatedId'));
    });

    test('local database restore preserves last message type', () {
      final source = File('lib/services/local_db_service.dart').readAsStringSync();
      expect(source, isNot(contains('messageType: MessageType.text,')));
      expect(source, contains('messageType: MessageType.values.firstWhere'));
    });
  });
}
```

- [ ] **Step 2: Run Flutter RED tests**

Run:

```bash
cd /d/FlutterProject/nonto && flutter test test/chat_message_types_contract_test.dart
```

Expected: FAIL because enum still contains `file/comment`, parsing does not use `file_url`, and UI hooks are missing.

---

## Task 5: Flutter model and preview helpers

**Files:**
- Modify: `D:/FlutterProject/nonto/lib/models/message.dart`
- Modify: `D:/FlutterProject/nonto/lib/widgets/nonto/nonto_conversation_helpers.dart`
- Modify: `D:/FlutterProject/nonto/lib/services/local_db_service.dart`

- [ ] **Step 1: Update MessageType and parsing**

In `D:/FlutterProject/nonto/lib/models/message.dart`, change enum to:

```dart
enum MessageType { text, image, video, post, system }
```

Add helper inside `Message`:

```dart
  static MessageType _messageTypeFromJson(Map<String, dynamic> json) {
    final raw = (json['message_type'] ?? json['type'] ?? 'text').toString();
    return MessageType.values.firstWhere(
      (e) => e.name == raw,
      orElse: () => MessageType.text,
    );
  }
```

Use it in `fromJson`:

```dart
messageType: _messageTypeFromJson(json),
mediaUrl: json['media_url']?.toString() ?? json['file_url']?.toString(),
```

Remove getters for `isFile` and `isCommentCard`; keep:

```dart
  bool get isPostCard => messageType == MessageType.post;
  bool get isSystem => messageType == MessageType.system;
```

- [ ] **Step 2: Make conversation preview type-aware**

In `D:/FlutterProject/nonto/lib/widgets/nonto/nonto_conversation_helpers.dart`, implement:

```dart
String nontoConversationPreview(Conversation conversation) {
  final last = conversation.lastMessage;
  if (last == null) return '';
  if (last.isRecalled) return '消息已撤回';
  final content = last.content?.trim() ?? '';
  switch (last.messageType) {
    case MessageType.image:
      return '[图片]';
    case MessageType.video:
      return '[视频]';
    case MessageType.post:
      return content.isEmpty ? '[帖子]' : '[帖子] $content';
    case MessageType.system:
      return content;
    case MessageType.text:
      return content;
  }
}
```

- [ ] **Step 3: Preserve lastMessage type from local DB**

In `D:/FlutterProject/nonto/lib/services/local_db_service.dart`, replace forced `MessageType.text` in conversation lastMessage restore with:

```dart
messageType: MessageType.values.firstWhere(
  (e) => e.name == row.lastMessageType,
  orElse: () => MessageType.text,
),
```

If the row does not have `lastMessageType`, use the existing stored message type field available in that method.

- [ ] **Step 4: Run Flutter model tests**

Run:

```bash
cd /d/FlutterProject/nonto && flutter test test/chat_message_types_contract_test.dart
```

Expected: Remaining failures only for video send, post/system UI hooks, and queue relatedId if not implemented yet.

---

## Task 6: Flutter send path for video and post metadata

**Files:**
- Modify: `D:/FlutterProject/nonto/lib/providers/chat_notifiers.dart`
- Modify: `D:/FlutterProject/nonto/lib/services/chat_send_queue.dart`
- Modify: `D:/FlutterProject/nonto/lib/screens/chat/chat_room_screen.dart`

- [ ] **Step 1: Pass relatedId through send queue**

In `D:/FlutterProject/nonto/lib/services/chat_send_queue.dart`, add to `WebSocketService.sendMessage` call:

```dart
relatedId: msg.relatedId,
```

- [ ] **Step 2: Allow generic sendMessage to include relatedId**

In `ChatNotifier.sendMessage`, add parameter:

```dart
int? relatedId,
```

Set it on optimistic message:

```dart
relatedId: relatedId,
```

- [ ] **Step 3: Add sendVideoMessage**

In `D:/FlutterProject/nonto/lib/providers/chat_notifiers.dart`, add method modeled on `sendImageMessage` but using video values:

```dart
  Future<void> sendVideoMessage(Uint8List bytes, String fileName) async {
    if (_currentUserId == null) return;
    final requestId = _generateRequestId();
    final now = DateTime.now();
    final optimisticMsg = Message(
      id: now.millisecondsSinceEpoch,
      conversationId: conversationId,
      senderId: _currentUserId!,
      content: '视频',
      messageType: MessageType.video,
      createdAt: now,
      requestId: requestId,
      status: 'uploading',
      tempBytes: bytes,
    );

    state = state.copyWith(messages: [...state.messages, optimisticMsg], isSending: true);
    DataLayer().persistMessage(optimisticMsg);

    try {
      final uploadResp = await ApiClient().uploadBytes('/upload/chat/video', bytes, fileName);
      if (uploadResp.success) {
        final url = _extractUrl(uploadResp.data);
        if (url != null) {
          final queuedMsg = optimisticMsg.copyWith(
            content: url,
            mediaUrl: url,
            status: 'sending',
            uploadProgress: 1.0,
            clearTempBytes: true,
          );
          state = state.copyWith(
            messages: state.messages.map((m) => m.id == optimisticMsg.id ? queuedMsg : m).toList(),
            isSending: true,
          );
          await DataLayer().persistMessage(queuedMsg);
          _syncL1();
          SoundService().playSendSound();
          _onMessageSent?.call(conversationId, '视频', 'video', now);
          _sendQueue.enqueue(queuedMsg);
        }
      } else {
        _markUploadFailed(optimisticMsg.id, bytes);
      }
    } catch (e) {
      debugPrint('Send video error: $e');
      _markUploadFailed(optimisticMsg.id, bytes);
    }
  }
```

- [ ] **Step 4: Route selected videos to sendVideoMessage**

In `D:/FlutterProject/nonto/lib/screens/chat/chat_room_screen.dart`, in media selection handling, detect extensions:

```dart
bool _isVideoFileName(String name) {
  final lower = name.toLowerCase();
  return lower.endsWith('.mp4') || lower.endsWith('.mov') || lower.endsWith('.avi') || lower.endsWith('.mkv');
}
```

When iterating selected media:

```dart
if (_isVideoFileName(file.name)) {
  await notifier.sendVideoMessage(bytes, file.name);
} else {
  await notifier.sendImageMessage(bytes, file.name);
}
```

- [ ] **Step 5: Run Flutter tests**

Run:

```bash
cd /d/FlutterProject/nonto && flutter test test/chat_message_types_contract_test.dart
```

Expected: UI hook tests still fail until Task 7.

---

## Task 7: Flutter post-card and system-message UI

**Files:**
- Modify: `D:/FlutterProject/nonto/lib/screens/chat/chat_room_screen.dart`
- Modify: `D:/FlutterProject/nonto/lib/screens/community/community_chat_screen.dart`

- [ ] **Step 1: Add private chat system message rendering**

In `_buildBubble` before normal bubble rendering, add:

```dart
    if (msg.messageType == MessageType.system) {
      return _buildSystemMessage(msg.content ?? '');
    }
```

Add helper:

```dart
  Widget _buildSystemMessage(String text) {
    return Center(
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 8),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        decoration: BoxDecoration(
          color: _isDark ? Colors.white10 : Colors.black.withValues(alpha: 0.06),
          borderRadius: BorderRadius.circular(14),
        ),
        child: Text(
          text,
          style: TextStyle(
            fontSize: 12,
            color: _isDark ? Colors.white60 : Colors.black54,
          ),
        ),
      ),
    );
  }
```

- [ ] **Step 2: Add private chat post-card rendering**

In `_buildBubble` content branch, add before media fallback:

```dart
                  else if (msg.messageType == MessageType.post)
                    _buildPostCardBubble(msg, isMe)
```

Add helper:

```dart
  Widget _buildPostCardBubble(Message msg, bool isMe) {
    final title = (msg.content?.trim().isNotEmpty == true) ? msg.content!.trim() : '查看帖子 #${msg.relatedId ?? ''}';
    return InkWell(
      onTap: msg.relatedId == null
          ? null
          : () => Navigator.pushNamed(context, '/posts/${msg.relatedId}'),
      child: Container(
        constraints: const BoxConstraints(maxWidth: 260),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (msg.mediaUrl?.isNotEmpty == true) ...[
              ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: CachedNetworkImage(
                  imageUrl: msg.mediaUrl!,
                  width: 72,
                  height: 72,
                  fit: BoxFit.cover,
                ),
              ),
              const SizedBox(width: 10),
            ],
            Flexible(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                mainAxisSize: MainAxisSize.min,
                children: [
                  Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.article_outlined, size: 15, color: isMe ? Colors.white70 : _NontoChatColors.timestamp),
                      const SizedBox(width: 4),
                      Text('帖子', style: TextStyle(fontSize: 12, color: isMe ? Colors.white70 : _NontoChatColors.timestamp)),
                    ],
                  ),
                  const SizedBox(height: 5),
                  Text(title, maxLines: 2, overflow: TextOverflow.ellipsis, style: TextStyle(fontSize: 14, color: isMe ? Colors.white : _NontoChatColors.text)),
                  const SizedBox(height: 4),
                  Text('点击查看详情', style: TextStyle(fontSize: 12, color: isMe ? Colors.white70 : _NontoChatColors.timestamp)),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
```

If route naming differs, replace `Navigator.pushNamed(context, '/posts/${msg.relatedId}')` with the project's existing post detail route constant.

- [ ] **Step 3: Add community system and post card hooks**

In `D:/FlutterProject/nonto/lib/screens/community/community_chat_screen.dart`, in `_MessageBubble`, branch on `message_type == 'system'` and `message_type == 'post'`.

Add source-level helpers named exactly:

```dart
Widget _buildSystemMessage(String text) { ... }
Widget _buildPostCard(Map<String, dynamic> message) { ... }
```

Use `media_url` for optional image and `related_id` for post navigation.

- [ ] **Step 4: Run Flutter contract tests**

Run:

```bash
cd /d/FlutterProject/nonto && flutter test test/chat_message_types_contract_test.dart
```

Expected: PASS.

---

## Task 8: Full verification

**Files:**
- No new edits unless verification finds issues.

- [ ] **Step 1: Run backend focused tests**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -m unittest tests.test_chat_message_type_contracts tests.test_ws_validation_unit tests.test_community_chat_contracts tests.test_identity_role_contracts tests.test_push_device_state_contracts tests.test_notification_service_unit
```

Expected: all tests pass.

- [ ] **Step 2: Run backend import smoke**

Run:

```bash
cd /d/NanTuPy && ./.venv/Scripts/python.exe -c "from app.services.message_type_service import MESSAGE_TYPES, normalize_user_message_payload; from app.routers.chat import router as chat_router; from app.routers.ws import _validate_send_message_payload; from app.routers.communities import router as community_router; print('chat message type imports ok')"
```

Expected: `chat message type imports ok`.

- [ ] **Step 3: Run Flutter focused tests**

Run:

```bash
cd /d/FlutterProject/nonto && flutter test test/chat_message_types_contract_test.dart test/role_identity_contract_test.dart test/permissions_and_unread_regression_test.dart
```

Expected: all tests pass.

- [ ] **Step 4: Run Flutter analyzer**

Run:

```bash
cd /d/FlutterProject/nonto && flutter analyze
```

Expected: `No issues found!`.

---

## Plan Self-Review

- Spec coverage: The plan covers unified backend constants, illegal/system rejection, media normalization, post card with optional post image, `media_url` compatibility, Flutter enum parsing, private video sending, post/system UI, preview labels, and local lastMessage type preservation.
- Placeholder scan: No TODO/TBD placeholders remain. The only route-name caveat in Task 7 explicitly tells the implementer to use the existing project route if the literal route differs.
- Type consistency: Backend normalized dict keys are `content`, `message_type`, `media_url`, `related_id` and are used consistently in route tasks. Flutter enum names match the spec: `text`, `image`, `video`, `post`, `system`.
