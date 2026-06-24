# Role Identity System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement manually reviewed, verified-only business identity labels for users and posts without changing publishing permissions.

**Architecture:** Reuse the existing `Role`, `UserRole`, and `RoleApplication` tables as the identity source, separating business identities from system permissions. Posts may store one optional verified business identity in `display_role_type`; invalid or unverified requests are saved as `NULL` so publishing always succeeds.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Python unittest, Flutter/Dart, flutter_test.

---

## File Structure

- Backend model/schema changes:
  - `D:\NanTuPy\app\models\models.py`: role constants/helpers, unified application fields, verified public role serialization, post identity fields.
  - `D:\NanTuPy\app\routers\roles.py`: role list, apply/review endpoints, verified/suspended semantics.
  - `D:\NanTuPy\app\routers\admin.py`: keep duplicate admin review endpoints aligned.
  - `D:\NanTuPy\app\routers\posts.py`: accept `content_category` and `display_role_type` form fields and validate against verified identities.
  - `D:\NanTuPy\alembic\versions\2026_06_23_1500-add_role_identity_system.py`: add missing role tables if needed, add role-application unified fields, add post identity fields, seed business/system roles.
  - `D:\NanTuPy\tests\test_identity_role_contracts.py`: backend contract tests.
- Flutter changes:
  - `D:\FlutterProject\nonto\lib\models\user.dart`: parse verified identity roles.
  - `D:\FlutterProject\nonto\lib\models\post.dart`: parse post display identity/category fields.
  - `D:\FlutterProject\nonto\lib\services\api\post_service.dart`: send optional category/display role.
  - `D:\FlutterProject\nonto\lib\services\api\role_service.dart`: apply/list identity APIs.
  - `D:\FlutterProject\nonto\lib\widgets\identity_badge.dart`: reusable verified identity badge.
  - `D:\FlutterProject\nonto\lib\widgets\post_card.dart`: display one post identity badge.
  - `D:\FlutterProject\nonto\lib\screens\post\create_post_screen.dart`: add identity selector with “不展示身份”.
  - `D:\FlutterProject\nonto\lib\screens\profile\profile_tab.dart` and `D:\FlutterProject\nonto\lib\screens\profile\user_profile_screen.dart`: display multiple verified identities.
  - `D:\FlutterProject\nonto\test\role_identity_contract_test.dart`: Flutter contract tests.

## Tasks

### Task 1: Backend RED tests
- [ ] Add tests asserting: public user serialization hides admin/unverified roles, verified business identities are exposed, post display role is only saved for verified identities, role applications support unified template fields, and review uses `verified/rejected/suspended`.
- [ ] Run focused unittest and confirm failures are due to missing fields/behavior.

### Task 2: Backend implementation
- [ ] Add role constants/helpers and update model serialization.
- [ ] Add nullable post `content_category` and `display_role_type` fields.
- [ ] Add unified role-application fields while keeping `reason` compatible.
- [ ] Update role apply/review/admin endpoints.
- [ ] Update post creation validation so invalid role labels become `NULL`, never blocking publish.
- [ ] Add Alembic migration for schema and role seeds.
- [ ] Run backend focused tests until green.

### Task 3: Flutter RED tests
- [ ] Add widget/model/service static tests for parsing verified identities, parsing post display role, rendering identity badge, and sending category/display role.
- [ ] Run focused flutter tests and confirm failures.

### Task 4: Flutter implementation
- [ ] Add identity parsing helpers to user/post models.
- [ ] Update post creation service and create-post screen.
- [ ] Add badge widget and render it on post cards/profile screens.
- [ ] Add role-application API service with unified payload.
- [ ] Run focused flutter tests until green.

### Task 5: Verification
- [ ] Run backend unittest for new contracts and existing notification tests.
- [ ] Run backend import smoke and Alembic heads.
- [ ] Run Flutter focused tests and `flutter analyze`.
- [ ] Report exact verification results and any skipped scope.
