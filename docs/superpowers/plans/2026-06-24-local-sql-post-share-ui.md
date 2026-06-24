# Local SQL and Post Share UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete first-release local chat SQLite schema and add post-share-to-chat UI entry points.

**Architecture:** Because the app is not released, update the Drift v1 schema directly and regenerate `app_database.g.dart` instead of adding migration branches. Then add a reusable post-share sheet that lists recent private conversations and joined communities and sends `message_type=post` with `related_id`.

**Tech Stack:** Flutter, Drift, flutter_test, existing `ChatService` and `CommunityApiService`.

---

## Tasks

1. Add RED tests in `test/chat_message_types_contract_test.dart` for local SQL fields and post share entry points.
2. Update `lib/services/database/app_database.dart` v1 schema with complete message and last-message columns.
3. Regenerate `lib/services/database/app_database.g.dart` with build_runner.
4. Update `lib/services/local_db_service.dart` to persist and restore full message metadata and last-message metadata.
5. Add `relatedId` support to `CommunityApiService.sendMessage`.
6. Create `lib/widgets/post_share_to_chat_sheet.dart`.
7. Wire share entry points in `lib/screens/post/post_detail_screen.dart` and `lib/widgets/post_card.dart`.
8. Run focused Flutter tests and analyzer.

## File Map

- Modify: `D:/FlutterProject/nonto/lib/services/database/app_database.dart`
- Generated modify: `D:/FlutterProject/nonto/lib/services/database/app_database.g.dart`
- Modify: `D:/FlutterProject/nonto/lib/services/local_db_service.dart`
- Modify: `D:/FlutterProject/nonto/lib/services/api/community_service.dart`
- Create: `D:/FlutterProject/nonto/lib/widgets/post_share_to_chat_sheet.dart`
- Modify: `D:/FlutterProject/nonto/lib/screens/post/post_detail_screen.dart`
- Modify: `D:/FlutterProject/nonto/lib/widgets/post_card.dart`
- Modify test: `D:/FlutterProject/nonto/test/chat_message_types_contract_test.dart`

## Verification Commands

```bash
cd /d/FlutterProject/nonto && flutter test test/chat_message_types_contract_test.dart test/role_identity_contract_test.dart test/permissions_and_unread_regression_test.dart
cd /d/FlutterProject/nonto && flutter analyze
```

Expected: all tests pass and analyzer reports no issues.
