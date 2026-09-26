# Content Moderation Phase 2 Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在阶段 1 统一文本审核已经通过门禁的前提下，实现审核案件、内容版本、任务队列、举报、申诉、处罚、管理员治理、用户端状态展示和独立 Worker 的完整治理闭环。

**Architecture:** FastAPI 单体继续作为业务 API，审核领域拆为独立 ORM 模型、状态机、Repository、目标适配器和治理服务；所有状态变化、版本应用、违规累计和举报结案在单个数据库事务中完成，外部副作用只在事务成功后触发。Flutter 保持现有 Dio、不可变模型和 Riverpod 模式，消费稳定审核状态与错误码；本阶段只建设数据库 Worker 基础和用户侧“我的审核”，不实现腾讯云 Provider、COS 隔离上传或管理员 UI。

**Tech Stack:** Python 3, FastAPI, SQLAlchemy 2.x synchronous ORM, Alembic, MySQL/PyMySQL, Pydantic 2, pytest, Flutter/Dart, Dio, Riverpod StateNotifier, Reliable WebSocket client, flutter_test

---

## Scope and execution rules

- 阶段 1 是硬前置，但 Task 1 必须在阶段 1 的**同一个** `ModerationService`、`ModerationPolicy` 和 `app/services/moderation_types.py` 上扩展公开内容处置契约；不得创建第二个 route-facing service、第二套本地审核器或兼容包装层。
- 后端命令从 `D:\NanTuPy`（Git Bash 为 `/d/NanTuPy`）运行；Flutter 命令从 `D:\FlutterProject\nonto`（Git Bash 为 `/d/FlutterProject/nonto`）运行。
- Repository 和 service 只执行 `flush()`，不在内部 `commit()`；路由或 Worker 拥有事务边界。任务 claim 先提交，完成时在新事务中用 claim token 条件更新。
- 阶段 2 不调用腾讯云、不安装/调用腾讯云 SDK、不创建上传会话/COS 对象表，也不改造媒体上传路径。`PolicyDisposition.PROVIDER_REVIEW` 在 Provider 能力未启用时必须原子降级为 `manual_review` 并创建人工案件，且**不创建、不认领、不消费云任务**；阶段 3 才注册腾讯 Provider 并创建/处理 Provider 任务。
- 实时私聊和实时社群文本只调用 `moderate_realtime_text()`，该方法只做本地同步审核并绝不路由 Provider。公开文本只调用 `moderate_public_text()`，由其返回稳定的策略处置。
- 所有公开列表、搜索、推荐、fanout、通知、推送、话题、提及和计数副作用只读取已批准版本。待审创建仅作者可见；待审编辑继续公开旧批准版本。
- 阶段 2 只建立治理字段、violation/compensation 不可变账本、风险 profile、`last_confirmed_violation_at` 和无副作用纯函数。生产 retention cleanup 与每满 30 天 risk decay 的调度、账本及执行唯一由阶段 4 实现。
- 阶段 1 已提供 Flutter `ApiResponse`、`ChatSendFailure`、Message `failureCode/failureMessage`、Drift failure 列及 v1→v2 migration；阶段 2 只复用，不重复定义、不新增 failure 列、不提升 `schemaVersion`。
- 普通用户账户停用统一为 HTTP 403 `ACCOUNT_DISABLED`；发布限制统一为 HTTP 403 `PUBLISH_RESTRICTED`。不得引入其他停用账户错误码。
- 本阶段只实现管理员治理 API 和用户侧“我的审核”；不创建任何管理员 Flutter 页面、route、provider 或 widget。
- 每个任务完成后只提交该任务列出的文件。执行本计划时允许提交；编写本计划本身不提交。

## Locked file structure and interfaces

### Backend files

- `app/models/moderation.py`: `ModerationCase`、`ModerationTask`、`ContentRevision`、`ModerationAction`、`UserViolation`、`UserViolationCompensation`、`UserModerationProfile`、`ModerationAppeal`、`ModerationWorkerHeartbeat`、`ModerationPolicyConfig` ORM。
- `app/services/moderation_types.py`: 阶段 1–3 共用的唯一枚举、DTO 和任务归一化边界；路由、repository 和 Worker 不使用裸字符串分支。
- `app/services/moderation_state_machine.py`: 纯函数案件状态机，不访问数据库。
- `app/services/moderation_repository.py`: 案件/版本/任务认领、条件完成、动作追加、违规和举报的原子持久化。
- `app/services/moderation_targets.py`: 目标类型注册表与各业务表的存在性、所有者、可见性、隐藏、恢复、版本读写白名单。
- `app/services/moderation_governance_service.py`: 编排公开内容创建/编辑、审核结果应用、管理员操作、申诉和一次性副作用事件。
- `app/services/report_service.py`: 单一举报受理入口，完成目标鉴权、重复约束和案件关联。
- `app/services/publish_restrictions.py`: 违规分值、限制状态和期限计算的纯函数，以及追加 violation/compensation 后的 profile 更新；不包含 decay job。
- `app/services/moderation_effects.py`: 审核通过后在新事务内原子执行话题、提及通知记录、计数与唯一 `effect:<name>` 动作；提交后再触发使用既有投递幂等机制的推送/fanout。
- `app/routers/admin_moderation.py`: `/api/admin/moderation`、`/api/admin/reports`、`/api/admin/users` 治理 API；不包含 UI。
- `app/routers/moderation.py`: `/api/moderation/me/*` 用户案件、申诉和限制 API。
- `app/workers/moderation_worker.py`: 独立 Worker CLI、租约循环、退避和心跳；阶段 2 不注册腾讯 processor 或 maintenance processor。
- `deploy/moderation-worker-entrypoint.sh`: 不执行 Alembic 的 Worker 进程入口。

### Canonical backend contracts

```python
class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

SEVERITY_POINTS: Mapping[Severity, int] = {
    Severity.LOW: 1,
    Severity.MEDIUM: 3,
    Severity.HIGH: 8,
}

class PolicyDisposition(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    PROVIDER_REVIEW = "provider_review"
    MANUAL_REVIEW = "manual_review"

class CaseStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MANUAL_REVIEW = "manual_review"
    CANCELLED = "cancelled"

class PublicModerationStatus(StrEnum):
    APPROVED = "approved"
    PENDING_REVIEW = "pending_review"
    REJECTED = "rejected"

class ModerationTaskType(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    VIDEO_POLL = "video_poll"

class ModerationCaseSource(StrEnum):
    PUBLIC_CREATE = "public_create"
    PUBLIC_EDIT = "public_edit"
    REALTIME_REJECTION = "realtime_rejection"
    REPORT = "report"
    ADMIN = "admin"
    MEDIA_UPLOAD = "media_upload"
    BACKFILL = "backfill"

class ModerationProvider(StrEnum):
    NONE = "none"
    TENCENT = "tencent"

class TaskStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"

class AppealStatus(StrEnum):
    MANUAL_REVIEW = "manual_review"
    APPROVED = "approved"
    REJECTED = "rejected"

class NormalizedTaskDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    MANUAL_REVIEW = "manual_review"

class RestrictionScope(StrEnum):
    POST = "post"
    COMMENT = "comment"
    CHAT = "chat"
    ALL_CONTENT = "all_content"

@dataclass(frozen=True, slots=True)
class PublicTextModerationOutcome:
    disposition: PolicyDisposition
    result: ModerationResult

@dataclass(frozen=True, slots=True)
class NormalizedTaskResult:
    decision: NormalizedTaskDecision
    risk_category: RiskCategory
    severity: Severity | None
    confidence: float
    policy_version: str
    rule_version: int
    provider: ModerationProvider
    provider_request_id: str | None = None
    provider_job_id: str | None = None

class ProviderStillProcessing(Exception):
    def __init__(self, *, provider_job_id: str, retry_after_seconds: int):
        self.provider_job_id = provider_job_id
        self.retry_after_seconds = retry_after_seconds
        super().__init__("provider result still processing")

PHASE2_DISPOSITION_TO_CASE_STATUS: Mapping[PolicyDisposition, CaseStatus] = {
    PolicyDisposition.APPROVED: CaseStatus.APPROVED,
    PolicyDisposition.REJECTED: CaseStatus.REJECTED,
    PolicyDisposition.PROVIDER_REVIEW: CaseStatus.MANUAL_REVIEW,
    PolicyDisposition.MANUAL_REVIEW: CaseStatus.MANUAL_REVIEW,
}

NORMALIZED_TASK_TO_CASE_STATUS: Mapping[NormalizedTaskDecision, CaseStatus] = {
    NormalizedTaskDecision.APPROVED: CaseStatus.APPROVED,
    NormalizedTaskDecision.REJECTED: CaseStatus.REJECTED,
    NormalizedTaskDecision.MANUAL_REVIEW: CaseStatus.MANUAL_REVIEW,
}

CASE_TO_PUBLIC_STATUS: Mapping[CaseStatus, PublicModerationStatus] = {
    CaseStatus.APPROVED: PublicModerationStatus.APPROVED,
    CaseStatus.REJECTED: PublicModerationStatus.REJECTED,
    CaseStatus.PENDING: PublicModerationStatus.PENDING_REVIEW,
    CaseStatus.MANUAL_REVIEW: PublicModerationStatus.PENDING_REVIEW,
}

@dataclass(frozen=True, slots=True)
class ClaimedTask:
    task_id: int
    case_id: int
    task_type: ModerationTaskType
    provider: ModerationProvider
    claim_token: str
    attempt_count: int
    lease_expires_at: datetime

@dataclass(frozen=True, slots=True)
class CompletionOutcome:
    accepted: bool
    case_id: int | None
    stale_reason: str | None

@dataclass(frozen=True, slots=True)
class TargetSnapshot:
    target_type: str
    target_id: int
    author_id: int
    version: int
    content_hash: str
    exists: bool
    deleted: bool
    visible: bool
    payload: dict[str, object]

@dataclass(frozen=True, slots=True)
class PublishDecision:
    moderation_status: PublicModerationStatus
    case_id: int | None
    target_id: int | None
    published_payload: dict[str, object] | None

@dataclass(frozen=True, slots=True)
class ModerationEffect:
    case_id: int
    name: str
    target_type: str
    target_id: int

class ModerationTargetAdapter(Protocol):
    target_type: str
    revision_fields: frozenset[str]

    def snapshot(self, db: Session, target_id: int) -> TargetSnapshot | None:
        raise NotImplementedError

    def create_pending(
        self, db: Session, author_id: int, payload: dict[str, object],
    ) -> int:
        raise NotImplementedError

    def apply_revision(
        self, db: Session, target_id: int, payload: dict[str, object],
    ) -> None:
        raise NotImplementedError

    def hide(self, db: Session, target_id: int, actor_id: int) -> None:
        raise NotImplementedError

    def restore(self, db: Session, target_id: int, actor_id: int) -> None:
        raise NotImplementedError
```

`ModerationService` 的唯一文本入口如下；阶段 3 只能在公开内容的 `PROVIDER_REVIEW` 分支接入 Provider，不能改变实时入口：

```python
def moderate_realtime_text(
    self, *, text: str | None, context: ModerationContext,
) -> ModerationResult:
    raise NotImplementedError  # local-only; never Provider

def moderate_public_text(
    self, *, text: str | None, context: ModerationContext,
) -> PublicTextModerationOutcome:
    raise NotImplementedError
```

`ModerationRepository` 的固定签名如下，后续任务与阶段 3 必须复用，不创建同义方法：

```python
def create_case_with_revision(
    self, *, target_type: str, target_id: int, author_id: int,
    source: ModerationCaseSource, version: int, payload: dict[str, object],
    content_hash: str, local_disposition: PolicyDisposition,
    policy_version: str, rule_version: int,
    task_type: ModerationTaskType | None,
    provider: ModerationProvider = ModerationProvider.NONE,
) -> tuple[ModerationCase, ContentRevision, ModerationTask | None]:
    raise NotImplementedError

def record_realtime_rejection(
    self, *, author_id: int, channel: RestrictionScope,
    attempt_key_hash: str, content_hash: str,
    risk_category: RiskCategory, severity: Severity,
    policy_version: str, rule_version: int,
) -> ModerationCase:
    raise NotImplementedError

def claim_due_tasks(
    self, *, worker_id: str, limit: int, now: datetime,
    lease_seconds: int,
    supported_providers: frozenset[ModerationProvider],
) -> list[ClaimedTask]:
    raise NotImplementedError

def complete_task(self, *, task_id: int, claim_token: str,
                  result: NormalizedTaskResult,
                  now: datetime) -> CompletionOutcome:
    raise NotImplementedError

def reschedule_processing_task(
    self, *, task_id: int, claim_token: str,
    provider_job_id: str, next_attempt_at: datetime, now: datetime,
) -> bool:
    raise NotImplementedError

def retry_task(self, *, task_id: int, claim_token: str, error_code: str,
               safe_message: str, next_attempt_at: datetime,
               exhausted: bool, now: datetime) -> bool:
    raise NotImplementedError

def append_action(self, *, case_id: int | None, actor_type: str,
                  actor_id: int | None, action: str, idempotency_key: str,
                  reason_code: str, note: str | None,
                  before_status: str | None,
                  after_status: str | None) -> ModerationAction:
    raise NotImplementedError
```

所有受限发布 HTTP 响应固定为 403，且 `expires_at` 为带 `Z` 的 UTC ISO-8601；不得返回其他 key 或裸字符串 detail：

```json
{"detail":{"code":"PUBLISH_RESTRICTED","message":"当前发布权限受限","restriction_scope":"post","expires_at":"2026-07-19T12:00:00Z"}}
```

### Flutter files

- `lib/models/moderation.dart`: `ModerationStatus`、`ModerationCaseSummary`、`ModerationRestriction`、`ModerationAppealResult` 和用户文案映射。
- `lib/services/api/moderation_service.dart`: 用户案件、案件详情、申诉和限制 API。
- `lib/providers/moderation_notifier.dart`: “我的审核”分页、筛选、申诉与刷新状态。
- `lib/screens/profile/my_moderation_screen.dart`: 用户侧页面；不提供管理员功能。
- 现有 `Post`、`Comment` 只增加 Phase 2 治理字段，不建立平行模型；`Message`、其 failure 字段与 Drift 映射完全复用 Phase 1，不在 Phase 2 改动。

---

### Task 1: Extend the Phase 1 ModerationService with the Phase 2 policy contract

**Files:**
- Modify: `app/services/moderation_types.py`
- Modify: `app/services/moderation_policy.py`
- Modify: `app/services/moderation_service.py`
- Test: `tests/test_moderation_phase1_contracts.py`
- Test: `tests/test_moderation_phase2_service_contracts.py`
- Test: `tests/test_moderation_phase1_write_paths.py`
- Test: `tests/test_inactive_auth.py`

- [ ] **Step 1: Write failing contract tests against the existing service**

Keep the Phase 1 gate assertions, then add exact type/value/signature tests:

```python
assert {item.value for item in PolicyDisposition} == {
    "approved", "rejected", "provider_review", "manual_review",
}
assert {item.value for item in Severity} == {"low", "medium", "high"}
assert ModerationResult.__annotations__["severity"] == Severity | None
assert ModerationResult.__annotations__["policy_version"] is str
assert ModerationResult.__annotations__["rule_version"] is int
assert PublicTextModerationOutcome.__annotations__ == {
    "disposition": PolicyDisposition,
    "result": ModerationResult,
}

realtime = service.moderate_realtime_text(text="hello", context=private_context)
assert isinstance(realtime, ModerationResult)
assert provider.calls == []

public = service.moderate_public_text(text="hello", context=public_context)
assert isinstance(public, PublicTextModerationOutcome)
assert public.disposition in set(PolicyDisposition)
```

Parameterize policy outcomes so local approve/reject plus policy thresholds produce all four dispositions. Assert `moderate_realtime_text()` never invokes the injected Provider seam even when the context is malformed as public; Phase 2 has no Provider call in either method. Assert a `PROVIDER_REVIEW` public outcome is preserved as a typed value for governance to downgrade safely rather than treated as approved.

- [ ] **Step 2: Run Phase 1 plus new contract tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest \
  tests/test_moderation_phase1_contracts.py \
  tests/test_moderation_phase2_service_contracts.py \
  tests/test_moderation_phase1_write_paths.py \
  tests/test_inactive_auth.py -q
```

Expected: existing Phase 1 tests PASS, while the new test FAILS because `PolicyDisposition`, `PublicTextModerationOutcome`, `moderate_realtime_text()` and `moderate_public_text()` do not yet exist.

- [ ] **Step 3: Extend the existing types, policy and service in place**

In `app/services/moderation_types.py`, add the locked `PolicyDisposition` and `PublicTextModerationOutcome`; preserve `ModerationResult`, but enforce `policy_version: str` and `rule_version: int`. Normalize Phase 1's no-hit representation before constructing the public result so externally persisted severity is either `None` or one of `Severity.LOW/MEDIUM/HIGH`; never add a `none`, `minor` or `severe` enum value.

In the existing `ModerationPolicy`, return `PolicyDisposition`: local explicit rejection maps to `REJECTED`; local approval maps to `APPROVED`, `PROVIDER_REVIEW`, or `MANUAL_REVIEW` according to its deterministic public-context policy. Then implement the only two service methods:

```python
def moderate_realtime_text(
    self, *, text: str | None, context: ModerationContext,
) -> ModerationResult:
    return self._moderate_local(text=text, context=context)

def moderate_public_text(
    self, *, text: str | None, context: ModerationContext,
) -> PublicTextModerationOutcome:
    result = self._moderate_local(text=text, context=context)
    return PublicTextModerationOutcome(
        disposition=self.policy.decide(context, result),
        result=result,
    )
```

Keep any Phase 1 `moderate_text()` only as a private implementation renamed `_moderate_local`; migrate all callers in later tasks to one of the two explicit methods. Do not inject or call a Tencent client here. Do not create another `ModerationService` class or module.

- [ ] **Step 4: Run service, write-path and authentication regressions**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest \
  tests/test_moderation_phase1_contracts.py \
  tests/test_moderation_phase2_service_contracts.py \
  tests/test_moderation_phase1_write_paths.py \
  tests/test_inactive_auth.py \
  tests/test_post_visibility_contracts.py \
  tests/test_ws_validation_unit.py \
  tests/test_community_chat_contracts.py -q
```

Expected: all tests PASS; realtime tests observe no Provider calls, public calls return `PublicTextModerationOutcome`, `rule_version` remains an integer, and no external server on port 8898 is required.

- [ ] **Step 5: Commit the shared Phase 1/2 contract extension**

```bash
cd /d/NanTuPy
git add app/services/moderation_types.py app/services/moderation_policy.py app/services/moderation_service.py tests/test_moderation_phase1_contracts.py tests/test_moderation_phase2_service_contracts.py
git commit -m "feat: extend moderation policy dispositions"
```

---

### Task 2: Add governance ORM models and the dynamic Alembic migration

**Files:**
- Create: `app/models/moderation.py`
- Modify: `app/models/models.py:97-197,296-340,457-510,669-688,786-898`
- Modify: `app/models/community.py:22-91,135-210`
- Modify: `app/models/__init__.py`
- Modify: `alembic/env.py`
- Create: `alembic/versions/2026_07_18_0300_add_moderation_governance.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_moderation_models.py`
- Create: `tests/test_moderation_migration.py`

- [ ] **Step 1: Write failing model and metadata tests**

Add tests that inspect SQLAlchemy metadata and assert all ten new tables, report columns, target governance columns, indexes and uniqueness constraints. The core assertions are:

```python
assert {"moderation_cases", "moderation_tasks", "content_revisions",
        "moderation_actions", "user_violations", "user_violation_compensations",
        "user_moderation_profiles", "moderation_appeals",
        "moderation_worker_heartbeats", "moderation_policy_config"} <= set(Base.metadata.tables)
assert _unique("moderation_cases", "target_type", "target_id", "version", "content_hash", "source")
assert _unique("moderation_cases", "attempt_key_hash")
assert _unique("moderation_tasks", "idempotency_key")
assert _unique("content_revisions", "target_type", "target_id", "version")
assert _unique("moderation_appeals", "case_id", "appellant_id")
assert _unique("reports", "reporter_id", "target_type", "target_id")
assert _unique("moderation_actions", "idempotency_key")
assert _unique("user_violation_compensations", "violation_id", "idempotency_key")
assert _column_type("moderation_cases", "rule_version") is Integer
assert _column_type("moderation_cases", "policy_version") is String
assert _column_type("moderation_tasks", "task_type") is String
assert _column_type("moderation_tasks", "provider") is String
```

Assert `Report` has `assignee_id`, `moderation_case_id`, `resolution`, `resolution_note`, `action_taken`, `resolved_by`, `resolved_at`; public revision targets have `moderation_status`, `published_revision_id`, `pending_revision_id`; `UserModerationProfile` has `last_confirmed_violation_at`; persisted severities are nullable or exactly `low/medium/high`; case sources, task types and providers accept only values from `ModerationCaseSource`, `ModerationTaskType` and `ModerationProvider`. Assert `ModerationAction`, `UserViolation`, `UserViolationCompensation`, and `ModerationAppeal` expose no mutable relationship with delete-orphan cascade. Add ORM `before_update`/`before_delete` guards for action, violation and compensation rows so corrections can only append; the migration also creates MySQL `BEFORE UPDATE` triggers for those three ledger tables, while no application repository exposes delete operations.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_models.py tests/test_moderation_migration.py -q
```

Expected: FAIL because `app.models.moderation` and the governance tables do not exist.

- [ ] **Step 3: Implement the models and generate the migration from the live head**

Define fields with bounded strings and explicit indexes:

```python
class ModerationCase(Base):
    __tablename__ = "moderation_cases"
    # target_type, target_id(nullable only for realtime rejected attempt),
    # attempt_key_hash, revision_id, author_id, source, version,
    # content_hash, status, local_decision, provider_decision,
    # risk_category, severity, confidence, policy_version, rule_version,
    # provider_request_id, created_at, completed_at

class ModerationTask(Base):
    __tablename__ = "moderation_tasks"
    # case_id, task_type, provider, status, idempotency_key, attempt_count,
    # max_attempts, next_attempt_at, claim_token, worker_id, claimed_at,
    # lease_expires_at, last_error_code, last_error_message, created_at, updated_at

class ContentRevision(Base):
    __tablename__ = "content_revisions"
    # target_type, target_id, author_id, version, content_hash,
    # payload_json, status, created_at, applied_at, rejected_at

class ModerationAction(Base):
    __tablename__ = "moderation_actions"
    # case_id nullable for account actions, actor_type, actor_id, action,
    # idempotency_key, reason_code, note, before_status, after_status, created_at

class UserViolation(Base):
    __tablename__ = "user_violations"
    # user_id, case_id, points, severity, reason_code, created_at; unique(case_id)

class UserViolationCompensation(Base):
    __tablename__ = "user_violation_compensations"
    # violation_id, case_id, points, reason_code, idempotency_key,
    # actor_id, created_at; unique(idempotency_key)

class UserModerationProfile(Base):
    __tablename__ = "user_moderation_profiles"
    # user_id PK, risk_score, last_confirmed_violation_at,
    # post_restricted_until, comment_restricted_until,
    # chat_restricted_until, all_content_restricted_until,
    # updated_at, version

class ModerationAppeal(Base):
    __tablename__ = "moderation_appeals"
    # case_id, appellant_id, reason,
    # status(manual_review/approved/rejected), resolved_by,
    # resolution_note, resolved_at, created_at

class ModerationWorkerHeartbeat(Base):
    __tablename__ = "moderation_worker_heartbeats"
    # worker_id PK, started_at, heartbeat_at, current_task_id, version

class ModerationPolicyConfig(Base):
    __tablename__ = "moderation_policy_config"
    # singleton id=1, policy_version, strict_review_score,
    # post_comment_restrict_score, all_content_restrict_score,
    # priority_review_score, post_comment_restrict_hours,
    # all_content_restrict_hours, updated_by, updated_at
```

Add an isolated governance DB fixture in `tests/conftest.py` that begins a transaction per test, binds FastAPI `get_db` to that session, and rolls back after the test; concurrent claim tests create two sessions against the same test schema. Do not use the live uvicorn/port-8898 fixtures for phase-2 tests.

Use `Text` only for `ContentRevision.payload_json` and appeal reason; cap appeal input at 1000 characters. `last_error_message`, action note and report resolution note are capped at 500 characters and must be sanitized before persistence. Register the model module in both `app/models/__init__.py` and `alembic/env.py`.

Add exact governance columns as follows: `Post` keeps its existing `hidden_by_admin/hidden_by/hidden_at` and gains `moderation_status`, `published_revision_id`, `pending_revision_id`; `Comment`, `ComicEvent`, `ComicComment`, `Community`, and `CommunityAnnouncement` gain those six status/revision/hide columns; `User` gains profile-scoped `profile_moderation_status`, `profile_published_revision_id`, `profile_pending_revision_id`, `profile_hidden_by_admin`, `profile_hidden_by`, `profile_hidden_at` so content hiding never changes `is_active`. All revision IDs reference `content_revisions.id` with named FKs added after both sides exist in `upgrade()` and dropped before table teardown in `downgrade()`; ORM FKs use `use_alter=True` and nullable IDs during row bootstrap. `Message` uses existing `deleted_by_admin` and does not store rejected messages. `CommunityJoinRequest` remains non-public and is reportable through its adapter, but does not receive revision columns. Rejected never-published target rows stay author-only;阶段 2 不删除、不清空、不 tombstone，阶段 4 的 retention maintenance 才执行 30/180/365 天生命周期。

Before creating the migration, dynamically confirm the current head:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m alembic heads
```

Expected: exit code 0 and exactly one line ending in `(head)`. Do not continue if zero or multiple heads are printed. Then generate the exact revision ID so Alembic writes the currently observed head into `down_revision`:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m alembic revision --rev-id 2026_07_18_0300 -m "add moderation governance"
```

Rename the generated file to `alembic/versions/2026_07_18_0300_add_moderation_governance.py` if Alembic uses a different slug. Verify its `down_revision` equals the head printed immediately before generation; never replace it with a revision remembered from investigation. Implement MySQL-compatible `upgrade()`/`downgrade()`, named foreign keys, named unique constraints, task claim index `(status, next_attempt_at, lease_expires_at)`, case queue index `(status, created_at)`, report queue index `(status, assignee_id, created_at)`, and user case index `(author_id, status, created_at)`. Persist `rule_version` as integer and `policy_version` as bounded string. Map Python enums at all ORM boundaries: `ModerationTaskType`, `ModerationCaseSource`, `ModerationProvider`, `Severity`; never pass literal task/source/provider strings from routes. Seed singleton `moderation_policy_config` with version `phase2-default-v1`, scores `5/10/15/20` and durations `24/72` hours; do not add a decay interval/configuration because阶段 4 owns decay.

Backfill every existing public target's moderation status to `approved` before making the status column non-null; leave revision IDs null because legacy rows have no synthetic revision payload. Before adding the report unique constraint, find duplicate `(reporter_id,target_type,target_id)` groups, retain the earliest report, append one body-free `legacy_report_duplicate_merged` action per removed report using `legacy-report-duplicate:{report_id}` as idempotency key, then delete the duplicate row. Normalize legacy report target type `user` to `user_profile` only after the duplicate merge. Migration tests seed approved, hidden and duplicate-report legacy data and assert the backfill/merge is deterministic and downgrade restores the old schema.

- [ ] **Step 4: Run model, migration and single-head verification**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_models.py tests/test_moderation_migration.py -q
./.venv/Scripts/python.exe -m alembic upgrade head
./.venv/Scripts/python.exe -m alembic heads
```

Expected: tests PASS, upgrade succeeds, and exactly one Alembic head is printed. The migration test must upgrade from its captured parent, inspect every table/index/FK, downgrade to the parent, then upgrade back to head.

- [ ] **Step 5: Commit the schema**

```bash
cd /d/NanTuPy
git add app/models/moderation.py app/models/models.py app/models/community.py app/models/__init__.py alembic/env.py alembic/versions/2026_07_18_0300_add_moderation_governance.py tests/conftest.py tests/test_moderation_models.py tests/test_moderation_migration.py
git commit -m "feat: add moderation governance schema"
```

---

### Task 3: Implement the case state machine and concurrency-safe repository

**Files:**
- Modify: `app/services/moderation_types.py`
- Create: `app/services/moderation_state_machine.py`
- Create: `app/services/moderation_repository.py`
- Create: `tests/test_moderation_state_machine.py`
- Create: `tests/test_moderation_repository.py`

- [ ] **Step 1: Write failing state and database behavior tests**

Use an isolated database session fixture and test real conditional updates, not source-string assertions. Cover the exact transition set and reject every other transition:

```python
ALLOWED = {
    (CaseStatus.PENDING, CaseStatus.APPROVED),
    (CaseStatus.PENDING, CaseStatus.REJECTED),
    (CaseStatus.PENDING, CaseStatus.MANUAL_REVIEW),
    (CaseStatus.PENDING, CaseStatus.CANCELLED),
    (CaseStatus.MANUAL_REVIEW, CaseStatus.APPROVED),
    (CaseStatus.MANUAL_REVIEW, CaseStatus.REJECTED),
    (CaseStatus.APPROVED, CaseStatus.REJECTED),
}
```

Repository tests must prove: duplicate case creation returns the existing row; duplicate task idempotency key creates one task; two sessions cannot retain claims on the same row; `supported_providers` excludes unregistered Provider rows; expired leases are reclaimable with a new token; a stale token cannot complete/retry/reschedule; and task completion does not apply a result when case status, target version, hash, deletion state, or pending revision changed. Assert `complete_task()` accepts `NormalizedTaskResult` and rejects a Phase 1 `ModerationResult`, raw Provider result, dict or string. Assert the only normalized decisions are `approved/rejected/manual_review`; `ProviderStillProcessing(provider_job_id,retry_after_seconds)` is a distinct control-flow signal. Assert `reschedule_processing_task()` with a valid token stores the job ID, computes the bounded next poll time, clears claim fields and leaves case terminal fields unchanged so Phase 3 can poll without inventing a `processing` decision.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_state_machine.py tests/test_moderation_repository.py -q
```

Expected: FAIL on missing state-machine and repository imports.

- [ ] **Step 3: Implement typed transitions, claims, leases and idempotency**

Implement:

```python
def require_case_transition(
    before: CaseStatus,
    after: CaseStatus,
    *,
    trigger: Literal["normal", "report_review", "backfill"],
) -> None:
    if (before, after) not in ALLOWED_TRANSITIONS:
        raise InvalidCaseTransition(before, after)
    if before is CaseStatus.APPROVED and after is CaseStatus.REJECTED \
            and trigger not in {"report_review", "backfill"}:
        raise InvalidCaseTransition(before, after)
```

`create_case_with_revision()` computes no hashes itself, validates the payload has already been whitelisted, and accepts only typed `ModerationCaseSource`/`ModerationTaskType`/`ModerationProvider`. It inserts revision/case/task in one transaction, catches only the named uniqueness violation, rolls back to a savepoint, then returns the existing tuple. The task idempotency key is `moderation:{case_id}:{task_type.value}:{content_hash}`. `record_realtime_rejection()` inserts no revision/task/body; it creates one rejected `message_attempt` case with `source=ModerationCaseSource.REALTIME_REJECTION` per HMAC-SHA256 `attempt_key_hash` (server-side log-hash key + user/channel/client message ID or request ID), stores only SHA-256 `content_hash` plus classification/rule versions, appends `realtime_rejected`, and returns the existing case on retry. Never use the raw client ID or body as an idempotency key.

`claim_due_tasks()` selects only rows whose typed `provider` belongs to `supported_providers`, then selects `TaskStatus.PENDING`/`TaskStatus.FAILED` rows whose `next_attempt_at <= now`, plus `TaskStatus.PROCESSING` rows whose lease expired, ordered by `next_attempt_at,id`; claim each row with one conditional `UPDATE` that sets a UUID token, `TaskStatus.PROCESSING`, worker ID, timestamps and increments `attempt_count`. Phase 2 passes `frozenset({ModerationProvider.NONE})`, so it cannot claim Tencent work; Phase 3 extends the set after registering its processor. Commit claims before any Worker processing.

`complete_task()` accepts only `NormalizedTaskResult`, then uses one conditional update requiring task ID, `processing`, token and unexpired lease, loads the case/revision and calls a supplied target snapshot verifier. Return `CompletionOutcome(accepted=True, case_id=case.id, stale_reason=stale_reason)` after durably recording a valid claimed result, including stale/deleted/superseded targets; return `accepted=False` for token/lease mismatches. Mark the task `TaskStatus.SUCCEEDED` when its normalized result is valid even if target application is stale, and never apply content or violations in this method. The Worker invokes `apply_case_result()` only when `accepted` is true and `stale_reason` is null. Phase 3 Provider adapters must translate Provider-specific responses to `NormalizedTaskResult` before this boundary; they raise `ProviderStillProcessing` while an asynchronous job remains pending and never pass Provider-specific decisions into the repository.

`retry_task()` strips control characters, caps the message at 500 characters, conditionally updates by token, clears claim fields, and sets either `failed` with `next_attempt_at` or `dead`. Exhaustion appends an action and transitions an otherwise pending case to `manual_review`.

- [ ] **Step 4: Run repository tests including two-session races**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_state_machine.py tests/test_moderation_repository.py -q
```

Expected: all tests PASS; assertions confirm one durable claim, stale tokens return `False`, and no test depends only on reading source text.

- [ ] **Step 5: Commit repository foundations**

```bash
cd /d/NanTuPy
git add app/services/moderation_types.py app/services/moderation_state_machine.py app/services/moderation_repository.py tests/test_moderation_state_machine.py tests/test_moderation_repository.py
git commit -m "feat: add moderation state and task repository"
```

---

### Task 4: Add target adapters and atomic content revision application

**Files:**
- Create: `app/services/moderation_targets.py`
- Create: `app/services/moderation_governance_service.py`
- Create: `app/services/moderation_effects.py`
- Create: `tests/test_moderation_targets.py`
- Create: `tests/test_content_revisions.py`

- [ ] **Step 1: Write failing adapter and revision lifecycle tests**

Parameterize adapters for `post`, `comment`, `message`, `user_profile`, `community`, `community_join_request`, `community_announcement`, `comic_event`, and `comic_comment`. Assert unknown types fail closed, missing/deleted targets cannot be restored by stale results, self-contained payloads reject non-whitelisted keys, and each adapter returns its real author/owner.

Test revision behavior and every policy disposition:

```python
assert submit(PolicyDisposition.APPROVED).moderation_status is PublicModerationStatus.APPROVED
assert submit(PolicyDisposition.REJECTED).moderation_status is PublicModerationStatus.REJECTED
assert submit(PolicyDisposition.MANUAL_REVIEW).moderation_status is PublicModerationStatus.PENDING_REVIEW
assert submit(PolicyDisposition.PROVIDER_REVIEW).moderation_status is PublicModerationStatus.PENDING_REVIEW
assert task_count() == 0  # Phase 2 provider_review safely becomes a manual case
```

Also prove: pending create is author-visible but absent from public adapter queries; pending edit leaves the old approved payload on the target; a newer edit cancels the prior pending case; approval applies the matching revision once and clears `pending_revision_id`; rejection keeps the old approved payload and retains the rejected revision; a stale approval cannot overwrite a newer revision. For `PROVIDER_REVIEW`, assert the case stores the local disposition for audit, ends in `manual_review`, uses `ModerationProvider.NONE`, appends `provider_review_deferred_to_manual`, and creates no `ModerationTask`.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_targets.py tests/test_content_revisions.py -q
```

Expected: FAIL because adapter registry and governance service are missing.

- [ ] **Step 3: Implement adapters, revision orchestration and idempotent effects**

Create an explicit registry; no dynamic model lookup from client input:

```python
TARGET_ADAPTERS: dict[str, ModerationTargetAdapter] = {
    "post": PostTargetAdapter(),
    "comment": CommentTargetAdapter(),
    "message": MessageTargetAdapter(),
    "user_profile": UserProfileTargetAdapter(),
    "community": CommunityTargetAdapter(),
    "community_join_request": CommunityJoinRequestTargetAdapter(),
    "community_announcement": CommunityAnnouncementTargetAdapter(),
    "comic_event": ComicEventTargetAdapter(),
    "comic_comment": ComicCommentTargetAdapter(),
}
```

Whitelist phase-2 revision payload fields exactly: post `{content, post_type, visibility, is_public, community_id, community_only}`; comment/comic comment `{content, parent_id, reply_to_user_id}`; user profile `{username, bio}`; community `{name, description, rules, join_policy, topic_id}`; announcement `{title, content, is_pinned}`; comic event `{name, city_id, venue, start_date, end_date, start_time, end_time, ticket_info, website, intro, tag_ids}`. Message and join-request adapters are read/hide targets and must reject revision creation. `images`, `video_url`, `avatar_url`, `cover_photo_url`, `banner_url`, and `image_urls` are excluded from revision JSON in phase 2: a pending create may retain already-uploaded opaque media references only on its author-only business row, while an edit cannot change those fields through the moderated revision path. Phase 3 will replace this compatibility boundary with verified upload-session IDs and quarantined objects; phase 2 must never persist a client-supplied signed/private URL in governance tables.

Canonicalize JSON with sorted keys and compact separators before SHA-256 hashing. `submit_public_create()` and `submit_public_edit()` accept `PublicTextModerationOutcome`, never a raw `ModerationResult`. `APPROVED` applies immediately; `REJECTED` records a rejected case/revision without publishing; `MANUAL_REVIEW` stages a manual case; and Phase 2 maps `PROVIDER_REVIEW` to that same safe manual path with action `provider_review_deferred_to_manual`, `provider=ModerationProvider.NONE` and `task_type=None`. The provider-review downgrade and case/revision creation happen in one transaction, create no cloud task, and consume no queue capacity. `submit_public_create()` creates the business row in the disposition-appropriate state, version 1 revision and case in one transaction and returns `PublishDecision`; `submit_public_edit()` locks the target, increments max revision version, cancels the previous pending case, and leaves the approved target payload untouched.

`apply_case_result()` locks case, revision, and target; rechecks status/version/hash/existence/supersession; calls `require_case_transition` with `normal`, `report_review`, or `backfill` from the trusted service operation rather than client input or the case's original source; applies or rejects the revision; and appends an action whose idempotency key is `case-result:{case_id}:{after_status}`. It returns a list of `ModerationEffect` values but does not send notifications inside the transaction.

`ModerationEffects.run_after_commit()` handles named effects such as `post_topics`, `post_mentions`, `comment_notification`, `comment_mentions`, `comment_counts`, `comic_comment_counts`, and `community_announcement_notifications`. For each effect, open one new transaction, attempt the unique action `effect:<name>` with idempotency key `effect:{case_id}:{name}`, execute business DB rows/counters only when the insert succeeds, and commit both together; rollback removes both marker and side effect so a crash can retry. Notification rows use their existing delivery idempotency, and push/fanout happens only after this transaction commits. Do not persist payload text in the action.

- [ ] **Step 4: Run target and revision tests**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_targets.py tests/test_content_revisions.py -q
```

Expected: all tests PASS, including duplicate result application and newer-version race tests.

- [ ] **Step 5: Commit target governance**

```bash
cd /d/NanTuPy
git add app/services/moderation_targets.py app/services/moderation_governance_service.py app/services/moderation_effects.py tests/test_moderation_targets.py tests/test_content_revisions.py
git commit -m "feat: add moderated content revisions"
```

---

### Task 5: Implement the violation ledger, compensation and publishing restrictions

**Files:**
- Create: `app/services/publish_restrictions.py`
- Modify: `app/services/moderation_governance_service.py`
- Modify: `app/services/moderation_repository.py`
- Create: `tests/test_publish_restrictions.py`
- Create: `tests/test_violation_ledger.py`

- [ ] **Step 1: Write failing ledger, pure-function and restriction-contract tests**

Cover the only allowed severity mapping `low=1`, `medium=3`, `high=8`; thresholds 5/10/15/20; duplicate case result; partial/full compensation; expired restrictions; and UTC serialization. Assert only local explicit reject, future high-confidence Provider reject normalized by Phase 3, or administrator-confirmed violation adds points. `pending`, `manual_review`, Provider error, low-confidence result, restored content and duplicate results add none. Assert a confirmed violation sets `last_confirmed_violation_at` to its UTC confirmation time; compensation and ordinary reads do not move that anchor.

Assert restriction mapping and the exact HTTP contract:

```python
assert severity_points(Severity.LOW) == 1
assert severity_points(Severity.MEDIUM) == 3
assert severity_points(Severity.HIGH) == 8
assert restriction_for(score=10).post is True
assert restriction_for(score=10).comment is True
assert restriction_for(score=10).chat is False
assert restriction_for(score=15).all_content is True
assert restriction_for(score=20).requires_priority_review is True
assert response.status_code == 403
assert response.json() == {
    "detail": {
        "code": "PUBLISH_RESTRICTED",
        "message": "当前发布权限受限",
        "restriction_scope": "post",
        "expires_at": "2026-07-19T12:00:00Z",
    }
}
```

Parameterize `RestrictionScope.POST/COMMENT/CHAT/ALL_CONTENT`; reject arbitrary channel strings. Explicitly assert no Phase 2 code decrements risk because time elapsed and no `risk_decay` action/ledger row or maintenance schedule is created.
- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_publish_restrictions.py tests/test_violation_ledger.py -q
```

Expected: FAIL because restriction and violation services are absent.

- [ ] **Step 3: Implement append-only ledger operations and one restriction guard**

Implement one route-facing guard:

```python
class PublishRestrictionError(HTTPException):
    code = "PUBLISH_RESTRICTED"

    def __init__(self, *, scope: RestrictionScope, expires_at: datetime):
        super().__init__(
            status_code=403,
            detail={
                "code": self.code,
                "message": "当前发布权限受限",
                "restriction_scope": scope.value,
                "expires_at": utc_z(expires_at),
            },
        )

def require_can_publish(db: Session, *, user_id: int,
                        channel: RestrictionScope,
                        now: datetime) -> None:
    restriction = active_restriction(db, user_id=user_id, scope=channel, now=now)
    if restriction is not None:
        raise PublishRestrictionError(
            scope=channel, expires_at=restriction.expires_at,
        )
```

`record_violation()` validates the typed `Severity`, maps it through `SEVERITY_POINTS`, inserts one `UserViolation` per case, locks/creates `UserModerationProfile`, increments risk score, sets `last_confirmed_violation_at=confirmed_at` in UTC, derives restriction deadlines from policy, and appends `violation_added`. Catch duplicate case uniqueness and return the existing violation without adding points or moving the anchor again.

`compensate_violation()` never updates/deletes `UserViolation`. Sum existing `UserViolationCompensation.points`, insert only the remaining amount up to the original violation points using a caller-supplied idempotency key, append `violation_compensated`, atomically decrement profile score, recompute active deadlines, and restore content through the same target/revision path without rewriting the rejected case. A repeated idempotency key returns the prior compensation outcome.

Implement `severity_points()`, `restriction_for()` and deadline calculation as deterministic side-effect-free functions. Keep thresholds in the shared policy object and expose read-only normalized restrictions to ordinary users; policy mutation is added under super-admin API in Task 7. Do not implement elapsed-time risk reduction, `risk_decay` actions, decay tables, a scheduler or Worker dispatch in Phase 2. Phase 4 is the sole owner of the exact every-30-complete-days decay algorithm and append-only decay ledger, using `last_confirmed_violation_at` as its anchor.

- [ ] **Step 4: Run ledger and restriction tests**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_publish_restrictions.py tests/test_violation_ledger.py -q
```

Expected: all tests PASS and duplicate/reversal tests preserve every original violation and action row.

- [ ] **Step 5: Commit risk governance**

```bash
cd /d/NanTuPy
git add app/services/publish_restrictions.py app/services/moderation_governance_service.py app/services/moderation_repository.py tests/test_publish_restrictions.py tests/test_violation_ledger.py
git commit -m "feat: add moderation restrictions and compensation"
```

---

### Task 6: Close the report intake and case-linking loop

**Files:**
- Modify: `app/routers/reports.py`
- Create: `app/services/report_service.py`
- Modify: `app/services/moderation_repository.py`
- Modify: `app/services/moderation_targets.py`
- Create: `tests/test_reports_governance.py`

- [ ] **Step 1: Write failing report intake tests**

Parameterize accepted targets `post`, `comment`, `message`, `user_profile`, `community`, `community_join_request`, `community_announcement`, `comic_event`, `comic_comment`. Assert target existence, reporter visibility/membership, no self-report, database-backed duplicate rejection under concurrent sessions, and no raw exception in responses. Verify legacy endpoints `/api/reports/post`, `/comment`, `/user` delegate to the same service and preserve their response shape.

Assert a report links the active case for the same target when one exists; otherwise it creates one `source=ModerationCaseSource.REPORT`, `status=CaseStatus.MANUAL_REVIEW` case with a snapshot hash. Multiple reporters may link the same case, while one reporter cannot duplicate a target report; routes/services never pass source/task/provider as raw strings.

- [ ] **Step 2: Run report tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_reports_governance.py -q
```

Expected: FAIL because current report intake accepts only three target types and does not create/link cases.

- [ ] **Step 3: Implement a single report intake path**

Add:

```python
@router.post("/api/reports", status_code=201)
def create_report(
    payload: ReportCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    report = report_service.submit(
        db, reporter_id=current_user.id,
        target_type=payload.target_type, target_id=payload.target_id,
        reason=payload.reason, description=payload.description,
    )
    db.commit()
    return {"id": report.id, "status": report.status, "message": "举报已提交"}
```

Validate `reason` against the current public reason enum, cap description at 1000 characters, and use adapters for owner/existence/visibility. For messages, require reporter membership in the conversation; never copy private message text into the report, case, action, or logs. Resolve a uniqueness race into HTTP 409 with a stable `REPORT_ALREADY_EXISTS` code. Existing post/comment/user helper endpoints must construct `ReportCreate` and invoke this exact service, not duplicate validation.

- [ ] **Step 4: Run report and compatibility tests**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_reports_governance.py tests/test_post_visibility_contracts.py -q
```

Expected: all tests PASS; legacy report calls retain success behavior and private content is absent from persisted governance records.

- [ ] **Step 5: Commit report intake governance**

```bash
cd /d/NanTuPy
git add app/routers/reports.py app/services/report_service.py app/services/moderation_repository.py app/services/moderation_targets.py tests/test_reports_governance.py
git commit -m "feat: connect reports to moderation cases"
```

---

### Task 7: Add role boundaries and append-only administrator governance APIs (no administrator UI)

**Files:**
- Modify: `app/dependencies.py`
- Create: `app/routers/admin_moderation.py`
- Modify: `app/main.py`
- Modify: `app/services/moderation_governance_service.py`
- Modify: `app/services/moderation_repository.py`
- Create: `tests/test_admin_moderation_api.py`
- Create: `tests/test_moderation_audit.py`

- [ ] **Step 1: Write failing permission, conflict and audit tests**

Test anonymous/member/admin/super-admin access. `admin` and `super_admin` may list/read ordinary cases and reports; only `super_admin` may permanently deactivate/reactivate or change policy. An admin cannot handle content they authored or a report targeting them. A super-admin override requires non-empty reason code and note. Every state change must add exactly one action; detail access to report/private-message metadata adds `content_accessed` without message text.

Test list filters for status, target type, severity, created range, author and reporter; deterministic `(created_at,id)` pagination; assignment conflict; report resolution atomicity; hide/restore idempotency; and approve/reject stale-revision conflict returning 409.

- [ ] **Step 2: Run administrator tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_admin_moderation_api.py tests/test_moderation_audit.py -q
```

Expected: FAIL because the governance router and combined admin-role dependency are absent.

- [ ] **Step 3: Implement exact API routes and authorization**

Add `require_moderation_admin` accepting role names `admin` or `super_admin`, plus `require_super_admin`. Register `admin_moderation.router` once in `app/main.py`; define its router as `APIRouter(prefix="/api/admin", tags=["admin-moderation"])` so prefixes are not doubled.

Implement:

```text
GET  /api/admin/moderation/cases
GET  /api/admin/moderation/cases/{id}
POST /api/admin/moderation/cases/{id}/assign
POST /api/admin/moderation/cases/{id}/approve
POST /api/admin/moderation/cases/{id}/reject
POST /api/admin/moderation/content/{type}/{id}/hide
POST /api/admin/moderation/content/{type}/{id}/restore
GET  /api/admin/reports
GET  /api/admin/reports/{id}
POST /api/admin/reports/{id}/assign
POST /api/admin/reports/{id}/resolve
POST /api/admin/users/{id}/restrict
POST /api/admin/users/{id}/deactivate
POST /api/admin/users/{id}/reactivate
GET  /api/admin/moderation/audit
GET  /api/admin/moderation/policy
PUT  /api/admin/moderation/policy
```

Pydantic mutation bodies require `reason_code`; approve/reject/hide/restore/restrict/deactivate/reactivate/policy update also require `note` with 1–500 characters. Case approve/reject bodies accept optional `appeal_id`: without it they execute an allowed `pending/manual_review -> approved/rejected` case transition; with it they require that the referenced appeal belongs to the case and is `manual_review`, then set only appeal status to `approved/rejected`. Appeal approval restores/applies the rejected revision and invokes idempotent compensation while leaving the original case `rejected`; appeal rejection leaves target and ledger unchanged. `resolve` accepts `resolution in {dismissed,confirmed,transferred}`, a bounded note, and typed `action_taken`; in one transaction update Report, transition/apply case, apply optional hide/restriction, and append action.

Hide/restore always use adapters. Restore a confirmed false positive through `compensate_violation()`; do not delete actions or violations. Do not return `payload_json`, hit details, raw Provider response or private body in list responses. Case detail exposes content only through an authorization-checked adapter projection and records access. This task ends at backend API tests: do not add any Flutter administrator route, screen, notifier/provider, menu item or widget.

- [ ] **Step 4: Run API, audit and existing admin regression tests**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest \
  tests/test_admin_moderation_api.py \
  tests/test_moderation_audit.py \
  tests/test_identity_role_contracts.py -q
```

Expected: all tests PASS; existing sensitive-word and identity-role administrator endpoints remain compatible.

- [ ] **Step 5: Commit administrator governance**

```bash
cd /d/NanTuPy
git add app/dependencies.py app/routers/admin_moderation.py app/main.py app/services/moderation_governance_service.py app/services/moderation_repository.py tests/test_admin_moderation_api.py tests/test_moderation_audit.py
git commit -m "feat: add moderation administration APIs"
```

---

### Task 8: Add user cases, one-time appeals and restriction APIs

**Files:**
- Create: `app/routers/moderation.py`
- Modify: `app/main.py`
- Modify: `app/services/moderation_governance_service.py`
- Create: `tests/test_user_moderation_api.py`
- Create: `tests/test_moderation_appeals.py`

- [ ] **Step 1: Write failing ownership, privacy and appeal tests**

Assert a user can only see their own pending/rejected cases and revisions; cannot enumerate another user's case; cannot see hit words, regex, thresholds, internal notes, Provider fields, private message bodies or arbitrary revision JSON. Test one appeal per `(case_id, appellant_id)`, only rejected public content within a fixed 30-day appeal submission window, no appeal for unsent private chat, and concurrent duplicate submissions. This appeal eligibility window is validation, not retention cleanup: Phase 2 does not erase the revision at day 30. Submission creates `ModerationAppeal.status="manual_review"` while the original case remains append-only `rejected`; the rejected case must remain in its terminal state because the specification does not allow reopening it.

Test administrator appeal approval applies/restores the exact rejected revision, writes `appeal_approved`, and compensates the matching violation without rewriting the original case/action/violation; appeal rejection writes `appeal_rejected` and leaves target/case/points unchanged. Test restriction response includes booleans, ISO-8601 deadlines and `server_time`.

- [ ] **Step 2: Run user moderation tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_user_moderation_api.py tests/test_moderation_appeals.py -q
```

Expected: FAIL because `/api/moderation/me` is not registered.

- [ ] **Step 3: Implement the user-safe API**

Define `APIRouter(prefix="/api/moderation", tags=["moderation"])` and register it once. Implement:

```text
GET  /api/moderation/me/cases?status=pending,rejected&cursor=Y3Vyc29yLTE%3D
GET  /api/moderation/me/cases/{id}
POST /api/moderation/me/cases/{id}/appeals
GET  /api/moderation/me/restrictions
```

Serialize each case as `id,target_type,target_id,source,moderation_status,risk_category,severity,created_at,completed_at,appeal_status,revision{version,status,created_at}`. Include approved public content only through the normal target serializer; never return rejected payload content from the generic list. The appeal body is `reason` with 1–1000 characters.

Inside one transaction lock the case, verify owner/type/status/fixed appeal deadline/no prior appeal, insert `ModerationAppeal(status=AppealStatus.MANUAL_REVIEW)`, leave `ModerationCase.status=CaseStatus.REJECTED`, and append `appeal_submitted`. Resolve uniqueness races as 409 `APPEAL_ALREADY_SUBMITTED`. The administrator case-detail response includes the pending appeal; Task 7's approve/reject commands accept `appeal_id`, lock both rows, update only the appeal status, and use restore/compensation or no-op disposition respectively. This preserves the specification's exact case transition set without implementing retention.

- [ ] **Step 4: Run user API and privacy tests**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_user_moderation_api.py tests/test_moderation_appeals.py tests/test_moderation_audit.py -q
```

Expected: all tests PASS and serialized JSON contains none of the forbidden internal fields.

- [ ] **Step 5: Commit user governance APIs**

```bash
cd /d/NanTuPy
git add app/routers/moderation.py app/main.py app/services/moderation_governance_service.py tests/test_user_moderation_api.py tests/test_moderation_appeals.py
git commit -m "feat: add user moderation and appeals APIs"
```

---

### Task 9: Wire account deactivation to login, HTTP and WebSocket authentication

**Files:**
- Modify: `app/core/auth_core.py`
- Modify: `app/routers/auth.py`
- Modify: `app/routers/ws.py`
- Modify: `app/routers/admin_moderation.py`
- Modify: `tests/test_inactive_auth.py`
- Create: `tests/test_inactive_governance.py`

- [ ] **Step 1: Write failing lifecycle tests**

Create a user, issue a token, deactivate through the super-admin API, then assert password login, refresh, existing-token HTTP request, and WebSocket authentication all fail with status 403 and stable `ACCOUNT_DISABLED`; assert no publish path can proceed. HTTP detail must equal `{"code":"ACCOUNT_DISABLED","message":"账号已停用"}` and the WS auth failure must carry the same code/status. Reactivate through the audited API and verify the same credentials/token policy behaves as designed by the corrected shared contract. Assert admin cannot deactivate/reactivate and super-admin cannot deactivate without reason/note; scan responses/tests to prove no alternate disabled-account code exists.

- [ ] **Step 2: Run lifecycle tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_inactive_governance.py -q
```

Expected: FAIL if any auth path only checks that the user exists or if governance changes are not audited.

- [ ] **Step 3: Centralize the active-user check**

Ensure every token path calls the same check:

```python
def require_active_user(user: User) -> User:
    if user.is_active is not True:
        raise AuthError(code="ACCOUNT_DISABLED", message="账号已停用", status_code=403)
    return user
```

`verify_token`, login, refresh and WebSocket authentication must call it. HTTP adapters preserve status 403 and `detail={"code":"ACCOUNT_DISABLED","message":"账号已停用"}`; WebSocket sends an authentication failure frame with `status=403`, then closes. Deactivate/reactivate lock the user, perform idempotent state change and append an account action with `case_id=None`, then commit. Existing tokens are rejected on their next database-backed `verify_token` lookup because `is_active` is false; reactivation restores that lookup behavior. Do not introduce a second token-version mechanism in stage 2 and do not expose whether a disabled email exists through a different login message.

- [ ] **Step 4: Run auth and governance regressions**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_inactive_governance.py tests/test_inactive_auth.py tests/test_admin_moderation_api.py -q
```

Expected: all tests PASS for login, existing HTTP token, refresh, WebSocket, deactivate and reactivate.

- [ ] **Step 5: Commit active-account governance**

```bash
cd /d/NanTuPy
git add app/core/auth_core.py app/routers/auth.py app/routers/ws.py app/routers/admin_moderation.py tests/test_inactive_auth.py tests/test_inactive_governance.py
git commit -m "feat: enforce audited account activation state"
```

---

### Task 10: Add the independent Worker, lease recovery and safe health reporting

**Files:**
- Create: `app/workers/__init__.py`
- Create: `app/workers/moderation_worker.py`
- Modify: `app/routers/health.py`
- Modify: `app/main.py:131-150`
- Create: `deploy/moderation-worker-entrypoint.sh`
- Modify: `docker-compose.yml`
- Create: `tests/test_moderation_worker.py`
- Create: `tests/test_moderation_health.py`

- [ ] **Step 1: Write failing Worker and health tests**

With a fake clock and repository, assert claim commit precedes processing, heartbeat updates, expired lease recovery, exponential backoff with bounded jitter, `NormalizedTaskResult` completion, stale completion, max-attempt transition to `manual_review`, and graceful stop. Assert Phase 2 never invokes a Tencent/COS client and registers no processor for `ModerationProvider.TENCENT`. Normal Phase 2 `PROVIDER_REVIEW` creates a manual case before queueing, so queue depth remains unchanged. Seed a Tencent task defensively and prove the Phase 2 Worker neither claims nor updates it because `supported_providers={ModerationProvider.NONE}`; Phase 3 may claim it only after enabling its Provider processor. Assert no `cleanup`, retention or risk-decay task type/processor exists.

Health tests must separately expose API, database, queue depth/oldest age/dead count, worker heartbeat age, and Provider `configured` boolean. Force DB errors and assert responses/logs omit raw DSN, credentials and exception text.

- [ ] **Step 2: Run Worker and health tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_worker.py tests/test_moderation_health.py -q
```

Expected: FAIL because the Worker CLI and moderation health sections are missing.

- [ ] **Step 3: Implement a process-separated Worker foundation**

Implement CLI arguments `--worker-id`, `--poll-seconds`, `--batch-size`, `--lease-seconds`, and `--once`. The loop is:

```python
while not stopping:
    heartbeat(db, worker_id)
    claims = repository.claim_due_tasks(
        worker_id=worker_id, limit=batch_size,
        now=clock.now(), lease_seconds=lease_seconds,
        supported_providers=processor_registry.providers,
    )
    db.commit()
    for claim in claims:
        processor = processor_registry.require(claim.provider, claim.task_type)
        try:
            normalized = processor.process(claim)  # no API BackgroundTask
        except ProviderStillProcessing as pending:
            with SessionLocal.begin() as tx:
                repository.reschedule_processing_task(
                    task_id=claim.task_id,
                    claim_token=claim.claim_token,
                    provider_job_id=pending.provider_job_id,
                    next_attempt_at=clock.now() + timedelta(
                        seconds=clamp(pending.retry_after_seconds, 5, 300),
                    ),
                    now=clock.now(),
                )
        except RetryableTaskError as error:
            retry_claim(claim, error)
        else:
            with SessionLocal.begin() as tx:
                processor.finalize(tx, claim, normalized)
    sleep_when_empty()
```

Every `processor.process()` is registered by typed `(ModerationProvider, ModerationTaskType)` and either returns a `NormalizedTaskResult`, raises `ProviderStillProcessing` for a future Phase 3 asynchronous Provider adapter, or raises a typed retryable/permanent processing error. `processor.finalize()` passes only `NormalizedTaskResult` into `complete_task()`. The `ProviderStillProcessing` branch calls the locked `reschedule_processing_task()` CAS operation, which stores only the job ID, clears claim fields and returns the task to pending polling. It never fakes `manual_review` or a fourth normalized decision. Phase 2 itself has no processor that raises this signal, but locks the shared boundary for Phase 3. Backoff is `min(base_seconds * 2 ** (attempt_count - 1), max_seconds) + uniform(0, jitter_seconds)`. Errors persist only stable code and sanitized bounded message. Heartbeats contain IDs/timestamps only.

Host-side commands in this plan use the repository's existing `./.venv/Scripts/python.exe`; inside the Linux image, `deploy/moderation-worker-entrypoint.sh` runs `python -m app.workers.moderation_worker` because the container installs requirements into its system interpreter. The Worker entrypoint must not execute `alembic upgrade`. Register health with `app.include_router(health.router, prefix="/api/health")`, preserving `/api/health/` and `/api/health/ping`; update tests/callers from the old root health path. Add `moderation-worker` to Compose using the same image/env/database, command the new entrypoint, and set `depends_on.nantupy-backend.condition: service_healthy` so the API entrypoint has completed Alembic first. Change API healthcheck from `/docs` to `/api/health/` so `HIDE_API_DOCS=1` is valid. Do not start Worker from `main.py` lifespan.

- [ ] **Step 4: Run Worker, health and import smoke tests**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_worker.py tests/test_moderation_health.py -q
./.venv/Scripts/python.exe -m app.workers.moderation_worker --once --worker-id test-smoke
```

Expected: tests PASS; one-shot exits 0 after heartbeat/empty poll and does not contact an external Provider.

- [ ] **Step 5: Commit Worker infrastructure**

```bash
cd /d/NanTuPy
git add app/workers/__init__.py app/workers/moderation_worker.py app/routers/health.py app/main.py deploy/moderation-worker-entrypoint.sh docker-compose.yml tests/test_moderation_worker.py tests/test_moderation_health.py
git commit -m "feat: add moderation worker and health checks"
```

---

### Task 11: Integrate moderated revisions into post create/edit/read paths

**Files:**
- Modify: `app/routers/posts.py`
- Modify: `app/models/models.py:171-274`
- Modify: `app/services/post_visibility_service.py`
- Modify: `app/services/recommendation_service.py`
- Modify: `app/services/search_service.py`
- Modify: `app/services/topic_service.py`
- Modify: `app/services/moderation_effects.py`
- Create: `tests/test_post_moderation_governance.py`

- [ ] **Step 1: Write failing post lifecycle and side-effect tests**

Test immediate approved create, pending create, rejected create, pending edit of approved post, approved edit, rejected edit, superseded edit and deletion during review. Assert pending create is visible to its author through detail/my-posts only and absent from public feed/search/topic/community/recommendation. Assert pending edit returns old public content plus `moderation_status="pending_review"` and case ID to author; other users see old content with approved status.

Spy on `TopicService`, `MentionService`, notifications, feed insertion and counters. Assert none run before approval, exactly once after approval/commit, and never for rejected/cancelled/stale results.

- [ ] **Step 2: Run post governance tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_post_moderation_governance.py -q
```

Expected: FAIL because current edits overwrite `Post.content` and create side effects immediately.

- [ ] **Step 3: Route all post writes through governance service**

At every create/edit entry, call `require_can_publish(db, user_id=current_user.id, channel=RestrictionScope.POST, now=clock.now())`, then the shared `ModerationService.moderate_public_text()` and `submit_public_create()`/`submit_public_edit()`. Pass the complete `PublicTextModerationOutcome`; do not branch on `ModerationResult.decision` in the route. Remove route-level direct sensitive-word calls and direct topic/mention processing. Commit the business transaction before invoking returned effects.

Serialize the stable response:

```json
{
  "moderation_status": "approved | pending_review | rejected",
  "case_id": 123,
  "content": {"id": 456, "content": "approved public payload"}
}
```

For a pending edit, `content` is the current approved `Post`; never echo rejected/pending text from a generic error. Extend every public predicate with `Post.moderation_status == "approved"`; author-owned queries may include pending rows explicitly. Keep `hidden_by_admin` filtering and combine it with approval rather than replacing it.

- [ ] **Step 4: Run post and visibility regressions**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_post_moderation_governance.py tests/test_post_visibility_contracts.py tests/test_posts_liked_performance_contract.py -q
```

Expected: all tests PASS and approval effects occur once after commit.

- [ ] **Step 5: Commit post governance integration**

```bash
cd /d/NanTuPy
git add app/routers/posts.py app/models/models.py app/services/post_visibility_service.py app/services/recommendation_service.py app/services/search_service.py app/services/topic_service.py app/services/moderation_effects.py tests/test_post_moderation_governance.py
git commit -m "feat: govern post publication revisions"
```

---

### Task 12: Integrate ordinary and comic comments without premature counters or notifications

**Files:**
- Modify: `app/routers/interactions.py`
- Modify: `app/routers/comic.py`
- Modify: `app/models/models.py:296-340,856-898`
- Modify: `app/services/moderation_effects.py`
- Create: `tests/test_comment_moderation_governance.py`
- Create: `tests/test_comic_comment_moderation_governance.py`

- [ ] **Step 1: Write failing parameterized comment tests**

For post targets, test root comment/reply create, ordinary-comment edit, approval, rejection, supersession and parent deletion. For comic-event targets, test root comment/reply create, approval, rejection and parent deletion; do not add a comic-comment edit endpoint because none exists today. Pending comments are author-only; public counts/reply counts remain unchanged. Approval increments target/parent counts and sends reply/mention notification exactly once. Rejection, cancellation, repeated result and stale parent produce no counters or notifications.

Assert restriction channel `comment` blocks both ordinary and comic comments with `PUBLISH_RESTRICTED` and a normalized expiry, before any row/counter is created.

- [ ] **Step 2: Run comment tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_comment_moderation_governance.py tests/test_comic_comment_moderation_governance.py -q
```

Expected: FAIL because current routes increment counters and commit the comment before governance approval.

- [ ] **Step 3: Apply the common comment lifecycle**

Use the `comment` and `comic_comment` adapters with identical status semantics. Route handlers reduce to this sequence:

```python
require_can_publish(
    db, user_id=current_user.id,
    channel=RestrictionScope.COMMENT, now=clock.now(),
)
outcome = moderation_service.moderate_public_text(
    text=payload.content,
    context=ModerationContext(
        target_type=target_type,
        actor_user_id=current_user.id,
        is_public=True,
    ),
)
publish = governance.submit_public_create(
    db, target_type=target_type, author_id=current_user.id,
    payload=adapter.validate_payload(payload), outcome=outcome,
)
db.commit()
moderation_effects.run_after_commit(publish.effects)
```

Validate parent belongs to the same target before staging the revision. Pending rows carry zero public side effects; public list/reply/count queries require approved moderation status except author-scoped pending projections.

Move parent reply count, target comment count, `NotificationService` and `MentionService` into named post-commit effects. Each effect obtains `effect:{case_id}:{name}` before changing business state, and updates counters with guarded SQL so retries cannot double increment. Return `moderation_status`, `case_id`, and current approved `comment` projection.

- [ ] **Step 4: Run comment and existing interaction regressions**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest \
  tests/test_comment_moderation_governance.py \
  tests/test_comic_comment_moderation_governance.py \
  tests/test_comment_like_contract.py -q
```

Expected: all tests PASS; liked/reply serialization remains compatible and pending content never affects public counts.

- [ ] **Step 5: Commit comment governance integration**

```bash
cd /d/NanTuPy
git add app/routers/interactions.py app/routers/comic.py app/models/models.py app/services/moderation_effects.py tests/test_comment_moderation_governance.py tests/test_comic_comment_moderation_governance.py
git commit -m "feat: govern comment publication and effects"
```

---

### Task 13: Apply restrictions and governance adapters to chat, community, comic-event and profile writes

**Files:**
- Modify: `.env.example`
- Modify: `app/core/config.py`
- Modify: `app/routers/health.py`
- Modify: `app/routers/chat.py`
- Modify: `app/routers/ws.py`
- Modify: `app/routers/communities.py`
- Modify: `app/services/community_service.py`
- Modify: `app/routers/comic.py`
- Modify: `app/routers/auth.py`
- Modify: `app/services/moderation_targets.py`
- Create: `tests/test_governed_write_paths.py`
- Create: `tests/test_chat_moderation_side_effects.py`

- [ ] **Step 1: Write failing all-entry restriction and privacy tests**

Parameterize HTTP chat, WebSocket private chat, community chat, community create/edit, join-request message, announcement create/edit, comic-event create/edit and profile text update. Assert every path calls stage-1 moderation and the correct restriction channel before persistence. `all_content` blocks every user-generated write; `chat` blocks private/community messages; `post`/`comment` remain scoped.

For `CONTENT_REJECTED` and `MODERATION_UNAVAILABLE`, assert no `Message`, no `WSMessageLog`, no `WSAckDedup` success row, no `last_message_at`, no fanout, notification or push. WebSocket failure frame must contain `type="ack"`, original `client_msg_id`, non-200 status, stable `code`, and safe user message.

- [ ] **Step 2: Run governed write tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_governed_write_paths.py tests/test_chat_moderation_side_effects.py -q
```

Expected: FAIL on missing restriction checks, revision orchestration or failure ACK fields.

- [ ] **Step 3: Integrate without reimplementing stage-1 moderation**

Add required `MODERATION_LOG_HASH_KEY` configuration and a placeholder-free `.env.example` description instructing operators to generate at least 32 random bytes; startup must fail outside tests when it is absent, and health exposes only `log_hash_configured: true/false`. At each entry call `require_can_publish()` first and the existing stage-1 `ModerationService` second. The realtime guard is:

```python
def authorize_realtime_text(
    db, *, user_id: int, content: str, channel: RestrictionScope,
) -> ModerationResult:
    require_can_publish(db, user_id=user_id, channel=channel, now=clock.now())
    result = moderation_service.moderate_realtime_text(
        text=content,
        context=ModerationContext(
            target_type="chat_message", actor_user_id=user_id, is_public=False,
        ),
    )
    if result.decision is not ModerationDecision.APPROVE:
        raise ModerationHTTPError.from_result(result)
    return result
```

Real-time private/community chat stays synchronous and local-only; `moderate_realtime_text()` never consults policy dispositions and never sends text to Provider. Do not create a cloud task, rejected message row, revision payload or appeal. For a local explicit reject, call `record_realtime_rejection()` with hashes/metadata only and then `record_violation()` in the same transaction before returning failure; infrastructure-unavailable and restriction failures create neither case nor violation. Moderation HTTP failures retain the Phase 1 `detail={"code": code, "message": safe_message}` shape; `PUBLISH_RESTRICTED` uses the locked 403 four-field detail. Emit WebSocket failure ACK using original `client_msg_id`, code, safe message, and status so the reliable client can settle its queue.

Public community/profile/comic-event creates and edits use Task 4 adapters and revisions. Pending announcements do not notify members; pending community/profile/event edits leave approved fields public. Join-request text is non-public and locally moderated before insert. Do not change existing COS upload implementation or add media quarantine logic.

Ensure `WSAckDedup` only records a durable successful message ID; a rejected client ID may be retried only according to its code and must never resolve to a server message.

- [ ] **Step 4: Run write-path, chat and community regressions**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest \
  tests/test_governed_write_paths.py \
  tests/test_chat_moderation_side_effects.py \
  tests/test_chat_message_type_contracts.py \
  tests/test_community_chat_contracts.py \
  tests/test_community_service_sql.py -q
```

Expected: all tests PASS and private chat tests prove no third-party call and no persisted rejected body.

- [ ] **Step 5: Commit remaining backend write-path integration**

```bash
cd /d/NanTuPy
git add .env.example app/core/config.py app/routers/health.py app/routers/chat.py app/routers/ws.py app/routers/communities.py app/services/community_service.py app/routers/comic.py app/routers/auth.py app/services/moderation_targets.py tests/test_governed_write_paths.py tests/test_chat_moderation_side_effects.py
git commit -m "feat: enforce governance across content writes"
```

---

### Task 14: Enforce append-only audit privacy and lock the Phase 4 maintenance boundary

**Files:**
- Modify: `app/services/moderation_service.py`
- Modify: `app/services/moderation_repository.py`
- Modify: `app/services/moderation_governance_service.py`
- Create: `tests/test_moderation_privacy.py`
- Create: `tests/test_moderation_phase_boundaries.py`

- [ ] **Step 1: Write failing privacy, immutability and negative-boundary tests**

Inject sentinel values for full body, JWT, cloud secret, Provider JSON, signed URL and database password; capture logs, API JSON and governance rows and assert no sentinel appears outside an allowed rejected public `ContentRevision.payload_json`. Assert private chat sentinel appears nowhere in governance persistence. Attempt ORM update/delete of `ModerationAction`, `UserViolation` and `UserViolationCompensation` through repository APIs and assert no such API exists; all corrections append a new row.

Add executable Phase 2 boundary assertions:

```python
assert not hasattr(ModerationRepository, "cleanup_retention")
assert not hasattr(ModerationRepository, "apply_risk_decay")
assert "cleanup" not in {item.value for item in ModerationTaskType}
assert profile.last_confirmed_violation_at == confirmed_at

fake_clock.advance(days=366)
assert rejected_revision.payload_json == original_payload
assert profile.risk_score == original_score
assert actions_named("risk_decay") == []
```

Also assert no Phase 2 Worker processor/CLI/Compose command schedules retention or decay. These are negative scope tests, not production lifecycle tests; exact 30/180/365-day cleanup and every-30-day decay behavior belong only to Phase 4.

- [ ] **Step 2: Run privacy and phase-boundary tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_privacy.py tests/test_moderation_phase_boundaries.py -q
```

Expected: FAIL if existing `text_preview`, raw exception text, unbounded notes, mutable ledger access, a cleanup task/processor, or automatic risk decay remains.

- [ ] **Step 3: Implement redaction and remove Phase 2 production maintenance behavior**

Replace content previews with identifiers and hashes:

```python
logger.warning(
    "moderation_blocked case_id=%s target_type=%s target_id=%s code=%s request_id=%s",
    case_id, target_type, target_id, error_code, request_id,
)
```

Create one `sanitize_error_message()` that removes control characters, credentials, query strings and signed URL parameters, then caps at 500 characters. Never log Authorization headers, request bodies, JWTs, DSNs or environment values. Ordinary API serializers use explicit allowlists.

Keep lifecycle metadata only: revision/case timestamps, appeal deadline inputs, profile `last_confirmed_violation_at`, violation/compensation ledgers, hashes and statuses. Remove any Phase 2 cleanup repository method, cleanup task type, Worker dispatch, periodic schedule, payload clearing, case/action deletion, elapsed-time score decrement or `risk_decay` action creation. Phase 4 will consume these fields and implement protected maintenance; Phase 2 only proves that advancing time cannot mutate retained data or risk.

- [ ] **Step 4: Run privacy, phase-boundary and Worker tests**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest \
  tests/test_moderation_privacy.py \
  tests/test_moderation_phase_boundaries.py \
  tests/test_moderation_worker.py \
  tests/test_moderation_health.py -q
```

Expected: all tests PASS; sentinel scan finds no leak, and boundary tests prove Phase 2 schedules neither cleanup nor decay.

- [ ] **Step 5: Commit audit privacy and phase boundaries**

```bash
cd /d/NanTuPy
git add app/services/moderation_service.py app/services/moderation_repository.py app/services/moderation_governance_service.py tests/test_moderation_privacy.py tests/test_moderation_phase_boundaries.py
git commit -m "feat: enforce moderation audit privacy"
```

---

### Task 15: Add Phase 2 Flutter governance models and services by reusing Phase 1 transport contracts

**Files:**
- Create: `lib/models/moderation.dart`
- Modify: `lib/models/post.dart`
- Modify: `lib/models/comment.dart`
- Modify: `lib/services/api/api_client.dart`
- Create: `lib/services/api/moderation_service.dart`
- Create: `test/moderation_models_test.dart`
- Create: `test/moderation_api_contract_test.dart`
- Test: `test/account_disabled_interceptor_test.dart`
- Test: `test/moderation_api_error_test.dart`
- Test: `test/local_db_moderation_migration_test.dart`

- [ ] **Step 1: Write failing governance-model and reuse-contract tests**

First import and exercise the Phase 1 `ApiResponse`, `ApiErrorCodes`, `ChatSendFailure`, Message `failureCode/failureMessage`, and Drift v2 migration; do not redeclare them. Then test snake_case/camelCase parsing, missing fields, partial `Post.mergeJson`, unknown moderation status, case/restriction pagination and exact restriction detail:

```dart
expect(Post.fromJson(json).moderationStatus, ModerationStatus.pendingReview);
expect(Comment.fromJson(json).caseId, 42);
expect(existing.mergeJson({'like_count': 3}).moderationStatus,
       ModerationStatus.pendingReview);
expect(response.statusCode, 403);
expect(response.errorCode, 'PUBLISH_RESTRICTED');
expect(response.restrictionScope, RestrictionScope.post);
expect(response.restrictedUntil!.toUtc().toIso8601String(),
       '2026-07-19T12:00:00.000Z');
```

Assert `PUBLISH_RESTRICTED` is non-retryable and parses `restriction_scope/expires_at`; rerun the existing Phase 1 test proving `ACCOUNT_DISABLED` at status 403 clears the session without refresh; unknown codes use a generic safe message. Add a source/behavior guard proving `AppDatabase.schemaVersion` remains 2 and no new `failure_code`/`failure_message` migration or duplicate `ApiResponse`/`ChatSendFailure` class is introduced by Phase 2.

- [ ] **Step 2: Run Flutter governance and Phase 1 contract tests and verify RED**

Run:

```bash
cd /d/FlutterProject/nonto
flutter test \
  test/moderation_models_test.dart \
  test/moderation_api_contract_test.dart \
  test/moderation_api_error_test.dart \
  test/local_db_moderation_migration_test.dart
```

Expected: Phase 1 API/chat/database tests PASS; new tests FAIL because governance models, restriction-scope parsing and `ModerationService` do not yet exist. A failure saying `ApiResponse`, `ChatSendFailure` or Drift v2 is missing means Phase 1 is incomplete; stop and finish Phase 1 rather than rebuilding it here.

- [ ] **Step 3: Implement only Phase 2 models and API additions**

Define Phase 2 models:

```dart
enum ModerationStatus { approved, pendingReview, rejected }
enum AppealStatus { manualReview, approved, rejected }
enum RestrictionScope { post, comment, chat, allContent }

class ModerationRestriction {
  final RestrictionScope scope;
  final DateTime expiresAt;
  const ModerationRestriction({required this.scope, required this.expiresAt});
}
```

Add `ModerationCaseSummary`, detail/revision projection and appeal result in `lib/models/moderation.dart`; add nullable `moderationStatus` defaulting to approved for legacy payloads and `caseId` to Post/Comment. Extend the existing Phase 1 parser/model in place only as needed to preserve typed `restrictionScope` and UTC `restrictedUntil` from the locked 403 detail. Keep the existing Phase 1 disabled-account interceptor unchanged: it already matches `errorCode == ACCOUNT_DISABLED` before ordinary 401 refresh logic, so canonical 403 clears session once and never refreshes. Do not redefine `ApiResponse`, `ApiErrorCodes` or `ChatSendFailure`; do not modify Message failure fields, Drift tables, generated database code, local DB mappings or `schemaVersion`.

`ModerationService` implements `getMyCases({statuses,cursor})`, `getCase(id)`, `submitAppeal(id,reason)`, and `getRestrictions()` against the exact Task 8 routes using the existing Phase 1 parser. Keep all existing constructor call sites valid through optional defaults.

- [ ] **Step 4: Run focused tests, Phase 1 regressions and analyzer**

Run:

```bash
cd /d/FlutterProject/nonto
flutter test \
  test/moderation_models_test.dart \
  test/moderation_api_contract_test.dart \
  test/moderation_api_error_test.dart \
  test/account_disabled_interceptor_test.dart \
  test/local_db_moderation_migration_test.dart
flutter analyze lib/models/moderation.dart lib/models/post.dart lib/models/comment.dart lib/services/api/api_client.dart lib/services/api/moderation_service.dart
```

Expected: tests PASS, status/scope/deadline parse exactly, `schemaVersion` remains 2, and analyzer reports no issues in listed files.

- [ ] **Step 5: Commit only Phase 2 Flutter contracts**

```bash
cd /d/FlutterProject/nonto
git add lib/models/moderation.dart lib/models/post.dart lib/models/comment.dart lib/services/api/api_client.dart lib/services/api/moderation_service.dart test/moderation_models_test.dart test/moderation_api_contract_test.dart
git commit -m "feat: add Flutter moderation governance contracts"
```

---

### Task 16: Make Flutter post and comment flows moderation-aware

**Files:**
- Modify: `lib/services/api/post_service.dart`
- Modify: `lib/services/api/comment_service.dart`
- Modify: `lib/providers/feed_notifier.dart`
- Modify: `lib/providers/comment_notifier.dart`
- Modify: `lib/providers/comment_state.dart`
- Modify: `lib/screens/post/create_post_screen.dart`
- Modify: `lib/widgets/post_card.dart`
- Modify: `lib/widgets/comment_section.dart`
- Create: `test/post_moderation_flow_test.dart`
- Create: `test/comment_moderation_flow_test.dart`

- [ ] **Step 1: Write failing notifier and widget tests**

Mock approved/pending/rejected/restricted responses. Assert approved post enters public feed; pending post does not enter feed and shows “内容审核中”; rejected rolls back local insertion and shows standardized text; pending edit keeps the old Post content and displays “编辑审核中”. Assert `FeedNotifier.insertNewPost()` refuses non-approved posts even if a caller invokes it.

For post and comic comment targets, assert pending optimistic comment remains author-local with an “审核中” badge and does not alter public callback count; rejected removes optimistic item and approved replaces it with server item. For ordinary post comments only, a pending edit preserves old public content. Assert `PUBLISH_RESTRICTED` shows deadline and `MODERATION_UNAVAILABLE` offers retry without exposing internals.

- [ ] **Step 2: Run Flutter flow tests and verify RED**

Run:

```bash
cd /d/FlutterProject/nonto
flutter test test/post_moderation_flow_test.dart test/comment_moderation_flow_test.dart
```

Expected: FAIL because current flows only branch on `resp.success` and insert optimistic content as published.

- [ ] **Step 3: Implement status-aware services, notifiers and badges**

Normalize create/edit response to a typed result:

```dart
class ModeratedContentResponse<T> {
  final ModerationStatus moderationStatus;
  final int? caseId;
  final T? content;
  const ModeratedContentResponse({
    required this.moderationStatus,
    this.caseId,
    this.content,
  });
}
```

In create-post screen: approved keeps current success navigation/cache/audio behavior; pending shows the review message, skips public feed insertion, and routes back safely; rejected/restricted/unavailable use the code-to-message mapper. In edit mode, merge only returned approved fields and retain current Post when response is pending.

Guard `FeedNotifier.insertNewPost()` with `post.moderationStatus == approved`. `CommentNotifier` tags pending author-local items by case ID, maintains an explicit moderation feedback field in `CommentState`, and rolls back only the matching optimistic ID. Add compact author-only badges to `PostCard` and `_CommentItem`; ordinary public users continue seeing approved content with no internal case metadata.

Do not alter media upload/COS behavior in `create_post_screen.dart`; status handling starts after the existing upload result and business API response.

- [ ] **Step 4: Run flow and existing widget regressions**

Run:

```bash
cd /d/FlutterProject/nonto
flutter test \
  test/post_moderation_flow_test.dart \
  test/comment_moderation_flow_test.dart \
  test/nonto_create_post_phase5a_regression_test.dart \
  test/post_visibility_task8_test.dart
flutter analyze lib/services/api/post_service.dart lib/services/api/comment_service.dart lib/providers/feed_notifier.dart lib/providers/comment_notifier.dart lib/providers/comment_state.dart lib/screens/post/create_post_screen.dart lib/widgets/post_card.dart lib/widgets/comment_section.dart
```

Expected: tests PASS and analyzer reports no issues in listed files.

- [ ] **Step 5: Commit Flutter publication states**

```bash
cd /d/FlutterProject/nonto
git add lib/services/api/post_service.dart lib/services/api/comment_service.dart lib/providers/feed_notifier.dart lib/providers/comment_notifier.dart lib/providers/comment_state.dart lib/screens/post/create_post_screen.dart lib/widgets/post_card.dart lib/widgets/comment_section.dart test/post_moderation_flow_test.dart test/comment_moderation_flow_test.dart
git commit -m "feat: show moderated post and comment states"
```

---

### Task 17: Integrate governance restriction responses into the existing Phase 1 chat failure flow

**Files:**
- Modify: `lib/services/api/community_service.dart`
- Modify: `lib/screens/chat/chat_room_screen.dart`
- Modify: `lib/screens/community/community_chat_screen.dart`
- Create: `test/chat_governance_restriction_test.dart`
- Create: `test/community_chat_governance_restriction_test.dart`
- Test: `test/chat_moderation_failure_test.dart`
- Test: `test/chat_reliability_regression_test.dart`
- Test: `test/local_db_moderation_migration_test.dart`

- [ ] **Step 1: Write failing integration tests that import Phase 1 contracts**

Use the existing Phase 1 `ChatSendFailure`, send-error stream, queue settlement, Message `failureCode/failureMessage`, manual-retry rules and Drift v2 storage as test fixtures. Feed a 403 `PUBLISH_RESTRICTED` failed ACK carrying the original `client_msg_id`, `restriction_scope="chat"`, and UTC `expires_at`; assert the exact local message becomes failed, displays the safe restriction deadline, does not update preview/play sound/fanout, and is not retryable. An ACK without a matching ID must not fail another queued message.

For HTTP community chat, return the existing structured `ApiResponse` with the same 403 detail; assert the optimistic item uses the already-existing Phase 1 failed-message path and no new map-only `failure_code/failure_message` storage model is introduced. Existing `CONTENT_REJECTED`, `MODERATION_UNAVAILABLE`, transport retry and upload-failure behavior must remain unchanged.

- [ ] **Step 2: Run new integration plus Phase 1 chat/storage tests and verify RED**

Run:

```bash
cd /d/FlutterProject/nonto
flutter test \
  test/chat_governance_restriction_test.dart \
  test/community_chat_governance_restriction_test.dart \
  test/chat_moderation_failure_test.dart \
  test/chat_reliability_regression_test.dart \
  test/local_db_moderation_migration_test.dart
```

Expected: existing Phase 1 tests PASS; new tests FAIL only because the Phase 2 restriction detail/deadline is not yet rendered through the existing failure path. If failure says `ChatSendFailure`, queue settlement, Message failure fields or Drift v2 is absent, stop and complete Phase 1 rather than implementing those foundations here.

- [ ] **Step 3: Reuse the existing typed failure path without redefining it**

Map the existing parsed restriction response to the existing `ChatSendFailure` constructor and preserve `clientMsgId`, `code`, safe message and `retryable=false`. Carry restriction scope/deadline using the existing Phase 1 metadata extension seam or a Phase 2 companion value at the UI boundary; do not declare another `ChatSendFailure`, alter Message columns, add local database fields, regenerate Drift code or increment `schemaVersion`.

In private and community chat screens, format the UTC deadline for display, retain the local failed text, disable retry for `PUBLISH_RESTRICTED`, and leave Phase 1 queue progression untouched. `CommunityApiService.sendMessage()` returns the original parsed `ApiResponse`; do not convert it to boolean/string exceptions. The implementation must not update conversation preview, play send sound or mark a server message successful before the API succeeds.

- [ ] **Step 4: Run chat reliability, Phase 1 storage and analyzer checks**

Run:

```bash
cd /d/FlutterProject/nonto
flutter test \
  test/chat_governance_restriction_test.dart \
  test/community_chat_governance_restriction_test.dart \
  test/chat_moderation_failure_test.dart \
  test/chat_reliability_regression_test.dart \
  test/chat_message_types_contract_test.dart \
  test/local_db_moderation_migration_test.dart
flutter analyze lib/services/api/community_service.dart lib/screens/chat/chat_room_screen.dart lib/screens/community/community_chat_screen.dart
```

Expected: tests PASS; queue ordering and failed-ACK matching remain intact, generated/database files are unchanged, and analyzer reports no issues.

- [ ] **Step 5: Commit only governance integration files**

```bash
cd /d/FlutterProject/nonto
git add lib/services/api/community_service.dart lib/screens/chat/chat_room_screen.dart lib/screens/community/community_chat_screen.dart test/chat_governance_restriction_test.dart test/community_chat_governance_restriction_test.dart
git commit -m "feat: show chat governance restrictions"
```

---

### Task 18: Add the user-side “My Moderation” experience

**Files:**
- Create: `lib/providers/moderation_notifier.dart`
- Create: `lib/screens/profile/my_moderation_screen.dart`
- Modify: `lib/routes/app_routes.dart`
- Modify: `lib/routes/route_generator.dart`
- Modify: `lib/screens/profile/settings_screen.dart`
- Create: `test/my_moderation_notifier_test.dart`
- Create: `test/my_moderation_screen_test.dart`

- [ ] **Step 1: Write failing provider, route and screen tests**

Test initial load, cursor pagination, pending/rejected filters, pull-to-refresh, restriction display, empty/error/retry states, and one successful/duplicate appeal. Widget tests assert only authenticated users reach the route, settings contains “我的审核”, cards show target type/status/time without rejected body or internal rule data, restriction deadlines are localized, and appealed cases disable the appeal button.

- [ ] **Step 2: Run “My Moderation” tests and verify RED**

Run:

```bash
cd /d/FlutterProject/nonto
flutter test test/my_moderation_notifier_test.dart test/my_moderation_screen_test.dart
```

Expected: FAIL because provider, route and screen are missing.

- [ ] **Step 3: Implement the authenticated user page**

Add `AppRoutes.myModeration` and register it with the existing auth guard in `RouteGenerator`. The notifier owns immutable state:

```dart
class ModerationState {
  final List<ModerationCaseSummary> cases;
  final ModerationRestriction? restrictions;
  final bool isLoading;
  final bool isLoadingMore;
  final String? nextCursor;
  final String? error;
  final Set<int> appealingCaseIds;
}
```

The screen contains pending/rejected filter chips, restriction summary, case cards, pull-to-refresh and paginated loading. Appeal opens a bounded multiline reason dialog, calls `submitAppeal`, keeps the case rejected while setting its `appealStatus` to `manualReview`, and maps 409 duplicate to “该内容已提交过申诉”. Add the settings entry under account/security. Do not expose or link administrator routes and do not render Provider/rule details.

- [ ] **Step 4: Run page, settings and analyzer regressions**

Run:

```bash
cd /d/FlutterProject/nonto
flutter test \
  test/my_moderation_notifier_test.dart \
  test/my_moderation_screen_test.dart \
  test/nonto_settings_phase4f_regression_test.dart
flutter analyze lib/providers/moderation_notifier.dart lib/screens/profile/my_moderation_screen.dart lib/routes/app_routes.dart lib/routes/route_generator.dart lib/screens/profile/settings_screen.dart
```

Expected: tests PASS and analyzer reports no issues in listed files.

- [ ] **Step 5: Commit the user moderation screen**

```bash
cd /d/FlutterProject/nonto
git add lib/providers/moderation_notifier.dart lib/screens/profile/my_moderation_screen.dart lib/routes/app_routes.dart lib/routes/route_generator.dart lib/screens/profile/settings_screen.dart test/my_moderation_notifier_test.dart test/my_moderation_screen_test.dart
git commit -m "feat: add my moderation screen"
```

---

### Task 19: Extend Flutter reporting without breaking existing post/comment methods

**Files:**
- Modify: `lib/services/api/report_service.dart`
- Modify: `lib/widgets/post_card.dart`
- Modify: `lib/widgets/comment_section.dart`
- Modify: `lib/screens/chat/chat_room_screen.dart`
- Create: `test/report_service_compatibility_test.dart`
- Create: `test/report_entry_points_test.dart`

- [ ] **Step 1: Write failing compatibility and UI tests**

Assert existing `reportPost(int,String)` and `reportComment(int,String)` signatures and success messages remain valid. Test generic `reportTarget(targetType,targetId,reason,{description})` JSON, message/community/comic-event/comic-comment target mapping, duplicate 409 user message, and safe generic failures. Widget tests verify post reporting still works, ordinary/comic comments expose report action, and a received chat message exposes report action without logging or copying its body to request description.

- [ ] **Step 2: Run report compatibility tests and verify RED**

Run:

```bash
cd /d/FlutterProject/nonto
flutter test test/report_service_compatibility_test.dart test/report_entry_points_test.dart
```

Expected: FAIL because the generic target API and missing report entry points are absent.

- [ ] **Step 3: Add a generic method behind compatible wrappers**

Implement:

```dart
Future<ApiResponse<Map<String, dynamic>>> reportTarget(
  String targetType,
  int targetId,
  String reason, {
  String? description,
});

Future<ApiResponse<dynamic>> reportPost(int id, String reason) =>
    reportTarget('post', id, reason);
Future<ApiResponse<dynamic>> reportComment(int id, String reason) =>
    reportTarget('comment', id, reason);
```

Keep legacy endpoint parsing compatible if older server responses are encountered, but send new targets to `/api/reports`. Reuse the existing reason selector and “举报已提交” message. Add report menu actions only where the current user is not the target owner/sender. Chat submits message ID, reason and optional user-entered description only; never prefill full message text. Map `REPORT_ALREADY_EXISTS` to “你已举报过该内容”.

- [ ] **Step 4: Run reporting and affected widget regressions**

Run:

```bash
cd /d/FlutterProject/nonto
flutter test \
  test/report_service_compatibility_test.dart \
  test/report_entry_points_test.dart \
  test/chat_reliability_regression_test.dart \
  test/post_visibility_task8_test.dart
flutter analyze lib/services/api/report_service.dart lib/widgets/post_card.dart lib/widgets/comment_section.dart lib/screens/chat/chat_room_screen.dart
```

Expected: tests PASS, analyzer reports no issues, and existing post/comment report behavior is unchanged.

- [ ] **Step 5: Commit report compatibility**

```bash
cd /d/FlutterProject/nonto
git add lib/services/api/report_service.dart lib/widgets/post_card.dart lib/widgets/comment_section.dart lib/screens/chat/chat_room_screen.dart test/report_service_compatibility_test.dart test/report_entry_points_test.dart
git commit -m "feat: extend moderation report targets"
```

---

### Task 20: Run phase-2 acceptance, migration and privacy verification

**Files:**
- Create: `tests/test_moderation_phase2_acceptance.py`
- Create: `test/moderation_phase2_acceptance_test.dart`

- [ ] **Step 1: Write end-to-end acceptance tests before final fixes**

Backend acceptance covers: all four `PolicyDisposition` values; `PROVIDER_REVIEW` safely becoming a manual case with zero Provider calls/tasks/queue consumption in Phase 2; realtime moderation returning `ModerationResult` with zero Provider calls; approved/pending/rejected create; old-version preservation; stale result; claim crash/recovery; `NormalizedTaskResult` completion and `ProviderStillProcessing` control flow; duplicate result; report-to-case-to-admin resolution; one appeal and compensation; `low/medium/high -> 1/3/8`; all four typed restriction scopes with exact 403 detail; `ACCOUNT_DISABLED` 403 across login/HTTP/WS; integer `rule_version` and string `policy_version`; no side effects before approval; health degradation without credentials; no retention/decay execution; and sentinel privacy scan. Flutter acceptance covers post/comment status, reuse of Phase 1 chat failure handling, exact restriction scope/deadline, “我的审核”, legacy report calls, unchanged Drift schema version 2, and no administrator UI.

- [ ] **Step 2: Run acceptance tests and verify RED independently**

Before saving each new acceptance file, temporarily replace the backend API/Flutter service dependency with a throwing stub named `UnwiredPhase2Boundary`. Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_phase2_acceptance.py -q
cd /d/FlutterProject/nonto
flutter test test/moderation_phase2_acceptance_test.dart
```

Expected: both commands FAIL at the first boundary assertion with `UnwiredPhase2Boundary`; this proves the acceptance tests execute the intended full path rather than passing vacuously. Remove only the throwing stubs after observing RED.

- [ ] **Step 3: Bind acceptance tests to the production boundaries**

Use concrete test seams:

```python
app.dependency_overrides[get_clock] = lambda: fake_clock
app.dependency_overrides[get_current_user] = lambda: author
app.dependency_overrides[get_moderation_service] = lambda: fake_moderator
effects = RecordingModerationEffects()
```

```dart
final container = ProviderContainer(overrides: [
  apiClientProvider.overrideWithValue(fakeApi),
  moderationServiceProvider.overrideWithValue(fakeModeration),
  webSocketServiceProvider.overrideWithValue(fakeWebSocket),
]);
```

Use the isolated test database for repository/lease operations. Do not edit production files in this task and do not add alternate states, direct status updates, direct local-filter calls, cloud Provider calls, COS changes or administrator Flutter screens.

- [ ] **Step 4: Run complete verification**

Backend:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_phase1_contracts.py tests/test_moderation_phase2_service_contracts.py tests/test_moderation_phase1_write_paths.py tests/test_inactive_auth.py -q
./.venv/Scripts/python.exe -m pytest \
  tests/test_moderation_models.py \
  tests/test_moderation_migration.py \
  tests/test_moderation_state_machine.py \
  tests/test_moderation_repository.py \
  tests/test_moderation_targets.py \
  tests/test_content_revisions.py \
  tests/test_publish_restrictions.py \
  tests/test_violation_ledger.py \
  tests/test_reports_governance.py \
  tests/test_admin_moderation_api.py \
  tests/test_moderation_audit.py \
  tests/test_user_moderation_api.py \
  tests/test_moderation_appeals.py \
  tests/test_inactive_governance.py \
  tests/test_moderation_worker.py \
  tests/test_moderation_health.py \
  tests/test_post_moderation_governance.py \
  tests/test_comment_moderation_governance.py \
  tests/test_comic_comment_moderation_governance.py \
  tests/test_governed_write_paths.py \
  tests/test_chat_moderation_side_effects.py \
  tests/test_moderation_privacy.py \
  tests/test_moderation_phase_boundaries.py \
  tests/test_moderation_phase2_acceptance.py -q
./.venv/Scripts/python.exe -m alembic upgrade head
./.venv/Scripts/python.exe -m alembic heads
```

Expected: all tests PASS, upgrade succeeds, and exactly one head is printed.

Flutter:

```bash
cd /d/FlutterProject/nonto
flutter test \
  test/moderation_models_test.dart \
  test/moderation_api_contract_test.dart \
  test/post_moderation_flow_test.dart \
  test/comment_moderation_flow_test.dart \
  test/chat_governance_restriction_test.dart \
  test/community_chat_governance_restriction_test.dart \
  test/chat_moderation_failure_test.dart \
  test/local_db_moderation_migration_test.dart \
  test/my_moderation_notifier_test.dart \
  test/my_moderation_screen_test.dart \
  test/report_service_compatibility_test.dart \
  test/report_entry_points_test.dart \
  test/moderation_phase2_acceptance_test.dart
flutter test
flutter analyze
```

Expected: all tests PASS and analyzer reports no issues. Manually verify the API health endpoint with docs hidden, run API and Worker as separate processes, and confirm a pending public item never appears in another account's feed.

- [ ] **Step 5: Commit acceptance coverage and final integration fixes**

```bash
cd /d/NanTuPy
git add tests/test_moderation_phase2_acceptance.py
git commit -m "test: verify moderation governance phase two"
cd /d/FlutterProject/nonto
git add test/moderation_phase2_acceptance_test.dart
git commit -m "test: verify Flutter moderation governance"
```

Expected: each repository is clean after its commit; no Tencent/COS implementation or administrator UI file is present in either commit.

---

## Final self-review checklist

- [ ] **Specification coverage:** Map the shared `ModerationService` extension and four policy dispositions to Task 1; schema/enums/version types to Task 2; normalized completion/claims/leases/idempotency and Phase 3 adapter seam to Task 3; provider-review-to-manual downgrade and revisions/adapters to Task 4; severity/ledger/restrictions/no-decay boundary to Task 5; reports to Tasks 6–7 and 19; backend-only administrator API to Task 7; user cases/appeals to Tasks 8 and 18; 403 `ACCOUNT_DISABLED` to Task 9; Worker/health/no-Provider-processor to Task 10; write interfaces/side effects to Tasks 11–13; privacy/audit/no-maintenance boundary to Task 14; Phase 1 Flutter contract reuse plus Phase 2 models/flows/user page to Tasks 15–19; acceptance to Task 20.
- [ ] **Placeholder scan:** From `D:\NanTuPy`, run the following exact script; expected output is `placeholder scan clean`:

  ```bash
  ./.venv/Scripts/python.exe - <<'PY'
  from pathlib import Path

  text = Path("docs/superpowers/plans/2026-07-18-content-moderation-phase2-governance.md").read_text(encoding="utf-8")
  forbidden = [
      "TB" + "D", "TO" + "DO", "implement " + "later",
      "fill in " + "details", "类似" + "任务",
      "similar to " + "Task", "appropriate error " + "handling",
      "." * 3,
  ]
  found = [term for term in forbidden if term in text]
  assert not found, found
  print("placeholder scan clean")
  PY
  ```
- [ ] **Type consistency:** Verify every later task uses the exact locked names and values: `PolicyDisposition`, `PublicTextModerationOutcome`, `Severity`, `ModerationTaskType`, `ModerationCaseSource`, `ModerationProvider`, `NormalizedTaskDecision`, `NormalizedTaskResult`, `ProviderStillProcessing`, `RestrictionScope`, `CaseStatus`, `PublicModerationStatus`, `TaskStatus`, `TargetSnapshot`, `PublishDecision`, `ModerationTargetAdapter`, `ModerationRepository`, Phase 1 `ChatSendFailure`, and Flutter `ModerationStatus`. Confirm the `result` argument passed to `complete_task` is always `NormalizedTaskResult`, `rule_version` is always `int`, and `policy_version` is always `str`.
- [ ] **Error consistency:** Search both repositories for legacy/alternate disabled-account codes and require zero matches; verify every disabled-account HTTP response is 403 `ACCOUNT_DISABLED`. Verify every `PUBLISH_RESTRICTED` HTTP response is 403 with exactly `code/message/restriction_scope/expires_at`, typed scope values and UTC `Z` deadline.
- [ ] **Migration consistency:** Re-run `./.venv/Scripts/python.exe -m alembic heads`; verify one head and verify the phase-2 migration's `down_revision` equals the head observed immediately before it was generated, not a revision copied from this plan or earlier investigation. In Flutter, verify `schemaVersion == 2`, generated DB files are unchanged by Phase 2, and no duplicate failure column migration exists.
- [ ] **Privacy and phase boundary:** Search changed files for full-body log previews, Authorization/JWT values, secrets, signed URL persistence, Provider response persistence, Tencent moderation client calls, quarantine/public COS movement, Provider task creation from Phase 2 `PROVIDER_REVIEW`, retention cleanup, automatic risk decay, duplicate `ApiResponse`/`ChatSendFailure`, and administrator Flutter routes; expected: none introduced by Phase 2.
- [ ] **Task/TDD/header review:** Verify the required plan header is intact; task headings are consecutive `Task 1` through `Task 20` with no duplicate/missing number; every task has explicit Files plus RED/GREEN/commit steps and exact commands/expected outcomes; no task references a later-only type without defining it in locked contracts.
- [ ] **Working-tree scope:** Review `git diff --stat` and `git status --short` in both repositories; expected at final implementation completion: only intentional phase-2 files before commits, then clean working trees after commits.
