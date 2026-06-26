# Chat Reply Display and Community Unread Bugs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix quoted-reply metadata loss in chat batch preload and clear community unread counts when opening group chat.

**Architecture:** Keep the fix small and contract-driven. Backend batch message responses must preserve the same quote fields as normal message endpoints. Backend mark-read must authorize community conversation participants as well as direct chat users. Flutter community chat should mark the loaded conversation read and clear the local unread badge just like direct chat.

**Tech Stack:** FastAPI, SQLAlchemy, Python unittest/pytest contract tests; Flutter/Dart, Riverpod, source/contract tests, `flutter analyze`.

---

## File Map

- Modify: `D:/NanTuPy/tests/test_community_chat_contracts.py`
  - Add backend source-contract tests for batch quote fields and community mark-read authorization.
- Modify: `D:/NanTuPy/app/routers/chat.py`
  - Add quote fields to `/chat/messages/batch` SQL and response.
  - Authorize community conversation participants in `/chat/conversations/{conversation_id}/mark-read`.
  - Include direct and community conversations when calculating remaining unread count.
- Create: `D:/FlutterProject/nonto/test/community_unread_contract_test.dart`
  - Add frontend source-contract tests proving community chat marks read and clears local unread.
- Modify: `D:/FlutterProject/nonto/lib/screens/community/community_chat_screen.dart`
  - Mark conversation read after resolving `_conversationId`.
  - Clear local unread badge immediately.

---

## Task 1: Backend RED tests

- [ ] Add tests to `D:/NanTuPy/tests/test_community_chat_contracts.py`:
  - `test_batch_messages_preserves_quote_fields_for_reply_display`
  - `test_mark_read_authorizes_community_participants`
  - `test_mark_read_total_unread_includes_community_membership`
- [ ] Run: `cd /d/NanTuPy && python -m pytest tests/test_community_chat_contracts.py -q`
- [ ] Confirm the new tests fail because current source does not include the required fields/authorization.

## Task 2: Backend GREEN implementation

- [ ] Update `D:/NanTuPy/app/routers/chat.py` batch SQL SELECT and response to include `quote_message_id`, `quote_preview`, `is_recalled`.
- [ ] Run batch response messages through `inject_quote_preview_batch(db, ...)` before returning.
- [ ] Update mark-read authorization to allow either direct participants or `ConversationParticipant` rows.
- [ ] Update total unread query to include direct conversations and community conversations where the user has active `CommunityMember` membership.
- [ ] Run backend focused tests and confirm green.

## Task 3: Frontend RED tests

- [ ] Create `D:/FlutterProject/nonto/test/community_unread_contract_test.dart` with source-contract tests that assert `CommunityChatScreen` calls `markConversationRead`, HTTP fallback `ChatService().markRead`, and `clearConversationUnread` after `_conversationId` is resolved.
- [ ] Run: `cd /d/FlutterProject/nonto && flutter test test/community_unread_contract_test.dart`
- [ ] Confirm the test fails before frontend implementation.

## Task 4: Frontend GREEN implementation

- [ ] Add `ChatService` import to `community_chat_screen.dart`.
- [ ] Add helper `_markConversationRead()` that:
  - returns if `_conversationId == null`
  - if WS is connected, calls `WebSocketService().markConversationRead(_conversationId!)`
  - otherwise calls `ChatService().markRead(_conversationId!)`
  - always calls `ref.read(conversationsProvider.notifier).clearConversationUnread(_conversationId!)`
- [ ] Invoke `_markConversationRead()` immediately after joining/setting current conversation.
- [ ] Run frontend focused test and confirm green.

## Task 5: Verification

- [ ] Run: `cd /d/NanTuPy && python -m pytest tests/test_community_chat_contracts.py tests/test_chat_around_contracts.py tests/test_quote_service_contracts.py -q`
- [ ] Run: `cd /d/FlutterProject/nonto && flutter test test/community_unread_contract_test.dart`
- [ ] Run: `cd /d/FlutterProject/nonto && flutter analyze`
- [ ] Report exact results and any pre-existing failures.
