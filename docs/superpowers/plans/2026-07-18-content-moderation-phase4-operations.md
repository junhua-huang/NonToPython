# Content Moderation Phase 4 Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在阶段 1–3 已完整上线且通过门禁的前提下，实现公开历史内容 dry-run 评估与执行回扫、可恢复运维任务、保留期清理、风险分衰减、可观测性以及可在宝塔 Docker 环境验证的分角色部署。

**Architecture:** FastAPI 单体和阶段 1–3 审核域保持不变；阶段 4 在其外增加独立的 operations 模型、公开目标 backfill adapter、CAS 游标调度器、低优先级 backfill worker 和 maintenance worker。评估模式只写脱敏统计与评估项，执行模式必须引用已完成评估并通过不同幂等键调用阶段 2–3 的案件、处置、Provider 和风险账本接口；API、实时审核 Worker、backfill、maintenance 与 migration 使用同一镜像但运行不同角色。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x synchronous ORM, Alembic, MySQL 8/PyMySQL, Pydantic 2, pytest, Docker Compose, Tencent Cloud moderation Provider from phase 3, Flutter/Dart regression gates

---

## Scope, phase boundary, and execution rules

- 阶段 1、2、3 是硬前置。Task 1 任一命令失败即停止；先回到对应阶段计划完成修复，不在本计划补建统一文本审核、治理 API、内容版本、举报/申诉、腾讯云 Provider、COS 隔离上传或 Flutter 审核 UI。
- 后端命令从 `D:\NanTuPy`（Git Bash 为 `/d/NanTuPy`）运行；Flutter 门禁从 `D:\FlutterProject\nonto`（Git Bash 为 `/d/FlutterProject/nonto`）运行。
- 本计划只回扫公开内容。`Message`、`WSMessageLog`、私聊/实时聊天正文及其历史记录均不注册 adapter；历史聊天媒体也不进入阶段 4 backfill。禁止媒体用途精确复用阶段 3 `UploadPurpose`：`direct_chat_image`、`direct_chat_video`、`community_chat_image`、`community_chat_video`、`identity_evidence_image`。
- Provider 边界锁定：私聊与实时聊天文本永不发送 TMS；聊天媒体只允许在首次阶段 3 上传审核中按类型发送 IMS/VM；阶段 4 不把任何历史聊天文本或媒体重新发送到 TMS/IMS/VM。公开历史文本可按既有文本策略评估，公开历史媒体（含阶段 3 `legacy_imported` `UploadObject`）可按既有 IMS/VM 策略评估。
- 所有扫描使用单调递增主键，不使用 offset。每个 target type 在运行创建时保存不可增长的 `snapshot_upper_bound_id`；本轮只读取 `last_scanned_id < id <= snapshot_upper_bound_id`。
- `assessment` 是 dry-run：文本和每个媒体对象可独立评估、计数并写脱敏 item，禁止隐藏、删除、处罚、创建治理案件、追加用户违规或发布业务副作用。`enforcement` 必须引用一个已完成的 assessment run，按业务 owner 聚合该 revision 的全部 assessment item，并核验 owner 当前版本与 `owner_revision_hash` 未变后才可执行。
- Repository/service 内只 `flush()`；CLI 或 worker 管理事务。外部 Provider/COS I/O 不能处于数据库事务中。claim 必须先提交，完成使用新事务与 claim token 条件更新。
- 阶段 4 计划中的每个实现任务均含独立 commit 步骤；编写本计划本身不提交。Flutter 在阶段 4 只运行阶段 1–3 与现有回归门禁，不编辑、不生成、不提交任何 Flutter 文件，也不修改 Flutter model/JSON/API schema 或生成代码。
- 计划调查时仓库当前只有阶段前的 Alembic head；实施 Task 2 时必须动态读取完成阶段 1–3 后届时真实且唯一的 head。迁移文件名/`rev-id` 的日期编号只是本次 revision 编号，不代表 parent；`down_revision` 只能取紧邻生成前动态读取的唯一 live head，不能复制调查值、文件编号或本文中的日期推断。

## Locked files and canonical interfaces

### Backend files

- `app/models/moderation_operations.py`: backfill run/cursor/per-asset item/owner decision/heartbeat、共享速率桶、maintenance run/item、legal hold、风险调整账本 ORM；不存正文、媒体 URL 或 Provider 原始响应。
- `app/services/moderation_backfill_types.py`: 固定枚举、DTO、幂等键和运行摘要类型。
- `app/services/moderation_backfill_targets.py`: 公开历史目标 adapter 协议和显式注册表；唯一允许历史扫描选择业务表的位置。
- `app/services/moderation_backfill_repository.py`: run/cursor/item CAS、租约、心跳、暂停恢复和原子计数。
- `app/services/moderation_backfill_service.py`: assessment/enforcement 编排；复用阶段 1–3 service/repository/provider，不复制规则或处置状态机。
- `app/services/moderation_rate_limiter.py`: 进程内平滑令牌桶与数据库共享秒级配额两层限速。
- `app/services/moderation_metrics.py`: 固定低基数指标快照与 Prometheus text exposition。
- `app/services/moderation_retention.py`: 30/180/365 天候选、保护规则、脱敏/删除与 COS 清理编排。
- `app/services/moderation_risk_decay.py`: 每完整 30 天无新确认违规减 1 分且追加不可变账本。
- `app/workers/moderation_backfill_worker.py`: 低优先级历史回扫 worker。
- `app/workers/moderation_maintenance_worker.py`: retention 与 risk-decay worker。
- `app/cli/moderation_operations.py`: create/status/pause/resume/cancel、legal hold、one-shot maintenance、worker health CLI。
- `app/routers/health.py`: `/live`、`/ready`、`/moderation` 和低基数 metrics endpoint。
- `deploy/entrypoint.sh`: API-only 入口，不运行迁移。
- `deploy/migration-entrypoint.sh`: 独立 Alembic migration job。
- `deploy/moderation-worker-entrypoint.sh`: 阶段 2–3 实时审核任务 worker。
- `deploy/moderation-backfill-entrypoint.sh`: 阶段 4 backfill worker。
- `deploy/moderation-maintenance-entrypoint.sh`: 阶段 4 maintenance worker。
- `deploy/README_BAOTA_DOCKER.md`: 宝塔 dry-run、执行、暂停、恢复、告警、升级和回滚 runbook。

### Canonical operation types

```python
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, Sequence

from app.services.moderation_types import (
    NormalizedTaskDecision,
    NormalizedTaskResult,
    RiskCategory,
    Severity,
)

# Phase 4 defines only operations-specific enums. Provider/task decisions,
# NormalizedTaskDecision, RiskCategory, Severity, NormalizedTaskResult, and
# integer rule_version are imported from phase-2 canonical moderation_types.
class BackfillMode(StrEnum):
    ASSESSMENT = "assessment"
    ENFORCEMENT = "enforcement"

class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"

class CursorStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"

class ItemOutcome(StrEnum):
    LOCAL_APPROVED = "local_approved"
    PROVIDER_APPROVED = "provider_approved"
    HIGH_CONFIDENCE_REJECTED = "high_confidence_rejected"
    MANUAL_REVIEW = "manual_review"
    ENFORCED_HIDDEN = "enforced_hidden"
    CHANGED_SINCE_ASSESSMENT = "changed_since_assessment"
    DELETED_OR_PRIVATE = "deleted_or_private"
    MANUAL_INVENTORY = "manual_inventory"
    ERROR = "error"

class MaintenanceJobType(StrEnum):
    RETENTION_30D = "retention_30d"
    RETENTION_180D = "retention_180d"
    RETENTION_365D = "retention_365d"
    RISK_DECAY = "risk_decay"

@dataclass(frozen=True)
class PublicBackfillSnapshot:
    scan_type: str
    scan_id: int
    target_type: str
    target_id: int
    author_id: int
    version: int
    content_hash: str
    owner_revision_hash: str
    text_parts: tuple[str, ...]
    media_object_ids: tuple[int, ...]
    updated_at: datetime

@dataclass(frozen=True)
class LegacyMediaInventoryCandidate:
    scan_type: str
    scan_id: int
    inventory_reason_code: str

BackfillCandidate = PublicBackfillSnapshot | LegacyMediaInventoryCandidate

@dataclass(frozen=True)
class ClaimedCursor:
    cursor_id: int
    run_id: int
    target_type: str
    last_scanned_id: int
    snapshot_upper_bound_id: int
    claim_token: str
    lease_expires_at: datetime

class PublicBackfillTargetAdapter(Protocol):
    target_type: str
    def snapshot_upper_bound(self, db) -> int: ...
    def fetch_batch(
        self, db, *, after_id: int, upper_bound_id: int, limit: int,
    ) -> Sequence[BackfillCandidate]: ...
    def load_current(self, db, *, target_id: int) -> PublicBackfillSnapshot | None: ...
```

### Locked target registry

Only these public adapters may be registered:

```python
PUBLIC_BACKFILL_TARGETS = {
    "post": PostBackfillAdapter(),
    "comment": CommentBackfillAdapter(),
    "user_profile": UserProfileBackfillAdapter(),
    "community": CommunityBackfillAdapter(),
    "community_announcement": CommunityAnnouncementBackfillAdapter(),
    "comic_event": ComicEventBackfillAdapter(),
    "comic_comment": ComicCommentBackfillAdapter(),
    "post_media": PostMediaBackfillAdapter(),
    "user_profile_media": UserProfileMediaBackfillAdapter(),
    "community_media": CommunityMediaBackfillAdapter(),
    "comic_event_media": ComicEventMediaBackfillAdapter(),
    "legacy_public_media": LegacyPublicMediaBackfillAdapter(),
}

FORBIDDEN_BACKFILL_MODELS = {"Message", "WSMessageLog"}
FORBIDDEN_MEDIA_PURPOSES = {
    UploadPurpose.DIRECT_CHAT_IMAGE,
    UploadPurpose.DIRECT_CHAT_VIDEO,
    UploadPurpose.COMMUNITY_CHAT_IMAGE,
    UploadPurpose.COMMUNITY_CHAT_VIDEO,
    UploadPurpose.IDENTITY_EVIDENCE_IMAGE,
}
```

Text snapshots concatenate only the adapter's documented public fields in memory and never persist that concatenated text. Normal public media adapters select non-`legacy_imported` phase-3 `UploadObject` rows with a verified public business owner, `status="published"`, a non-null `public_key`, and a purpose outside `FORBIDDEN_MEDIA_PURPOSES`. `legacy_public_media` inventories every non-forbidden phase-3 `legacy_imported` `UploadObject` regardless of whether modern owner binding is already complete: safely mapped, currently public owner rows enter the appropriate media assessment; the five forbidden chat/private/evidence purposes are excluded in SQL before snapshot/item creation and never reach phase 4; every remaining unmappable or ambiguous row writes a body/URL-free `MANUAL_INVENTORY` item with a stable reason code. Historical public media must never be silently ignored merely because it predates modern owner metadata.

### Locked idempotency keys

```python
def assessment_key(run_id: int, snapshot: PublicBackfillSnapshot, asset_kind: str) -> str:
    return (
        f"backfill:assessment:{run_id}:{snapshot.scan_type}:{snapshot.scan_id}:"
        f"{snapshot.target_type}:{snapshot.target_id}:{snapshot.version}:"
        f"{snapshot.content_hash}:{asset_kind}"
    )

def enforcement_key(snapshot: PublicBackfillSnapshot) -> str:
    return (
        f"backfill:enforcement:{snapshot.target_type}:{snapshot.target_id}:"
        f"{snapshot.version}:{snapshot.owner_revision_hash}"
    )
```

The assessment key intentionally contains its run ID and per-text/per-media `asset_kind` because repeated dry-runs and each media object are independently measurable. The enforcement key is owner-level and exactly `backfill:enforcement:{target_type}:{target_id}:{target_version}:{owner_revision_hash}`; it omits run ID, asset ID, and decision so one target revision with multiple media can create at most one case, hide, and violation across all execution runs. `owner_revision_hash` canonically hashes the owner target type/ID/version plus the sorted complete set of public text and media content hashes; enforcement refuses incomplete owner groups. Neither key contains body text, URL, token, credential, username, or email.

---

### Task 1: Enforce phases 1–3 as a stop-the-line gate

**Files:**
- Verify: `app/services/moderation_service.py`
- Verify: `app/services/moderation_repository.py`
- Verify: `app/services/moderation_targets.py`
- Verify: `app/services/publish_restrictions.py`
- Verify: `app/workers/moderation_worker.py`
- Verify: `app/services/tencent_moderation_provider.py`
- Verify: `app/services/media_moderation_service.py`
- Verify: `app/services/upload_service.py`
- Verify: `app/models/moderation.py`
- Verify: `app/models/media.py`
- Test: `tests/test_moderation_phase1_contracts.py`
- Test: `tests/test_moderation_phase1_write_paths.py`
- Test: `tests/test_moderation_phase2_acceptance.py`
- Test: `tests/test_moderation_phase3_acceptance.py`
- Test: `tests/test_moderation_privacy.py`
- Test: `tests/test_moderation_retention.py`
- Test: `tests/test_moderation_worker.py`
- Test: `tests/test_tencent_moderation_provider.py`
- Test: `tests/test_media_publish_saga.py`
- Test: `test/moderation_phase2_acceptance_test.dart`
- Test: `test/moderation_phase3_acceptance_test.dart`

- [ ] **Step 1: Verify the prerequisite import surface without editing it**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe - <<'PY'
from app.models.moderation import ModerationCase, ModerationTask, UserModerationProfile
from app.models.media import UploadObject, UploadSession
from app.services.moderation_service import ModerationService
from app.services.moderation_repository import ModerationRepository
from app.services.moderation_targets import TARGET_ADAPTERS
from app.services.publish_restrictions import require_can_publish
from app.services.media_types import ModerationProvider, UploadObjectStatus, UploadPurpose
from app.workers import moderation_worker
from app.services import media_moderation_service, tencent_moderation_provider

assert ModerationCase.__tablename__ == "moderation_cases"
assert ModerationTask.__tablename__ == "moderation_tasks"
assert UploadObject.__tablename__ == "upload_objects"
assert UploadObjectStatus.PUBLISHED.value == "published"
assert UploadPurpose.DIRECT_CHAT_IMAGE.value == "direct_chat_image"
assert UploadPurpose.DIRECT_CHAT_VIDEO.value == "direct_chat_video"
assert UploadPurpose.COMMUNITY_CHAT_IMAGE.value == "community_chat_image"
assert UploadPurpose.COMMUNITY_CHAT_VIDEO.value == "community_chat_video"
assert UploadPurpose.IDENTITY_EVIDENCE_IMAGE.value == "identity_evidence_image"
assert {"post", "comment", "user_profile", "community", "community_announcement", "comic_event", "comic_comment"} <= set(TARGET_ADAPTERS)
print("phase 1-3 imports verified")
PY
```

Expected: exit 0 and `phase 1-3 imports verified`. A missing symbol or alternate phase-3 model contract stops this plan; align and complete phase 3 before resuming rather than adding phase-4 compatibility duplicates.

- [ ] **Step 2: Run the backend phases 1–3 acceptance gate**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest \
  tests/test_moderation_phase1_contracts.py \
  tests/test_moderation_phase1_write_paths.py \
  tests/test_moderation_phase2_acceptance.py \
  tests/test_moderation_phase3_acceptance.py \
  tests/test_moderation_privacy.py \
  tests/test_moderation_retention.py \
  tests/test_moderation_worker.py \
  tests/test_tencent_moderation_provider.py \
  tests/test_media_publish_saga.py -q
```

Expected: all tests PASS. Any bypass, failed privacy assertion, Provider contract failure, upload ownership failure, stale result race, duplicate penalty, or dead worker recovery failure stops phase 4.

- [ ] **Step 3: Prove private messages and private media remain local-only**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest \
  tests/test_chat_moderation_side_effects.py \
  tests/test_governed_write_paths.py \
  tests/test_media_privacy.py -q
```

Expected: all tests PASS; rejected HTTP/WS messages create no `Message` or `WSMessageLog`; private/realtime chat text never reaches TMS; direct/community chat media may reach IMS/VM only during its first phase-3 upload moderation; no historical chat text or media is selected or sent by phase-4 backfill.

- [ ] **Step 4: Verify one migrated schema head and Flutter phase compatibility**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m alembic upgrade head
./.venv/Scripts/python.exe -m alembic heads
cd /d/FlutterProject/nonto
flutter test test/moderation_phase2_acceptance_test.dart test/moderation_phase3_acceptance_test.dart
```

Expected: Alembic upgrade succeeds, exactly one line ends with `(head)`, and both Flutter tests PASS.

- [ ] **Step 5: Leave both repositories unchanged**

Run:

```bash
git -C /d/NanTuPy status --short
git -C /d/FlutterProject/nonto status --short
```

Expected: no new phase-4 changes. Existing unrelated changes may remain, but record them and never stage them in later commits. There is no gate commit.

---

### Task 2: Add operations models and generate a migration from the live Alembic head

**Files:**
- Create: `app/models/moderation_operations.py`
- Modify: `app/models/moderation.py`
- Modify: `app/models/__init__.py`
- Modify: `alembic/env.py`
- Create: `alembic/versions/2026_07_18_0600_add_moderation_operations.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_moderation_operations_models.py`
- Create: `tests/test_moderation_operations_migration.py`

- [ ] **Step 1: Write failing metadata and migration tests**

Add exact metadata assertions:

```python
EXPECTED_TABLES = {
    "moderation_backfill_runs",
    "moderation_backfill_cursors",
    "moderation_backfill_items",
    "moderation_backfill_owner_decisions",
    "moderation_operations_heartbeats",
    "moderation_rate_limit_buckets",
    "moderation_maintenance_runs",
    "moderation_maintenance_items",
    "moderation_legal_holds",
    "user_risk_adjustments",
}
assert EXPECTED_TABLES <= set(Base.metadata.tables)
assert _unique("moderation_backfill_runs", "idempotency_key")
assert _unique("moderation_backfill_cursors", "run_id", "target_type")
assert _unique("moderation_backfill_items", "assessment_idempotency_key")
assert _unique("moderation_backfill_owner_decisions", "assessment_run_id", "target_type", "target_id", "target_version", "owner_revision_hash")
assert _unique("moderation_backfill_owner_decisions", "enforcement_idempotency_key")
assert _unique("moderation_operations_heartbeats", "worker_id")
assert _unique("moderation_rate_limit_buckets", "bucket_name")
assert _unique("moderation_maintenance_runs", "job_type", "scheduled_for")
assert _unique("moderation_maintenance_items", "idempotency_key")
assert _unique("user_risk_adjustments", "idempotency_key")
assert _unique("user_risk_adjustments", "user_id", "anchor_violation_at", "period_number")
```

Assert `moderation_tasks` gains nullable `backfill_item_id` and non-null `priority` default `0`; do **not** add a case/backfill XOR or any new ownership check to the shared phase-2/3 `moderation_tasks` table, because phase-3 cleanup/maintenance tasks may legitimately have both nullable. Only phase-4 backfill creation service requires `backfill_item_id IS NOT NULL` for backfill assessment tasks, while ordinary moderation retains its existing validation. Assert no operations table has columns named `content`, `payload_json`, `media_url`, `object_url`, `provider_response`, `token`, `secret`, `email`, or `username`. The automatic migration test fixture creates its own disposable MySQL database with a generated whitelisted name, upgrades, inspects every table/index/FK/default, downgrades to the captured parent, and upgrades again; ordinary developer or production databases are upgrade-only.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
cd /d/NanTuPy
APP_ENV=test \
PHASE4_MIGRATION_DB_NAME="nantupy_phase4_migration_test_red_$(date -u +%Y%m%d%H%M%S)_$$" \
./.venv/Scripts/python.exe -m pytest \
  tests/test_moderation_operations_models.py \
  tests/test_moderation_operations_migration.py -q
```

Expected: the database-name guard accepts only the disposable test name, then tests FAIL because `app.models.moderation_operations` and the operations tables are absent.

- [ ] **Step 3: Implement bounded ORM models and dynamically create the revision**

Implement these columns exactly:

```python
class ModerationBackfillRun(Base):
    __tablename__ = "moderation_backfill_runs"
    # id, mode, status, assessment_run_id, target_types_json,
    # policy_version, rule_version, provider_policy_version,
    # batch_size, scan_qps, provider_qps, created_by,
    # idempotency_key, acknowledgement_hash, pause_reason_code,
    # last_error_code, last_error_message, created_at, started_at,
    # paused_at, completed_at, cancelled_at, updated_at

class ModerationBackfillCursor(Base):
    __tablename__ = "moderation_backfill_cursors"
    # id, run_id, target_type, status, last_scanned_id,
    # snapshot_upper_bound_id, scanned_count, assessed_count,
    # enforced_count, skipped_count, error_count, claim_token,
    # worker_id, claimed_at, lease_expires_at, version, updated_at

class ModerationBackfillItem(Base):
    __tablename__ = "moderation_backfill_items"
    # id, run_id, cursor_id, assessment_item_id, scan_type, scan_id,
    # target_type nullable only for MANUAL_INVENTORY, target_id nullable only
    # for MANUAL_INVENTORY, target_version nullable only for MANUAL_INVENTORY,
    # asset_kind, content_hash nullable only for MANUAL_INVENTORY,
    # owner_revision_hash nullable only for MANUAL_INVENTORY,
    # local_decision, provider_decision, risk_category, severity,
    # confidence_bucket, outcome, inventory_reason_code present only for
    # MANUAL_INVENTORY, assessment_idempotency_key, moderation_task_id,
    # assessed_at, created_at, updated_at

class ModerationBackfillOwnerDecision(Base):
    __tablename__ = "moderation_backfill_owner_decisions"
    # id, assessment_run_id, target_type, target_id, target_version,
    # owner_revision_hash, expected_asset_count, completed_asset_count,
    # decision(NormalizedTaskDecision.value), enforcement_idempotency_key,
    # moderation_case_id, enforced_at, created_at, updated_at

class ModerationOperationsHeartbeat(Base):
    __tablename__ = "moderation_operations_heartbeats"
    # worker_id, role(backfill/maintenance), started_at, heartbeat_at,
    # current_run_id, current_cursor_id, safe_state, version

class ModerationRateLimitBucket(Base):
    __tablename__ = "moderation_rate_limit_buckets"
    # bucket_name PK(scan/provider), window_started_at,
    # used_tokens, capacity, version, updated_at

class ModerationMaintenanceRun(Base):
    __tablename__ = "moderation_maintenance_runs"
    # id, job_type, scheduled_for, status, claim_token, worker_id,
    # claimed_at, lease_expires_at, scanned_count, changed_count,
    # protected_count, error_count, last_error_code,
    # last_error_message, started_at, completed_at, created_at, updated_at

class ModerationMaintenanceItem(Base):
    __tablename__ = "moderation_maintenance_items"
    # id, run_id, entity_type, entity_id, operation, status,
    # idempotency_key, claim_token, lease_expires_at,
    # attempt_count, last_error_code, created_at, completed_at, updated_at

class ModerationLegalHold(Base):
    __tablename__ = "moderation_legal_holds"
    # id, scope_type(case/target/user), case_id nullable SET NULL,
    # case_reference_hash, target_type, target_id, user_id,
    # reason_code, note, created_by,
    # created_at, released_by, released_at, release_reason

class UserRiskAdjustment(Base):
    __tablename__ = "user_risk_adjustments"
    # id, user_id, adjustment_type(decay), points(-1),
    # anchor_violation_at, period_number, idempotency_key,
    # maintenance_run_id, created_at
    # unique(user_id, anchor_violation_at, period_number)
```

Use `String(64)` for enum-like values, `String(128)` for request/error codes, `String(255)` for hashes/idempotency keys, and `String(500)` only for sanitized safe messages/notes. Add an operations-table-only check that `MANUAL_INVENTORY` items have a fixed reason code and null business-owner/hash fields, while every other item has non-null owner/version/hash fields; this does not constrain `moderation_tasks`. `target_types_json` is a canonical sorted JSON array of names, never content. Add indexes: cursors `(run_id,status,lease_expires_at,id)`, items `(run_id,scan_type,scan_id)`, items `(run_id,target_type,target_id)`, items `(outcome,created_at)`, owner decisions `(assessment_run_id,target_type,target_id,target_version)`, maintenance runs `(status,lease_expires_at,id)`, maintenance items `(run_id,status,lease_expires_at,id)`, legal holds `(scope_type,case_id,target_type,target_id,user_id,released_at)`, and the required unique constraint on risk adjustments `(user_id,anchor_violation_at,period_number)`.

Modify phase-2 `ModerationTask.case_id` to nullable only if it is not already nullable, add nullable `backfill_item_id` and `priority`; default existing tasks to priority `0`. Add only the `backfill_item_id -> moderation_backfill_items.id` FK/index and no shared-table XOR/check constraint. The phase-3 worker continues to support its existing cleanup tasks unchanged; it dispatches a task with `backfill_item_id IS NOT NULL` to the phase-4 assessment result sink and never calls governance case application for that task.

Immediately before revision creation run:

```bash
cd /d/NanTuPy
HEAD_OUTPUT="$(./.venv/Scripts/python.exe -m alembic heads)"
printf '%s\n' "$HEAD_OUTPUT"
test "$(printf '%s\n' "$HEAD_OUTPUT" | grep -c '(head)')" -eq 1
./.venv/Scripts/python.exe -m alembic revision --rev-id 2026_07_18_0600 -m "add moderation operations"
```

Expected: exactly one head. Rename Alembic's generated file to `alembic/versions/2026_07_18_0600_add_moderation_operations.py` and preserve its generated `down_revision` verbatim. If revision ID `2026_07_18_0600` already exists after phases 1–3, stop and resolve the unexpected revision collision before resuming; do not overwrite it, invent an alternate parent, or create a branch head.

The migration must alter foreign keys so completed cases may be removed at 180 days while audit/risk rows survive: nullable `case_id` references from `moderation_actions`, `user_violations`, `reports`, and closed appeals use `ON DELETE SET NULL`; add a non-reversible HMAC reference column `case_reference_hash` to audit rows and backfill it using `MODERATION_LOG_HASH_KEY`. Keep open appeals/reports protected in service logic. Ensure `UserModerationProfile.last_confirmed_violation_at` exists as the only decay anchor; if a pre-phase-4 schema still carries the obsolete `last_violation_at` name, rename it without creating a second field. Create append-only MySQL guards for `user_risk_adjustments`; replace phase-2 action guards with a controlled delete condition that permits only a transaction where `@moderation_retention_cleanup = 1`, while all ordinary updates/deletes still signal SQLSTATE `45000`.

- [ ] **Step 4: Run the fixture-owned disposable roundtrip, then ordinary upgrade/head checks**

`tests/test_moderation_operations_migration.py` owns database creation and deletion. Its fixture requires `APP_ENV=test`, accepts only a generated database name matching `^nantupy_phase4_migration_test_[a-z0-9_]+$`, refuses the configured development/production database name and non-test hosts, creates that database before constructing its Alembic URL, captures the dynamic parent, runs parent→head→parent→head, and drops the database in `finally`. The test must abort before any downgrade if any guard fails. Never point this fixture or a manual `alembic downgrade` at a normal developer, staging, or production schema.

Run:

```bash
cd /d/NanTuPy
APP_ENV=test \
PHASE4_MIGRATION_DB_NAME="nantupy_phase4_migration_test_$(date -u +%Y%m%d%H%M%S)_$$" \
./.venv/Scripts/python.exe -m pytest \
  tests/test_moderation_operations_models.py \
  tests/test_moderation_operations_migration.py -q
./.venv/Scripts/python.exe -m alembic upgrade head
./.venv/Scripts/python.exe -m alembic heads
```

Expected: tests PASS, the guarded fixture proves downgrade/upgrade only in its disposable MySQL database and removes it, the ordinary database is only upgraded, and exactly one head is printed.

- [ ] **Step 5: Commit the operations schema**

```bash
cd /d/NanTuPy
git add app/models/moderation_operations.py app/models/moderation.py app/models/__init__.py alembic/env.py alembic/versions/2026_07_18_0600_add_moderation_operations.py tests/conftest.py tests/test_moderation_operations_models.py tests/test_moderation_operations_migration.py
git commit -m "feat: add moderation operations schema"
```

---

### Task 3: Build explicit public-target backfill adapters

**Files:**
- Create: `app/services/moderation_backfill_types.py`
- Create: `app/services/moderation_backfill_targets.py`
- Create: `tests/test_moderation_backfill_targets.py`
- Create: `tests/test_moderation_backfill_privacy.py`

- [ ] **Step 1: Write failing adapter, cursor, and exclusion tests**

Parameterize every locked target type. Seed IDs below, at, and above an upper bound; soft-hidden/deleted/non-approved/private rows; changed revisions; modern public media; every purpose of phase-3 `legacy_imported` media with safely mapped, ambiguous, and unmappable owners; direct/community chat media; identity evidence; and sentinel private bodies. Assert:

```python
assert set(PUBLIC_BACKFILL_TARGETS) == {
    "post", "comment", "user_profile", "community",
    "community_announcement", "comic_event", "comic_comment",
    "post_media", "user_profile_media", "community_media",
    "comic_event_media", "legacy_public_media",
}
assert not any(adapter.model in {Message, WSMessageLog}
               for adapter in PUBLIC_BACKFILL_TARGETS.values())
assert not set(media_purposes_scanned) & {
    UploadPurpose.DIRECT_CHAT_IMAGE,
    UploadPurpose.DIRECT_CHAT_VIDEO,
    UploadPurpose.COMMUNITY_CHAT_IMAGE,
    UploadPurpose.COMMUNITY_CHAT_VIDEO,
    UploadPurpose.IDENTITY_EVIDENCE_IMAGE,
}
assert [row.target_id for row in adapter.fetch_batch(
    db, after_id=10, upper_bound_id=20, limit=50,
)] == sorted(ids_between_11_and_20)
```

Capture SQL with SQLAlchemy events and assert every batch predicate contains `id > :after_id`, `id <= :upper_bound_id`, and `ORDER BY id ASC`; reject `OFFSET`. Assert snapshots contain only approved public fields in memory, compute canonical SHA-256 from target type/version/whitelisted fields/media object IDs, and no snapshot or log includes JWT, signed URL query, private body, COS credential, email, or Provider payload.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_backfill_targets.py tests/test_moderation_backfill_privacy.py -q
```

Expected: FAIL on missing target types and adapter registry.

- [ ] **Step 3: Implement the explicit adapters and fail-closed registry**

Use one shared base only for primary-key iteration; each adapter declares real whitelisted fields:

```python
TEXT_FIELDS = {
    "post": ("content",),
    "comment": ("content",),
    "user_profile": ("username", "bio"),
    "community": ("name", "description", "rules"),
    "community_announcement": ("title", "content"),
    "comic_event": ("name", "venue", "ticket_info", "intro"),
    "comic_comment": ("content",),
}

MEDIA_PURPOSES = {
    "post_media": frozenset({"post_image", "post_video"}),
    "user_profile_media": frozenset({"user_avatar", "user_cover"}),
    "community_media": frozenset({"community_avatar", "community_banner"}),
    "comic_event_media": frozenset({"comic_event_image"}),
}
```

Public text predicates require current phase-2 approved status, business visibility, and not hidden/deleted. `user_profile` requires active public/search-visible profile and uses `profile_moderation_status`. Text adapters set `scan_type=target_type`, `scan_id=target_id`. Modern media adapters scan `UploadObject.id`, so they set `scan_type` to the registry key, `scan_id=UploadObject.id`, and resolve `target_type/target_id/author_id/version` from the published owner binding; each media object remains an independent assessment item while owner aggregation prevents duplicate disposition.

`LegacyPublicMediaBackfillAdapter` applies the five-value `FORBIDDEN_MEDIA_PURPOSES` predicate in SQL, so historical direct/community chat and identity evidence create no phase-4 snapshot/item and no Provider I/O. It scans every remaining `legacy_imported` `UploadObject.id` through the same bounded cursor: a deterministic, allow-listed mapping to a currently public business owner emits a normal media snapshot; an absent, conflicting, or unsafe owner mapping emits `MANUAL_INVENTORY` with only object ID and fixed reason code. It must not filter remaining legacy rows out merely for missing modern `published`/`public_key` metadata, must never infer an owner from a client URL/path, and must never treat ambiguity as approval. Never derive a model or field from a CLI string; `get_public_backfill_adapter(name)` indexes the fixed registry and raises `UnknownBackfillTarget` otherwise.

Canonical hashing:

```python
def hash_public_asset(*, scan_type, scan_id, target_type, target_id,
                      version, fields, media_ids):
    document = {
        "scan_type": scan_type,
        "scan_id": scan_id,
        "target_type": target_type,
        "target_id": target_id,
        "version": version,
        "fields": fields,
        "media_object_ids": sorted(media_ids),
    }
    raw = json.dumps(document, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def hash_owner_revision(*, target_type, target_id, version,
                        text_hashes, media_hashes):
    document = {
        "target_type": target_type,
        "target_id": target_id,
        "version": version,
        "text_hashes": sorted(text_hashes),
        "media_hashes": sorted(media_hashes),
    }
    raw = json.dumps(document, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
```

Every asset snapshot stores the same `owner_revision_hash` computed from the complete current owner revision, not merely the current media object. Return text only through `PublicBackfillSnapshot.text_parts`; callers must drop the object before transaction completion. Never persist `fields` or `text_parts` in operations tables.

- [ ] **Step 4: Run adapter and phase-3 privacy regressions**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest \
  tests/test_moderation_backfill_targets.py \
  tests/test_moderation_backfill_privacy.py \
  tests/test_media_privacy.py \
  tests/test_chat_moderation_side_effects.py -q
```

Expected: all tests PASS and sentinels from private message/media fixtures are absent from selected snapshots, SQL parameters, logs, and operation rows.

- [ ] **Step 5: Commit public backfill adapters**

```bash
cd /d/NanTuPy
git add app/services/moderation_backfill_types.py app/services/moderation_backfill_targets.py tests/test_moderation_backfill_targets.py tests/test_moderation_backfill_privacy.py
git commit -m "feat: add public moderation backfill adapters"
```

---

### Task 4: Implement assessment and enforcement as separate idempotent lifecycles

**Files:**
- Create: `app/services/moderation_backfill_repository.py`
- Create: `app/services/moderation_backfill_service.py`
- Modify: `app/services/moderation_repository.py`
- Modify: `app/workers/moderation_worker.py`
- Create: `tests/test_moderation_backfill_modes.py`
- Create: `tests/test_moderation_backfill_idempotency.py`

- [ ] **Step 1: Write failing dry-run and enforcement tests**

Use spies that raise immediately if assessment invokes `create_case`, `apply_case_result`, `hide`, `record_violation`, media publish/delete, notification, fanout, or push. Cover local safe, local explicit reject, cloud approve, cloud high-confidence reject, cloud ambiguous, Provider retry, and duplicate callback. Assert assessment creates only item/task/aggregate rows and uses `backfill:assessment:` keys.

For enforcement, create a completed assessment containing one text item and several independent media items for the same owner revision, then assert creation fails without the exact acknowledgement hash or with an incomplete owner group. Materialize exactly one owner decision using this precedence: any high-confidence reject makes the owner `rejected`; otherwise any ambiguous/manual result makes it `manual_review`; only a complete group in which every item approved makes it `approved`. An unchanged rejected owner creates one `source="backfill"` case, one hide action, and at most one violation regardless of media count; manual creates one `manual_review` case without hide/points; approved creates no case; changed/deleted/private owners become skipped. Repeating or creating a second enforcement run must not add another case/hide/violation because the exact owner-level key is `backfill:enforcement:{target_type}:{target_id}:{target_version}:{owner_revision_hash}`.

- [ ] **Step 2: Run mode tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_backfill_modes.py tests/test_moderation_backfill_idempotency.py -q
```

Expected: FAIL because run lifecycle and separate result sinks are missing.

- [ ] **Step 3: Implement run creation, result sinks, and hard dry-run separation**

Implement exact entry points:

```python
class ModerationBackfillService:
    def create_assessment_run(
        self, db, *, target_types: tuple[str, ...], batch_size: int,
        scan_qps: int, provider_qps: int, created_by: int,
        idempotency_key: str,
    ) -> ModerationBackfillRun: ...

    def create_enforcement_run(
        self, db, *, assessment_run_id: int,
        acknowledgement_hash: str, created_by: int,
        idempotency_key: str,
    ) -> ModerationBackfillRun: ...

    def assess_snapshot(
        self, db, *, run: ModerationBackfillRun,
        cursor: ModerationBackfillCursor,
        snapshot: PublicBackfillSnapshot,
    ) -> ModerationBackfillItem: ...

    def enforce_owner_decision(
        self, db, *, run: ModerationBackfillRun,
        owner: ModerationBackfillOwnerDecision,
        current: PublicBackfillSnapshot | None,
    ) -> ModerationBackfillOwnerDecision: ...
```

`create_assessment_run()` validates target names, bounds `batch_size` to `1..500`, `scan_qps` to `1..1000`, provider QPS to `1..100`, captures policy/rule/provider versions, computes every upper bound in the same repeatable-read transaction, and inserts one cursor per scan adapter. `rule_version` remains the phase-2 integer contract. After all per-asset items are terminal, it groups by `(target_type,target_id,target_version,owner_revision_hash)`, verifies `completed_asset_count == expected_asset_count`, and materializes one `ModerationBackfillOwnerDecision` with reject > manual > approved precedence. `create_enforcement_run()` requires assessment status `completed` and every non-inventory owner group complete, computes `acknowledgement_hash = SHA256("{assessment_id}:{completed_at}:{owner_count}:{rejected_owner_count}:{manual_owner_count}")`, and creates enforcement cursors per business `target_type` over immutable owner-decision ID upper bounds; it does not copy media scan cursors or enforce per asset.

Assessment local-safe results complete immediately. A `LegacyMediaInventoryCandidate` completes immediately as `MANUAL_INVENTORY` with only scan/object ID and a fixed reason code; it creates no Provider task and participates in inventory counts but not owner enforcement aggregation. Suspicious text/media creates a phase-3 `ModerationTask` with `case_id=None`, `backfill_item_id=item.id`, `priority=100`, and assessment idempotency key. Extend the existing worker processor only at the result sink boundary:

```python
if task.backfill_item_id is not None:
    backfill_repository.complete_assessment_task(
        task_id=task.id,
        backfill_item_id=task.backfill_item_id,
        claim_token=task.claim_token,
        normalized_result=result,
        now=clock.now(),
    )
else:
    moderation_repository.complete_task(...)
```

The assessment branch consumes and persists only fields from phase-2 `NormalizedTaskResult` (`NormalizedTaskDecision`, `RiskCategory`, `Severity`, confidence, policy version, integer `rule_version`, Provider IDs where privacy permits); it defines no parallel decision/category/severity/result enum and never calls target governance. Per-media completion only updates its item and owner aggregate under CAS/uniqueness.

Enforcement reads `ModerationBackfillOwnerDecision`, reloads the complete current owner revision, and requires exact target type, ID, target version, `owner_revision_hash`, visibility, assessment policy/rule/provider versions, and completed assessment group. A rejected owner calls the existing governance hide/apply path with trusted trigger `backfill`, then `record_violation()` using the same owner-level enforcement key; unique key handling makes all media converge on one case/hide/score. A manual owner creates/links one `manual_review` case but does not hide or score. An approved owner creates no governance side effect. All case/action/task completion uses existing phase-2 state machine and claim checks.

- [ ] **Step 4: Run mode, worker, governance, and privacy tests**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest \
  tests/test_moderation_backfill_modes.py \
  tests/test_moderation_backfill_idempotency.py \
  tests/test_moderation_worker.py \
  tests/test_violation_ledger.py \
  tests/test_moderation_privacy.py -q
```

Expected: all tests PASS; assessment side-effect spies remain untouched and repeated enforcement leaves one case/action/violation.

- [ ] **Step 5: Commit separate assessment and enforcement flows**

```bash
cd /d/NanTuPy
git add app/services/moderation_backfill_repository.py app/services/moderation_backfill_service.py app/services/moderation_repository.py app/workers/moderation_worker.py tests/test_moderation_backfill_modes.py tests/test_moderation_backfill_idempotency.py
git commit -m "feat: add dry-run and enforcement backfill modes"
```

---

### Task 5: Add CAS cursor claims, snapshot bounds, leases, and crash recovery

**Files:**
- Modify: `app/services/moderation_backfill_repository.py`
- Create: `app/workers/moderation_backfill_worker.py`
- Create: `tests/test_moderation_backfill_claims.py`
- Create: `tests/test_moderation_backfill_recovery.py`

- [ ] **Step 1: Write failing two-session race and crash-point tests**

Against the shared MySQL test schema, use two SQLAlchemy sessions to race on one cursor. Assert only one committed claim token survives. Test lease renewal requires current token and unexpired lease; stale token cannot advance, complete, or release. Test a run paused/cancelled after claim stops before the next snapshot.

Inject crashes at four exact points: after claim commit, after item commit, after Provider task creation, and before cursor advance. Restart with a later clock and assert expired lease reclaim, assessment item uniqueness, Provider task uniqueness, no skipped target ID, no duplicate enforcement, and eventual cursor completion. Insert IDs above the captured upper bound during the run and prove they remain for a later run.

- [ ] **Step 2: Run claim/recovery tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_backfill_claims.py tests/test_moderation_backfill_recovery.py -q
```

Expected: FAIL because cursor claims and the backfill worker do not exist.

- [ ] **Step 3: Implement conditional claims and item-before-cursor durability**

Implement repository signatures:

```python
def claim_cursor(self, *, worker_id: str, now: datetime,
                 lease_seconds: int) -> ClaimedCursor | None: ...
def renew_cursor(self, *, cursor_id: int, claim_token: str,
                 now: datetime, lease_seconds: int) -> bool: ...
def advance_cursor(self, *, cursor_id: int, claim_token: str,
                   expected_last_id: int, new_last_id: int,
                   counters: dict[str, int], now: datetime) -> bool: ...
def release_cursor(self, *, cursor_id: int, claim_token: str,
                   completed: bool, now: datetime) -> bool: ...
```

`claim_cursor()` selects running runs and pending/expired-processing cursors ordered by run creation, target type, cursor ID, then performs one `UPDATE ... WHERE id=:id AND version=:version AND (claim_token IS NULL OR lease_expires_at<:now)` that increments version. Commit before loading target data.

Worker batch order is fixed and mode-specific:

```python
claim = repository.claim_cursor(...)
db.commit()
run = repository.get_run(claim.run_id)
if run.mode == BackfillMode.ASSESSMENT:
    rows = adapter.fetch_batch(
        db, after_id=claim.last_scanned_id,
        upper_bound_id=claim.snapshot_upper_bound_id,
        limit=batch_size,
    )
    for snapshot in rows:
        service.assess_snapshot(run=run, cursor=cursor, snapshot=snapshot)
        repository.renew_cursor(...)
    new_last_id = rows[-1].scan_id if rows else claim.snapshot_upper_bound_id
else:
    rows = repository.fetch_owner_decisions(
        assessment_run_id=run.assessment_run_id,
        target_type=claim.target_type,
        after_owner_id=claim.last_scanned_id,
        upper_bound_owner_id=claim.snapshot_upper_bound_id,
        decisions={NormalizedTaskDecision.REJECTED,
                   NormalizedTaskDecision.MANUAL_REVIEW,
                   NormalizedTaskDecision.APPROVED},
        limit=batch_size,
    )
    for owner in rows:
        current = owner_adapter.load_current(db, target_id=owner.target_id)
        service.enforce_owner_decision(run=run, owner=owner, current=current)
        repository.renew_cursor(...)
    new_last_id = rows[-1].id if rows else claim.snapshot_upper_bound_id
repository.advance_cursor(
    expected_last_id=claim.last_scanned_id,
    new_last_id=new_last_id,
    counters=batch_counters,
)
db.commit()
```

Assessment media adapters load by stored `scan_id` (`UploadObject.id`) and resolve the owning business target, but enforcement never iterates media items. Enforcement cursors are created from immutable upper bounds of `ModerationBackfillOwnerDecision.id`, load complete owner decisions, and include approved owners so their terminal no-op is explicit and measurable; `MANUAL_INVENTORY` legacy rows remain in assessment inventory and are never enforced. If the mode-specific batch is empty, set `last_scanned_id=snapshot_upper_bound_id`, mark cursor completed, and complete the run only when every cursor is completed, no assessment Provider task remains pending/processing/failed, and every enforceable owner group is complete. On SIGTERM stop claiming, finish only the current item transaction, release/leave the cursor for lease recovery, heartbeat `stopping`, and exit within configured grace seconds.

- [ ] **Step 4: Run race/recovery and repeated-backfill tests**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest \
  tests/test_moderation_backfill_claims.py \
  tests/test_moderation_backfill_recovery.py \
  tests/test_moderation_backfill_idempotency.py -q
```

Expected: all tests PASS under two sessions and every injected crash converges without gaps or duplicates.

- [ ] **Step 5: Commit the recoverable worker**

```bash
cd /d/NanTuPy
git add app/services/moderation_backfill_repository.py app/workers/moderation_backfill_worker.py tests/test_moderation_backfill_claims.py tests/test_moderation_backfill_recovery.py
git commit -m "feat: add recoverable moderation backfill worker"
```

---

### Task 6: Enforce pause/resume, realtime priority, and two-layer rate limits

**Files:**
- Create: `app/services/moderation_rate_limiter.py`
- Modify: `app/services/moderation_backfill_repository.py`
- Modify: `app/workers/moderation_backfill_worker.py`
- Modify: `app/services/moderation_repository.py`
- Modify: `app/core/config.py`
- Create: `tests/test_moderation_backfill_control.py`
- Create: `tests/test_moderation_rate_limiter.py`
- Create: `tests/test_moderation_realtime_priority.py`

- [ ] **Step 1: Write failing clock-driven control and priority tests**

With a fake monotonic/wall clock, assert pause changes `running -> paused`, allows the current item transaction to finish, then performs no additional target/provider calls; resume changes `paused -> running` without changing `last_scanned_id` or upper bound; cancel is terminal. Concurrent pause/resume uses run version CAS and returns conflict on stale version.

Assert layer 1 local token buckets smooth each process at configured scan and Provider QPS. Assert layer 2 database buckets cap aggregate QPS across two workers even when both local buckets have tokens. Freeze time at a one-second boundary and assert CAS window reset without over-allocation.

Seed any due phase-1–3 `ModerationTask(priority < 100)` and assert backfill does not start another Provider call until realtime queue depth is zero and oldest due age is below `BACKFILL_REALTIME_MAX_AGE_SECONDS`. Existing worker claim order must be `(priority,next_attempt_at,id)` so realtime tasks win; starvation protection only permits local-safe scanning and never consumes Provider capacity while realtime backlog exists.

- [ ] **Step 2: Run control/rate/priority tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest \
  tests/test_moderation_backfill_control.py \
  tests/test_moderation_rate_limiter.py \
  tests/test_moderation_realtime_priority.py -q
```

Expected: FAIL because shared budgets and realtime priority checks are absent.

- [ ] **Step 3: Implement deterministic control and both rate-limit layers**

Implement:

```python
class TwoLayerRateLimiter:
    def acquire(self, db, *, bucket: str, local_rate: int,
                shared_capacity: int, now: datetime) -> float:
        """Return zero when acquired; otherwise return bounded sleep seconds."""
```

The in-process token bucket uses `time.monotonic()`, capacity equal to one second of configured rate, and never carries more than one second of burst. The shared bucket uses a UTC second window and conditional version update; one token is committed before the external call. Failed Provider calls still consume a token. Allowed bucket names are only `backfill_scan` and `backfill_provider`; no target ID, user ID, case ID, status text, or error code becomes a bucket/metric label.

Before each snapshot, check run status and acquire scan budget. Before creating/dispatching each backfill Provider task, query due non-backfill queue depth/oldest age and acquire Provider budget. Configuration keys in `app/core/config.py` are read once with bounded parsing:

```python
BACKFILL_DEFAULT_SCAN_QPS = 50
BACKFILL_DEFAULT_PROVIDER_QPS = 2
BACKFILL_REALTIME_MAX_AGE_SECONDS = 5
BACKFILL_CURSOR_LEASE_SECONDS = 60
BACKFILL_IDLE_SLEEP_SECONDS = 2
```

Add these exact environment names to `.env.example` in Task 11 together with deployment values. `pause_run()` stores a bounded reason code, clears no cursor, and leaves active claims to finish/expire. `resume_run()` keeps all immutable snapshot/version fields. Reorder the existing moderation task claim query by `priority ASC,next_attempt_at ASC,id ASC`; phase 1–3 task creation defaults to `0`, backfill assessment always uses `100`.

- [ ] **Step 4: Run control, worker, and realtime regression tests**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest \
  tests/test_moderation_backfill_control.py \
  tests/test_moderation_rate_limiter.py \
  tests/test_moderation_realtime_priority.py \
  tests/test_moderation_worker.py \
  tests/test_moderation_backfill_recovery.py -q
```

Expected: all tests PASS; aggregate fake-clock calls never exceed configured shared capacity and realtime tasks finish first.

- [ ] **Step 5: Commit operational controls**

```bash
cd /d/NanTuPy
git add app/services/moderation_rate_limiter.py app/services/moderation_backfill_repository.py app/workers/moderation_backfill_worker.py app/services/moderation_repository.py app/core/config.py tests/test_moderation_backfill_control.py tests/test_moderation_rate_limiter.py tests/test_moderation_realtime_priority.py
git commit -m "feat: control and throttle moderation backfill"
```

---

### Task 7: Add a safe operations CLI

**Files:**
- Create: `app/cli/__init__.py`
- Create: `app/cli/moderation_operations.py`
- Create: `tests/test_moderation_operations_cli.py`

- [ ] **Step 1: Write failing CLI parsing, authorization, and output tests**

Invoke the module through `subprocess.run()` and test exact commands:

```text
create-assessment --targets post,comment --batch-size 100 --scan-qps 50 --provider-qps 2 --actor-id 7 --request-key rollout-20260718-a
create-enforcement --assessment-run-id 12 --acknowledgement-hash <64 hex> --actor-id 7 --request-key enforce-20260718-a
status --run-id 12 --json
pause --run-id 12 --reason-code operator_review --expected-version 4
resume --run-id 12 --expected-version 5
cancel --run-id 12 --reason-code rollout_cancelled --expected-version 5
run-backfill-once --worker-id manual-backfill
worker-health --role backfill --max-age-seconds 90
```

Assert unknown/private target names, out-of-range rates, malformed acknowledgement hashes, missing actor/reason, and stale versions exit `2` with a safe message. JSON output may include run/cursor IDs and aggregate counts but never target bodies, media URLs, credentials, DSN, Provider response, JWT, usernames, or emails. `create-enforcement` prints the expected acknowledgement hash on mismatch but performs no insert.

- [ ] **Step 2: Run CLI tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_operations_cli.py -q
```

Expected: FAIL because `app.cli.moderation_operations` is absent.

- [ ] **Step 3: Implement argparse commands with explicit transaction ownership**

Use one top-level parser and one function per subcommand. Convert only fixed enums/integers; target parsing must call `get_public_backfill_adapter()`. Every mutation opens `with SessionLocal.begin() as db:` and delegates to service/repository. Status uses this output shape:

```json
{
  "run_id": 12,
  "mode": "assessment",
  "status": "running",
  "version": 4,
  "targets": [
    {"target_type": "post", "last_scanned_id": 400, "upper_bound_id": 1000,
     "scanned": 400, "assessed": 400, "errors": 0}
  ],
  "outcomes": {"local_approved": 350, "high_confidence_rejected": 2,
               "manual_review": 3, "provider_approved": 45}
}
```

Configure logging with safe event names and IDs only. Catch known validation/conflict/not-found exceptions and return exit `2`; unexpected errors log only exception class plus generated request ID and return exit `1`. Never print `repr(exception)`, environment values, SQL parameters, or connection URLs.

- [ ] **Step 4: Run CLI tests and one-shot smoke commands**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_operations_cli.py -q
./.venv/Scripts/python.exe -m app.cli.moderation_operations --help
./.venv/Scripts/python.exe -m app.cli.moderation_operations worker-health --role backfill --max-age-seconds 90
```

Expected: tests PASS, help lists only the locked commands, and worker-health returns `0` for a fresh heartbeat or `1` with `stale_or_missing` without a stack trace.

- [ ] **Step 5: Commit the CLI**

```bash
cd /d/NanTuPy
git add app/cli/__init__.py app/cli/moderation_operations.py tests/test_moderation_operations_cli.py
git commit -m "feat: add moderation operations CLI"
```

---

### Task 8: Add liveness, readiness, moderation health, metrics, and redacted logs

**Files:**
- Modify: `app/core/config.py`
- Modify: `app/routers/health.py`
- Modify: `app/main.py`
- Create: `app/services/moderation_metrics.py`
- Modify: `app/workers/moderation_backfill_worker.py`
- Create: `tests/test_moderation_operations_health.py`
- Create: `tests/test_moderation_operations_metrics.py`
- Create: `tests/test_moderation_operations_logging.py`

- [ ] **Step 1: Write failing health, cardinality, and sentinel tests**

Assert endpoint semantics:

```text
GET /api/health/live        -> 200 without touching DB or Provider
GET /api/health/ready       -> 200 only when DB responds and DB revision equals script head; otherwise 503
GET /api/health/moderation  -> 200 healthy, 503 degraded for stale realtime worker/provider config/queue thresholds
GET /api/health/moderation/metrics -> Prometheus text with valid operations token
```

Moderation health includes only bounded statuses/counts/ages: realtime/backfill/maintenance heartbeat state, pending/dead task counts, oldest due age bucket, active/paused run counts, cursor remaining count, maintenance last-success age, Provider configured boolean. It contains no worker/run/cursor IDs, hostnames, exception text, credentials, URLs, target IDs, body, email, username, or request ID.

Parse every metric sample and assert label names are a subset of `role,mode,status,target_type,outcome,job_type,provider,decision`; label values belong to fixed enums/registry and never include database IDs. Inject body/JWT/cloud secret/signed URL/DSN sentinels into errors, then scan `caplog`, JSON, and metrics for absence.

- [ ] **Step 2: Run health/metrics/log tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest \
  tests/test_moderation_operations_health.py \
  tests/test_moderation_operations_metrics.py \
  tests/test_moderation_operations_logging.py -q
```

Expected: FAIL because split health endpoints and operations metrics are absent.

- [ ] **Step 3: Implement safe probes, fixed metrics, and structured events**

Register `health.router` once with prefix `/api/health`; remove the duplicate root `/health` implementation after updating existing tests. `/live` returns process state only. `/ready` runs `SELECT 1`, reads DB revision via `MigrationContext`, reads script head via `ScriptDirectory`, compares exact values, and emits only `database=ready|unavailable` and `migration=current|mismatch`.

`/moderation` uses thresholds from bounded config:

```python
MODERATION_REALTIME_WORKER_MAX_AGE_SECONDS = 90
MODERATION_OPERATIONS_WORKER_MAX_AGE_SECONDS = 180
MODERATION_QUEUE_MAX_OLDEST_SECONDS = 300
MODERATION_DEAD_TASK_MAX = 0
MODERATION_MAINTENANCE_MAX_AGE_HOURS = 26
```

Provider `configured=false`, stale realtime worker, too-old realtime task, or dead task above threshold degrades moderation health. A paused backfill does not degrade health when `pause_reason_code` is present; an expired processing cursor does. Backfill backlog never makes API readiness fail.

Metrics names are fixed:

```text
moderation_tasks{status,provider}
moderation_task_oldest_seconds{status}
moderation_worker_heartbeat_age_seconds{role}
moderation_backfill_runs{mode,status}
moderation_backfill_cursor_remaining{mode,target_type}
moderation_backfill_items_total{mode,target_type,outcome}
moderation_backfill_rate_per_minute{mode,target_type}
moderation_maintenance_last_success_age_seconds{job_type}
moderation_maintenance_items_total{job_type,status}
moderation_risk_decay_points_total
```

Protect metrics with constant-time comparison against `MODERATION_METRICS_TOKEN` from the `Authorization: Bearer` header; production startup fails if missing, tests inject a deterministic fake token. Never log the header/token. Implement `sanitize_operational_error()` by stripping control characters, credentials, query strings, signed URL parameters and content-like key/value fragments, then cap at 500 characters. Log templates use event, run/cursor/item/case/task IDs, target type, outcome, stable error code, and duration only.

- [ ] **Step 4: Run health, metrics, log, and existing health regressions**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest \
  tests/test_moderation_operations_health.py \
  tests/test_moderation_operations_metrics.py \
  tests/test_moderation_operations_logging.py \
  tests/test_moderation_health.py \
  tests/test_moderation_privacy.py -q
```

Expected: all tests PASS; sentinels are absent and metric label cardinality is bounded by enum counts.

- [ ] **Step 5: Commit observability**

```bash
cd /d/NanTuPy
git add app/core/config.py app/routers/health.py app/main.py app/services/moderation_metrics.py app/workers/moderation_backfill_worker.py tests/test_moderation_operations_health.py tests/test_moderation_operations_metrics.py tests/test_moderation_operations_logging.py
git commit -m "feat: observe moderation operations safely"
```

---

### Task 9: Implement protected 30/180/365-day retention cleanup and legal holds

**Files:**
- Create: `app/services/moderation_retention.py`
- Create: `app/workers/moderation_maintenance_worker.py`
- Modify: `app/services/media_publish_saga.py`
- Modify: `app/services/moderation_repository.py`
- Modify: `app/cli/moderation_operations.py`
- Modify: `tests/test_moderation_operations_cli.py`
- Create: `tests/test_moderation_retention_operations.py`
- Create: `tests/test_moderation_legal_hold.py`
- Create: `tests/test_moderation_maintenance_recovery.py`

- [ ] **Step 1: Write failing boundary, protection, crash, and CLI tests**

Extend `tests/test_moderation_operations_cli.py` with exact commands `run-maintenance-once --job retention_30d --scheduled-for 2026-07-18`, `legal-hold-create --scope case --case-id 91 --reason-code litigation --note "court order" --actor-id 1`, `legal-hold-release --hold-id 3 --reason-code released --actor-id 1`, and `worker-health --role maintenance --max-age-seconds 180`; malformed dates, scope-column mismatches, missing actor/reason/note, and stale/missing holds exit `2` without traceback.

Freeze time and seed records one second before, exactly at, and one second after the following locked cutoffs. Phase 4 is the only phase that schedules or executes retention; phase-2/3 helpers and cleanup tasks remain compatibility dependencies but do not own these policy windows. Assert:

- Rejected `ContentRevision` starts its 30-day clock at `ContentRevision.rejected_at`; at the cutoff its payload is cleared and a rejected never-published author-only shell may be tombstoned through the business adapter.
- Rejected quarantine media starts its independent 30-day clock at the owning `ModerationCase.completed_at`; at the cutoff it is deleted through the phase-3 media service. Do not use upload creation, rejection callback, object update, or maintenance-run timestamps as the anchor.
- Terminal `ModerationCase` metadata starts its 180-day clock at `ModerationCase.completed_at`; at the cutoff eligible case/task metadata is removed while audit/violation rows retain `case_reference_hash` and nullable `case_id`.
- Administrative `ModerationAction` retention starts its 365-day clock at `ModerationAction.created_at`; only rows at that cutoff are deleted. Current risk profiles, violations, compensations, and `user_risk_adjustments` remain.
- Pending/manual-review content, pending/processing tasks, non-terminal cases, open appeals, open reports, and any case/target/user covered by an active legal hold are not cleaned at any window. Closed appeals/reports cease to protect only after resolution. Approved revisions/media follow the owning business lifecycle rather than moderation retention.
- Cleanup is idempotent. Crash before COS delete, after COS delete, and before DB completion recovers through phase-4 maintenance item leases without double business mutation. COS not-found is success; permission/network errors retry with sanitized codes.

- [ ] **Step 2: Run retention/legal-hold/recovery tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest \
  tests/test_moderation_retention_operations.py \
  tests/test_moderation_legal_hold.py \
  tests/test_moderation_maintenance_recovery.py \
  tests/test_moderation_operations_cli.py -q
```

Expected: FAIL because maintenance orchestration and protection predicates are absent.

- [ ] **Step 3: Implement exact protection and cleanup phases**

Implement one reusable predicate:

```python
def retention_protection(db, *, case_id: int | None,
                         target_type: str | None, target_id: int | None,
                         user_id: int | None) -> str | None:
    # return legal_hold, open_appeal, or open_report; otherwise None
```

An active hold has `released_at IS NULL`; scope columns must match its scope and unused scope columns must be null. Add the four Step-1 maintenance/hold/health commands to the existing argparse tree, delegate to maintenance/hold repositories under CLI-owned transactions, and keep the Task-7 exit/output privacy contract. Hold create/release appends moderation actions and requires super-admin CLI actor, reason code, and bounded note. Never expose the note in metrics/health.

Maintenance scheduling inserts one run per `(job_type,scheduled_for UTC date)`. All retention and risk-decay work is claimed, retried, and completed only through phase-4 `ModerationMaintenanceRun`/`ModerationMaintenanceItem`; never encode phase-4 maintenance ownership in `ModerationTask`, never enqueue a phase-2/3 `cleanup` task for these jobs, and never add a `ModerationTask.case_id/backfill_item_id` XOR. Claim run/item with the same CAS token/lease pattern as backfill. Build bounded pages by primary key. For external object deletion, first insert/claim a `ModerationMaintenanceItem(idempotency_key="retention:30d:media:{object_id}:{case_completed_at}")`, commit, call phase-3 `delete_quarantine_object(object_id)` outside a transaction, then conditionally complete; object-not-found completes successfully.

The candidate queries use exactly these anchors: rejected revisions `rejected_at <= now-30d`; rejected quarantine `case.completed_at <= now-30d`; terminal cases `completed_at <= now-180d`; administrative actions `created_at <= now-365d`. At 30 days clear only eligible rejected `ContentRevision.payload_json`, retain hash and minimal case until 180 days, and tombstone a never-published business row only through its phase-2 adapter; independently delete eligible rejected quarantine through the phase-3 media service. At 180 days delete only terminal cases/tasks after protection check; FK `SET NULL` preserves violations/audits. At 365 days set `@moderation_retention_cleanup = 1` inside the dedicated transaction, delete only expired administrative actions, then reset it to `0` in `finally`; no route/repository API receives this capability. Completed phase-4 maintenance/backfill detail may have a separately tested operations-data lifecycle, but it must not substitute a different anchor for the four locked moderation windows. Never delete pending/processing/manual-review records, approved business content or media, active upload/media, open governance work, active holds, open reports/appeals, violations, compensations, profiles, or risk adjustments.

- [ ] **Step 4: Run retention, phase-3 media, and privacy tests**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest \
  tests/test_moderation_retention_operations.py \
  tests/test_moderation_legal_hold.py \
  tests/test_moderation_maintenance_recovery.py \
  tests/test_media_publish_saga.py \
  tests/test_moderation_retention.py \
  tests/test_moderation_privacy.py -q
```

Expected: all tests PASS; protected rows survive, exact-boundary rows are cleaned, and no raw object URL/body enters operations persistence or logs.

- [ ] **Step 5: Commit retention operations**

```bash
cd /d/NanTuPy
git add app/services/moderation_retention.py app/workers/moderation_maintenance_worker.py app/services/media_publish_saga.py app/services/moderation_repository.py app/cli/moderation_operations.py tests/test_moderation_operations_cli.py tests/test_moderation_retention_operations.py tests/test_moderation_legal_hold.py tests/test_moderation_maintenance_recovery.py
git commit -m "feat: enforce protected moderation retention"
```

---

### Task 10: Add exact 30-day risk decay with an append-only ledger

**Files:**
- Create: `app/services/moderation_risk_decay.py`
- Modify: `app/services/publish_restrictions.py`
- Modify: `app/workers/moderation_maintenance_worker.py`
- Create: `tests/test_moderation_risk_decay_operations.py`

- [ ] **Step 1: Write failing calendar, floor, reset, and idempotency tests**

Phase 4 is the only phase that schedules or applies risk decay. With UTC timestamps, assert score 5 remains 5 at `last_confirmed_violation_at + 30 days - 1 second`, becomes 4 exactly at 30 days, and becomes 2 at 90 days. Each full 30-day period appends one ledger row with `points=-1` and period numbers `1,2,3`; uniqueness is exactly `(user_id,anchor_violation_at,period_number)`. Score never falls below zero; score 2 after 180 days emits only two adjustments. Re-running the same or a later maintenance run emits none for an existing period.

Insert a new confirmed violation after one decay and assert it updates `last_confirmed_violation_at`, establishing a fresh `anchor_violation_at`; no decay occurs until 30 complete days after it. Prior anchor rows remain append-only and are never reused for the new anchor. Pending/manual/provider-error/compensation/appeal events do not reset the anchor. Two maintenance workers racing on one user create one row per unique tuple and the profile version increments once per applied point. Decay may relax natural score-derived temporary restrictions but must never reactivate or unban a permanently deactivated account.

- [ ] **Step 2: Run decay tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_risk_decay_operations.py -q
```

Expected: FAIL because exact-period decay ledger behavior is absent.

- [ ] **Step 3: Implement floor-to-complete-period catch-up and ledger-first updates**

Implement:

```python
def apply_due_risk_decay(db, *, user_id: int,
                         as_of: datetime,
                         maintenance_run_id: int) -> int:
    profile = lock_profile(db, user_id)
    anchor = profile.last_confirmed_violation_at
    if profile.risk_score <= 0 or anchor is None:
        return 0
    complete_periods = int((as_of - anchor).total_seconds() // (30 * 86400))
    existing_periods = decay_period_numbers(db, user_id, anchor)
    due_periods = [period for period in range(1, complete_periods + 1)
                   if period not in existing_periods]
    # insert due periods in order, stopping when risk_score reaches zero
    return apply_period_rows_up_to_score_floor(db, profile, anchor, due_periods)
```

Each row key is `risk-decay:{user_id}:{anchor_violation_at_utc}:{period_number}`, backed by the database unique constraint `(user_id,anchor_violation_at,period_number)` rather than idempotency-key convention alone. Insert a row before decrementing `UserModerationProfile.risk_score` in the same transaction; uniqueness races roll back to a savepoint and reload. A newly confirmed violation updates `last_confirmed_violation_at`; the next decay run automatically uses that new timestamp as its anchor while retaining every prior adjustment row. Append `ModerationAction(action="risk_decay")` with its own same-period derived key and no user content. Recompute restriction deadlines through existing phase-2 policy without shortening an explicit administrator restriction; natural score-based restrictions may expire normally. Explicitly bypass account activation fields: a permanently deactivated user remains deactivated regardless of decayed risk. Do not edit/delete violation, compensation, prior adjustment, or prior action rows.

Maintenance `risk_decay` run pages profiles by `user_id`, applies catch-up, updates aggregate changed count, and uses maintenance item key `risk-decay-user:{user_id}:{as_of_date}` for crash recovery.

- [ ] **Step 4: Run decay, ledger, and restriction regressions**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest \
  tests/test_moderation_risk_decay_operations.py \
  tests/test_violation_ledger.py \
  tests/test_publish_restrictions.py \
  tests/test_moderation_maintenance_recovery.py -q
```

Expected: all tests PASS and effective risk never becomes negative or double-decrements under races.

- [ ] **Step 5: Commit risk decay maintenance**

```bash
cd /d/NanTuPy
git add app/services/moderation_risk_decay.py app/services/publish_restrictions.py app/workers/moderation_maintenance_worker.py tests/test_moderation_risk_decay_operations.py
git commit -m "feat: decay moderation risk with ledger entries"
```

---

### Task 11: Split Docker migration, API, realtime worker, backfill, and maintenance roles

**Files:**
- Modify: `.env.example`
- Modify: `Dockerfile`
- Modify: `deploy/entrypoint.sh`
- Create: `deploy/migration-entrypoint.sh`
- Modify: `deploy/moderation-worker-entrypoint.sh`
- Create: `deploy/moderation-backfill-entrypoint.sh`
- Create: `deploy/moderation-maintenance-entrypoint.sh`
- Modify: `docker-compose.yml`
- Create: `tests/test_moderation_docker_roles.py`

- [ ] **Step 1: Write failing static and command contract tests**

Parse Compose YAML and shell files. Assert exactly these services/roles exist: `migration`, `nantupy-api`, `moderation-worker`, `moderation-backfill`, `moderation-maintenance`. All use the same image/env/database. Only migration runs `alembic upgrade head`; API and all workers contain no Alembic command. API/workers depend on migration with `condition: service_completed_successfully`. API healthcheck calls `/api/health/ready`; each worker healthcheck calls CLI `worker-health` for its role. No worker starts from FastAPI lifespan.

Assert `.env.example` contains all bounded operation settings and non-secret explanations for `MODERATION_METRICS_TOKEN`, backfill rates/leases, worker staleness, queue thresholds, and maintenance schedule. It must contain no real credential value.

- [ ] **Step 2: Run Docker role tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_docker_roles.py -q
```

Expected: FAIL because current API entrypoint runs migrations and Compose has only one role.

- [ ] **Step 3: Implement role-specific entrypoints and dependencies**

Use exact commands:

```sh
# deploy/entrypoint.sh
exec uvicorn app.main:app --host 0.0.0.0 --port 5000

# deploy/migration-entrypoint.sh
exec alembic upgrade head

# deploy/moderation-worker-entrypoint.sh
exec python -m app.workers.moderation_worker --worker-id "${MODERATION_WORKER_ID:-moderation-worker-1}"

# deploy/moderation-backfill-entrypoint.sh
exec python -m app.workers.moderation_backfill_worker --worker-id "${BACKFILL_WORKER_ID:-backfill-worker-1}"

# deploy/moderation-maintenance-entrypoint.sh
exec python -m app.workers.moderation_maintenance_worker --worker-id "${MAINTENANCE_WORKER_ID:-maintenance-worker-1}"
```

Every script starts with `#!/usr/bin/env sh` and `set -eu`. Dockerfile copies and `chmod +x` all five scripts. Compose migration has `restart: "no"`; API/workers use `restart: unless-stopped`. Backfill and maintenance expose no host port. Backfill gets lower CPU shares than realtime worker and API. Maintenance runs a scheduler loop that creates UTC daily jobs once; duplicate schedule keys make restarts safe.

Add exact environment names with safe example values:

```env
BACKFILL_DEFAULT_SCAN_QPS=50
BACKFILL_DEFAULT_PROVIDER_QPS=2
BACKFILL_REALTIME_MAX_AGE_SECONDS=5
BACKFILL_CURSOR_LEASE_SECONDS=60
BACKFILL_IDLE_SLEEP_SECONDS=2
MODERATION_REALTIME_WORKER_MAX_AGE_SECONDS=90
MODERATION_OPERATIONS_WORKER_MAX_AGE_SECONDS=180
MODERATION_QUEUE_MAX_OLDEST_SECONDS=300
MODERATION_DEAD_TASK_MAX=0
MODERATION_MAINTENANCE_MAX_AGE_HOURS=26
MODERATION_MAINTENANCE_UTC_HOUR=3
MODERATION_METRICS_TOKEN=replace-with-32-random-bytes
```

Production config rejects the literal metrics example and values outside Task 6/8 bounds.

- [ ] **Step 4: Validate tests, Compose rendering, image build, and migration role**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_docker_roles.py -q
docker compose config --quiet
docker build -t nantupy-backend:phase4-test .
docker compose run --rm migration
```

Expected: tests PASS, Compose config is valid, image builds, and the migration container exits `0` after upgrading to the sole head without starting API.

- [ ] **Step 5: Commit Docker role separation**

```bash
cd /d/NanTuPy
git add .env.example Dockerfile deploy/entrypoint.sh deploy/migration-entrypoint.sh deploy/moderation-worker-entrypoint.sh deploy/moderation-backfill-entrypoint.sh deploy/moderation-maintenance-entrypoint.sh docker-compose.yml tests/test_moderation_docker_roles.py
git commit -m "feat: split moderation deployment roles"
```

---

### Task 12: Turn the existing Baota guide into an executable operations runbook

**Files:**
- Modify: `deploy/README_BAOTA_DOCKER.md`
- Create: `tests/test_moderation_baota_runbook.py`

- [ ] **Step 1: Write a failing runbook command and safety test**

Parse fenced commands and assert the guide includes: database backup; image build; independent migration; role startup/status/logs; live/ready/moderation probes; metrics authentication; assessment creation/status; acknowledgement hash review; enforcement; pause/resume/cancel; legal hold create/release; 30/180/365 maintenance; risk decay; alerts; worker crash recovery; rollback order. Assert no command prints `.env`, DSN, JWT, secret, signed URL, raw body, Provider response, or database dump contents.

- [ ] **Step 2: Run the runbook test and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_baota_runbook.py -q
```

Expected: FAIL because the current guide describes only a single API container.

- [ ] **Step 3: Add exact BaoTa rollout and incident procedures**

Document these commands with `/www/wwwroot/NanTuPy` as the server path:

```bash
cd /www/wwwroot/NanTuPy
mysqldump --single-transaction --routines --triggers "$DB_NAME" > /www/backup/nantupy-pre-phase4.sql
docker compose build
docker compose run --rm migration
docker compose up -d nantupy-api moderation-worker moderation-backfill moderation-maintenance
docker compose ps
curl -fsS http://127.0.0.1:5000/api/health/live
curl -fsS http://127.0.0.1:5000/api/health/ready
curl -fsS http://127.0.0.1:5000/api/health/moderation
```

The dump command in the guide obtains credentials through a protected MySQL option file or interactive prompt, not command-line password. Add dry-run workflow:

```bash
docker compose exec moderation-backfill python -m app.cli.moderation_operations create-assessment --targets post,comment,user_profile,community,community_announcement,comic_event,comic_comment,post_media,user_profile_media,community_media,comic_event_media,legacy_public_media --batch-size 100 --scan-qps 50 --provider-qps 2 --actor-id 1 --request-key phase4-assessment-20260718
docker compose exec moderation-backfill python -m app.cli.moderation_operations status --run-id 1 --json
```

Require operator review of totals, sample cases through existing authorized admin API, false-positive rate, Provider cost, and acknowledgement hash before the exact `create-enforcement` command. State in bold operational language that assessment never hides or scores and that enforcement must not run until assessment is completed and acknowledged.

Add pause/resume/cancel and legal-hold CLI examples from Task 7. Define alerts: realtime worker age >90s, realtime oldest task >300s, any dead task, expired processing cursor, maintenance success age >26h, Provider unconfigured, or ready endpoint non-200. Logs are viewed per role with `docker compose logs --since 30m <service>` and must not be uploaded without redaction.

Rollback order: pause/cancel backfill; create legal holds for disputed cases; stop backfill/maintenance; keep API/realtime worker serving; take a fresh backup; restore a forward-compatible prior application image or deploy a forward-fix migration. The runbook must not instruct an operator to run `alembic downgrade` against development, staging, or production; downgrade roundtrip exists only inside the auto-created, whitelisted disposable MySQL migration fixture under `APP_ENV=test`. Never restore hidden content by SQL; use existing audited restore/appeal APIs.

- [ ] **Step 4: Run the runbook and Docker-role contract tests**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_baota_runbook.py tests/test_moderation_docker_roles.py -q
```

Expected: all tests PASS and every referenced service, endpoint, and CLI command exists exactly.

- [ ] **Step 5: Commit the BaoTa runbook**

```bash
cd /d/NanTuPy
git add deploy/README_BAOTA_DOCKER.md tests/test_moderation_baota_runbook.py
git commit -m "docs: add moderation operations runbook"
```

---

### Task 13: Verify migrations and failure recovery against real MySQL and Docker

**Files:**
- Create: `docker-compose.integration.yml`
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/test_moderation_phase4_mysql.py`
- Create: `tests/integration/test_moderation_phase4_faults.py`
- Create: `scripts/moderation_fault_drill.py`

- [ ] **Step 1: Write failing real-MySQL integration and drill assertions**

Use MySQL 8.4, not SQLite. The integration fixture automatically creates a separate disposable database whose generated name matches `^nantupy_phase4_integration_test_[a-z0-9_]+$`, sets `APP_ENV=test`, rejects any non-whitelisted/database-in-use name, and only there tests migration parent→head→parent→head. On the ordinary integration application database run upgrade/head only. Also test MySQL triggers, `SELECT ... FOR UPDATE`/CAS behavior, owner-level enforcement uniqueness races, risk-adjustment tuple uniqueness, UTF8MB4 fields, FK `SET NULL`, controlled 365-day admin-action delete, and one Alembic head. Test two live backfill worker processes claiming the same run.

Fault cases are exact: SIGKILL backfill after item commit/before cursor advance; SIGKILL maintenance after COS mock delete/before item completion; disconnect one worker from the database network until lease expiry; Provider returns 429/500/timeout then recovers; realtime task arrives while backfill is ready to call Provider. Assertions: recovery without cursor gap, duplicate case/hide/points/object deletion, accidental publication, dry-run disposition, leaked sentinel, or realtime starvation.

- [ ] **Step 2: Start MySQL and verify RED before fixtures are wired**

Run:

```bash
cd /d/NanTuPy
docker compose -f docker-compose.integration.yml up -d mysql
APP_ENV=test \
PHASE4_INTEGRATION_MIGRATION_DB_NAME="nantupy_phase4_integration_test_red_$(date -u +%Y%m%d%H%M%S)_$$" \
./.venv/Scripts/python.exe -m pytest \
  tests/integration/test_moderation_phase4_mysql.py \
  tests/integration/test_moderation_phase4_faults.py -q
```

Expected: FAIL on the explicit `Phase4IntegrationHarnessNotWired` assertion in each new test module. Remove only that assertion after observing RED.

- [ ] **Step 3: Implement a disposable MySQL stack and deterministic fault harness**

`docker-compose.integration.yml` defines `mysql:8.4`, healthcheck `mysqladmin ping`, a named disposable volume, UTF8MB4, UTC timezone, and test-only database/user values. It overlays all five application roles with `APP_ENV=test`, fake Provider/COS endpoints, and no production secret. `tests/integration/conftest.py` waits for health, upgrades the application test schema, and refuses to run unless DB host/name match the integration Compose values. Its migration-roundtrip fixture separately generates/creates a database named `nantupy_phase4_integration_test_<random>`, verifies `APP_ENV == "test"`, verifies the name whitelist and that it differs from every configured ordinary database, constructs a fixture-local Alembic URL, runs parent→head→parent→head, and drops it in `finally`; any failed guard aborts before downgrade or destructive cleanup.

Implement script commands:

```text
seed-backfill --mode assessment --targets post,post_media
wait-checkpoint --name item_committed
assert-recovered --run-id <id>
seed-maintenance --job retention_30d
provider-mode --result 429|500|timeout|approved|rejected
seed-realtime-task
assert-no-private-sentinel
```

The script communicates checkpoints through dedicated test-only files mounted under `/tmp/moderation-drill`, never production tables or logs. It outputs IDs/counts only. Fault tests use `docker compose kill -s SIGKILL moderation-backfill`, `docker compose up -d moderation-backfill`, and Docker network disconnect/connect for the selected worker. Always reconnect and stop the integration stack in fixture finalizers.

- [ ] **Step 4: Run real MySQL, Docker fault drills, and cleanup**

Run:

```bash
cd /d/NanTuPy
docker compose -f docker-compose.integration.yml up -d --build
APP_ENV=test \
PHASE4_INTEGRATION_MIGRATION_DB_NAME="nantupy_phase4_integration_test_$(date -u +%Y%m%d%H%M%S)_$$" \
./.venv/Scripts/python.exe -m pytest tests/integration/test_moderation_phase4_mysql.py -q
APP_ENV=test \
./.venv/Scripts/python.exe -m pytest tests/integration/test_moderation_phase4_faults.py -q
docker compose -f docker-compose.integration.yml exec -T nantupy-api alembic heads
docker compose -f docker-compose.integration.yml down -v
```

Expected: all tests PASS, container Alembic prints exactly one head, fault assertions show no duplicates/gaps/leaks, and stack/volume removal succeeds.

- [ ] **Step 5: Commit real-database and failure-drill coverage**

```bash
cd /d/NanTuPy
git add docker-compose.integration.yml tests/integration/conftest.py tests/integration/test_moderation_phase4_mysql.py tests/integration/test_moderation_phase4_faults.py scripts/moderation_fault_drill.py
git commit -m "test: verify moderation operations on MySQL"
```

---

### Task 14: Run Flutter as a phases 1–3 regression gate only

**Files:**
- Verify: `test/moderation_models_test.dart`
- Verify: `test/moderation_api_contract_test.dart`
- Verify: `test/post_moderation_flow_test.dart`
- Verify: `test/comment_moderation_flow_test.dart`
- Verify: `test/chat_moderation_failure_test.dart`
- Verify: `test/community_chat_moderation_failure_test.dart`
- Verify: `test/my_moderation_notifier_test.dart`
- Verify: `test/my_moderation_screen_test.dart`
- Verify: `test/post_media_upload_flow_test.dart`
- Verify: `test/profile_media_moderation_flow_test.dart`
- Verify: `test/community_media_moderation_flow_test.dart`
- Verify: `test/comic_media_moderation_flow_test.dart`
- Verify: `test/chat_media_pending_test.dart`
- Verify: `test/community_chat_media_pending_test.dart`
- Verify: `test/moderation_phase2_acceptance_test.dart`
- Verify: `test/moderation_phase3_acceptance_test.dart`
- Verify: existing post/comment/chat/community/block/privacy/push regression tests listed below

- [ ] **Step 1: Capture Flutter working-tree state without editing it**

Run:

```bash
cd /d/FlutterProject/nonto
git status --short > /tmp/nonto-phase4-status-before.txt
git diff --stat
```

Expected: command succeeds. Preserve any pre-existing unrelated changes and do not stage them.

- [ ] **Step 2: Run phase 1–3 moderation tests**

Run:

```bash
cd /d/FlutterProject/nonto
flutter test \
  test/moderation_models_test.dart \
  test/moderation_api_contract_test.dart \
  test/post_moderation_flow_test.dart \
  test/comment_moderation_flow_test.dart \
  test/chat_moderation_failure_test.dart \
  test/community_chat_moderation_failure_test.dart \
  test/my_moderation_notifier_test.dart \
  test/my_moderation_screen_test.dart \
  test/post_media_upload_flow_test.dart \
  test/profile_media_moderation_flow_test.dart \
  test/community_media_moderation_flow_test.dart \
  test/comic_media_moderation_flow_test.dart \
  test/chat_media_pending_test.dart \
  test/community_chat_media_pending_test.dart \
  test/moderation_phase2_acceptance_test.dart \
  test/moderation_phase3_acceptance_test.dart
```

Expected: all tests PASS. Failure returns to the relevant phase 1–3 implementation; phase 4 adds no Dart adapter, screen, model, API, generated file, JSON field, database table/column, Drift migration, or `schemaVersion` change.

- [ ] **Step 3: Run existing high-risk regressions**

Run:

```bash
cd /d/FlutterProject/nonto
flutter test \
  test/nonto_create_post_phase5a_regression_test.dart \
  test/post_visibility_task8_test.dart \
  test/chat_reliability_regression_test.dart \
  test/chat_message_types_contract_test.dart \
  test/nonto_community_chat_phase9_regression_test.dart \
  test/nonto_community_chat_media_mentions_regression_test.dart \
  test/blocking_behavior_test.dart \
  test/email_privacy_behavior_test.dart \
  test/push_app_state_regression_test.dart \
  test/push_dedupe_regression_test.dart
```

Expected: all tests PASS for posts, comments, private/community chat, media, blocking, privacy, and push.

- [ ] **Step 4: Run full Flutter tests/analyzer and prove no diff**

Run:

```bash
cd /d/FlutterProject/nonto
flutter test
flutter analyze
git status --short > /tmp/nonto-phase4-status-after.txt
diff -u /tmp/nonto-phase4-status-before.txt /tmp/nonto-phase4-status-after.txt
git diff --stat
```

Expected: all tests PASS, analyzer reports no issues, status snapshots are identical, and diff stat contains no phase-4 Flutter change. In particular, no Flutter model/serializer/API contract, Drift table/column/migration, generated file, or `schemaVersion` changes.

- [ ] **Step 5: Do not create a Flutter commit**

Expected: no `git add`, `git commit`, generated source, dependency update, or lockfile change occurs in `D:\FlutterProject\nonto` during phase 4.

---

### Task 15: Add phase-4 acceptance coverage and run full verification

**Files:**
- Create: `tests/test_moderation_phase4_acceptance.py`

- [ ] **Step 1: Write a failing full-path acceptance test**

Build one scenario that creates public text plus multiple media for one owner, safely mapped and unmappable `legacy_imported` public media, and forbidden direct/community chat/identity-evidence sentinels; captures upper bounds; runs assessment; pauses/resumes after a lease expiry; completes mocked Provider work; acknowledges and runs enforcement twice; creates an open report/legal hold; runs 30/180/365 maintenance; closes/releases protection; runs cleanup again; advances 90 days and applies risk decay; then inspects health/metrics/logs.

The acceptance assertions must prove: IDs above bounds wait for a later run; assessment performs zero disposition and can decide each media independently; safely mapped legacy public media is assessed while unmappable legacy public media enters `MANUAL_INVENTORY`; Message/WSMessageLog and all five forbidden UploadPurpose values are never selected; realtime task outranks backfill Provider work; owner aggregation follows any reject > any manual > all approved, and one owner revision with several media hides/scores at most once with the exact owner-level key; manual owner is not hidden/scored; crash resumes; protected data survives; the four locked retention anchors clean exactly after protection ends; three complete violation-free periods append three `-1` ledger rows for one anchor down to floor zero; a new confirmed violation resets the anchor; permanent account deactivation remains; health labels stay bounded; sentinels never leak.

- [ ] **Step 2: Run acceptance and verify RED through a deliberate boundary stub**

Before saving the final test, inject a test-local `UnwiredPhase4OperationsBoundary` service that raises at assessment creation, then run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_phase4_acceptance.py -q
```

Expected: FAIL exactly at `UnwiredPhase4OperationsBoundary`. Remove the stub and bind the test to production services after observing RED.

- [ ] **Step 3: Bind acceptance to production seams without changing behavior**

Use dependency/service injection only:

```python
clock = FakeClock(datetime(2026, 7, 18, tzinfo=timezone.utc))
provider = ScriptedModerationProvider([
    ProviderResult.approved(),
    ProviderResult.high_confidence_rejected(category="illegal"),
    ProviderResult.manual_review(category="other"),
])
cos = RecordingCosGateway()
worker = ModerationBackfillWorker(
    repository=repository, service=service, clock=clock,
    rate_limiter=rate_limiter,
)
maintenance = ModerationMaintenanceWorker(
    retention=retention, risk_decay=risk_decay, clock=clock,
)
```

The test uses isolated MySQL transaction fixtures, phase-3 Provider/COS protocols, existing governance adapters/repository, and production CLI/service methods. Do not add an acceptance-only branch to production code.

- [ ] **Step 4: Run complete backend, Alembic, Docker, and Flutter verification**

Backend focused and full suite:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest \
  tests/test_moderation_backfill_targets.py \
  tests/test_moderation_backfill_privacy.py \
  tests/test_moderation_backfill_modes.py \
  tests/test_moderation_backfill_idempotency.py \
  tests/test_moderation_backfill_claims.py \
  tests/test_moderation_backfill_recovery.py \
  tests/test_moderation_backfill_control.py \
  tests/test_moderation_rate_limiter.py \
  tests/test_moderation_realtime_priority.py \
  tests/test_moderation_operations_cli.py \
  tests/test_moderation_operations_health.py \
  tests/test_moderation_operations_metrics.py \
  tests/test_moderation_operations_logging.py \
  tests/test_moderation_retention_operations.py \
  tests/test_moderation_legal_hold.py \
  tests/test_moderation_maintenance_recovery.py \
  tests/test_moderation_risk_decay_operations.py \
  tests/test_moderation_docker_roles.py \
  tests/test_moderation_baota_runbook.py \
  tests/test_moderation_phase4_acceptance.py -q
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m alembic upgrade head
./.venv/Scripts/python.exe -m alembic heads
```

Expected: all tests PASS, migration succeeds, and exactly one head is printed.

Real MySQL and Docker roles:

```bash
cd /d/NanTuPy
docker compose config --quiet
docker compose -f docker-compose.integration.yml up -d --build
./.venv/Scripts/python.exe -m pytest tests/integration/test_moderation_phase4_mysql.py tests/integration/test_moderation_phase4_faults.py -q
docker compose -f docker-compose.integration.yml exec -T nantupy-api python -m app.cli.moderation_operations worker-health --role backfill --max-age-seconds 180
docker compose -f docker-compose.integration.yml exec -T nantupy-api alembic heads
docker compose -f docker-compose.integration.yml down -v
```

Expected: Compose renders/builds, all five roles reach expected state, integration/fault tests PASS, worker health exits 0, one head is printed, and cleanup removes the disposable stack.

Flutter gate:

```bash
cd /d/FlutterProject/nonto
flutter test
flutter analyze
```

Expected: all tests PASS and analyzer reports no issues, with no Flutter file changes.

- [ ] **Step 5: Commit acceptance coverage and verify repository scope**

```bash
cd /d/NanTuPy
git add tests/test_moderation_phase4_acceptance.py
git commit -m "test: verify moderation operations phase four"
git status --short
git diff --check
git -C /d/FlutterProject/nonto status --short
```

Expected: phase-4 implementation commits contain only files named by their tasks; backend has no unstaged phase-4 change, Flutter status equals its pre-gate snapshot, and neither repository contains credentials, raw moderation bodies, signed URLs, Provider responses, or generated debug artifacts.

---

## Final self-review checklist

- [ ] **Phase gates:** Task 1 stops unless phases 1–3 backend, Provider/media, privacy, worker, Alembic and Flutter acceptance pass; Task 14 modifies no Flutter file.
- [ ] **Schema and migration:** Task 2 covers runs, cursors, per-asset items, owner decisions, operation heartbeats, shared rate buckets, phase-4-only maintenance runs/items, legal holds, risk adjustments, task priority/backfill FK without shared-table XOR, real MySQL guards, and dynamic one-head migration generation. Migration file numbering is never treated as parent.
- [ ] **Public-only adapters:** Task 3 contains an explicit registry for public posts/comments/profiles/communities/announcements/comic events/comments and public media; it scans mapped and unmappable non-forbidden `legacy_imported` objects, sends unmappable ones to manual inventory, and excludes `Message`, `WSMessageLog` plus exactly `direct_chat_image`, `direct_chat_video`, `community_chat_image`, `community_chat_video`, `identity_evidence_image` from phase 4.
- [ ] **Backfill correctness:** Tasks 4–6 cover per-asset assessment, owner-level enforcement key/aggregation, reject > manual > approved precedence, zero dry-run disposition, one hide/score per owner revision, required acknowledgement, primary-key cursors, immutable upper bounds, CAS claims, leases, crash recovery, pause/resume/cancel, realtime priority, and process-local plus database-shared rate limits.
- [ ] **Operations:** Tasks 7–8 cover exact CLI, `/live`, `/ready`, `/moderation`, bounded metrics, heartbeat/queue thresholds, and sentinel-tested log/API/metric redaction.
- [ ] **Retention and risk:** Tasks 9–10 make phase 4 the sole retention/decay executor; lock anchors to revision `rejected_at`, case `completed_at` for quarantine/case metadata, and action `created_at`; protect pending/manual/open/held work; keep approved content on business lifecycle; decay once per complete 30-day period under unique `(user_id,anchor_violation_at,period_number)`, reset on a new confirmed violation, stop at zero, and never undo permanent deactivation.
- [ ] **Deployment and runbook:** Tasks 11–13 cover one image with independent migration/API/realtime-worker/backfill/maintenance roles, migration job ownership, BaoTa rollout/forward-fix rollback, real MySQL, Docker, worker kill/network/Provider fault drills, and one Alembic head. Downgrade roundtrip exists only in auto-created `APP_ENV=test` databases that pass name whitelists; ordinary paths are upgrade/head only.
- [ ] **No phase duplication:** Search phase-4 files for new local moderation rules, new governance state machines, direct Tencent SDK clients, alternate COS upload models, or Flutter feature code; expected: none. Phase 4 calls phase 1–3 interfaces.
- [ ] **Placeholder scan:** Run this exact script and expect `placeholder scan clean`:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe - <<'PY'
from pathlib import Path
p = Path("docs/superpowers/plans/2026-07-18-content-moderation-phase4-operations.md")
text = p.read_text(encoding="utf-8")
forbidden = [
    "TB" + "D", "TO" + "DO", "implement " + "later",
    "fill in " + "details", "similar to " + "Task",
    "appropriate error " + "handling", "类似" + "任务",
]
found = [value for value in forbidden if value in text]
assert not found, found
print("placeholder scan clean")
PY
```

- [ ] **Type consistency:** Verify every task uses exact values from `BackfillMode`, `RunStatus`, `CursorStatus`, `ItemOutcome`, `MaintenanceJobType`, `PublicBackfillSnapshot`, `LegacyMediaInventoryCandidate`, `ClaimedCursor`, and `PublicBackfillTargetAdapter`; reuse phase-2 `NormalizedTaskDecision`/`NormalizedTaskResult`/`RiskCategory`/`Severity`/integer `rule_version`; assessment and exact owner-level enforcement keys match the locked functions.
- [ ] **Migration consistency:** Immediately before Task 2 generation, `alembic heads` must print exactly one head; generated `down_revision` must equal that live value, never the migration filename/rev-id/date. Only whitelisted fixture-created disposable MySQL under `APP_ENV=test` runs downgrade; all ordinary commands run only `upgrade head`/`heads`. After all migration and Docker tests, local and container commands must still print one head.
- [ ] **Privacy consistency:** Operation rows, CLI, health, metrics and logs contain IDs, hashes, enums, counts and bounded safe codes only; no body, JWT, credentials, DSN, signed URL, email, username, private media, or full Provider response.
- [ ] **Working-tree scope:** During plan authoring only `docs/superpowers/plans/2026-07-18-content-moderation-phase4-operations.md` is created/edited and no commit is made. During later implementation, each commit stages only its task's exact files; Flutter remains unchanged.
