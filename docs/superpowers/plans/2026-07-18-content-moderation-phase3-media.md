# Content Moderation Phase 3 Media Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在阶段 2 治理状态机与独立 Worker 已完整上线的硬前提下，实现 COS 私有隔离上传、腾讯云 TMS/IMS/VM 可注入审核、媒体待审发布/清理 saga，以及后端和 Flutter 全部媒体入口的不可绕过接入。

**Architecture:** 客户端只提交后端签发的 typed upload session/object ID，使用绑定精确请求头且十分钟失效的 PUT URL 写入私有 `quarantine/`；后端通过 COS HEAD、受限下载、Pillow 和 ffprobe 确认真实媒体后冻结对象，再把对象绑定到阶段 2 的案件、版本、任务、租约和幂等流程。独立 Worker 通过可替换 Provider 调用腾讯云 TMS/IMS/VM，且每个 `ProviderResult` 必须先由 `normalize_provider_result()` 转成阶段 2 的 `NormalizedTaskResult` 或 `ProviderStillProcessing`；全部对象通过后由可重入发布 saga 复制到 `public/` 并原子应用阶段 2 revision。媒体过期、孤儿、发布后 quarantine 删除和补偿不伪装成需要案件的 `ModerationTask`，而由绑定 `upload_object_id` 的 `MediaCleanupJob` 以独立幂等键、claim token 和租约执行；治理 retention 在阶段 4 统一编排。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x synchronous ORM, Alembic, MySQL/PyMySQL, Pydantic 2, qcloud-cos SDK, Tencent Cloud Python SDK TMS/IMS/VM, Pillow, ffprobe/FFmpeg, pytest, Flutter/Dart, Dio, Riverpod, Drift, flutter_test

---

## Scope and execution rules

- 阶段 2 是硬前置。`ModerationCase`、`ModerationTask`、`ContentRevision`、`ModerationRepository`、`ModerationGovernanceService`、`ModerationTargetAdapter`、案件状态机、claim token、租约、重试、审计、违规账本、发布限制、用户案件 API、Flutter `ModerationStatus` 和独立 Worker 必须存在且阶段 2 验收通过。门禁失败时停止，不在阶段 3 重建状态机、任务表、治理 API 或第二套 Worker。
- 后端命令从 `D:\NanTuPy`（Git Bash `/d/NanTuPy`）运行；Flutter 命令从 `D:\FlutterProject\nonto`（Git Bash `/d/FlutterProject/nonto`）运行。
- 本计划中的每个实现任务都遵循 TDD：先写测试，运行指定命令确认 RED，再写该步给出的最小接口/实现，运行指定命令确认 GREEN，最后提交。计划编写本身不提交；实施时按任务提交。
- 单元、契约、集成和验收测试只使用 `FakeCosStorage`、`FakeTencentModerationProvider`、本地生成图片/视频 fixture 和 stub transport；不得读取 `.env` 中的真实云凭据，不得访问真实 COS/TMS/IMS/VM。
- COS 隔离桶/前缀必须私有。客户端不能选择 bucket、region、key、公开 URL、任意 URL或 Provider URL；业务接口只接收后端生成的 `upload_object_id`。
- 私聊和实时社群聊天的文本仍只走阶段 1/2 本地同步审核，绝不发送 TMS。`direct_chat_image`/`community_chat_image` 的初次媒体提交允许发送 IMS，`direct_chat_video`/`community_chat_video` 的初次媒体提交允许发送 VM，后续视频只按已保存 job ID 轮询；通过前不 fanout、不推送、不更新会话 preview。
- Provider I/O、COS HEAD/GET/COPY/DELETE 均在数据库事务之外执行。任务 claim 先提交；结果和 saga checkpoint 在新事务中用 claim token、case version、content hash、对象 generation/ETag 条件更新。
- Repository/service 内只 `flush()`；API 路由和 Worker 拥有 `commit()`。任何 Provider/COS 故障都失败关闭，不得自动公开。
- 实施 Task 2 时必须动态读取当时真实 Alembic head 并写入新迁移 `down_revision`；不得把调查时或本计划中的 revision 当成父版本。
- 媒体 HTTP 合同锁定为：`UPLOAD_REFERENCE_REQUIRED` 422/不可重试、`MEDIA_FORMAT_INVALID` 422/不可重试、`MEDIA_INSPECTION_UNAVAILABLE` 503/可重试、`UPLOAD_OBJECT_CHANGED` 409/不可重试、`UPLOAD_OBJECT_FROZEN` 409/不可重试；失败 JSON 的 `detail.code/detail.message/detail.retryable` 必须与下方表完全一致。`MEDIA_PENDING_REVIEW` 是 HTTP 202 accepted，不是失败。
- 阶段 3 只复用阶段 2 的 `RiskCategory`、`Severity`、整数 `rule_version`、`TaskType`、`NormalizedTaskResult`、`ProviderStillProcessing`、`TaskStatus` 和完成/重试入口；不得定义媒体专用风险分类、severity、规则版本类型、审核任务枚举或结果类型。
- 任务依赖锁定为：Task 1 门禁；Task 2 依赖 1；Tasks 3–4 依赖 2 且 Task 4 依赖 3；Task 5 依赖 1–4；Task 6 依赖 1、2、5；Task 7 依赖 2、3、6；Tasks 8–10 依赖 4、6、7；Task 11 依赖 8–10；Task 12 依赖 2、3、8–11；Task 13 依赖 4、11；Task 14 依赖阶段 1 Drift v2 与 Task 13；Tasks 15–17 依赖 13–14 及对应后端 Task 8–10；Task 18 依赖 3–7；Task 19 依赖 1–18。不得提前执行 legacy import、Flutter 切换或真实云验收。

## Locked file structure

### Backend

- `app/models/media.py`: `UploadSession`、`UploadObject`、`MediaCleanupJob`、`LegacyMediaManualInventory` ORM；上传状态、只读 legacy provenance、业务绑定和专用清理租约只在此定义。
- `app/services/media_types.py`: typed purpose/kind/status、purpose policy、COS/Provider DTO 和异常；路由不得按裸字符串自行判断用途。
- `app/services/cos_storage.py`: `CosStorage` Protocol、腾讯 COS adapter、精确 PUT/临时 GET 签名、HEAD/受限下载/copy/delete；不含业务事务。
- `app/services/media_inspector.py`: Pillow 真实格式/像素/帧预算检查与 ffprobe 视频检查。
- `app/services/upload_service.py`: 会话创建、后端 key 生成、确认冻结、所有权/用途/大小校验和一次性 attach。
- `app/routers/upload.py`: 只暴露 typed session API；旧任意 URL、通用上传和公开 URL API 在切换任务中关闭。
- `app/services/tencent_moderation_provider.py`: TMS/IMS/VM SDK adapter、标签标准化、错误分类；通过依赖注入替换。
- `app/services/media_moderation_service.py`: 把 confirmed 对象绑定阶段 2 revision/case/task，聚合对象结果并驱动既有状态机。
- `app/services/media_publish_saga.py`: quarantine -> public copy、原子应用、补偿和清理 checkpoint。
- `app/workers/moderation_worker.py`: 复用阶段 2 `TaskType` 和 claim/lease/retry 循环，增加 image/video/video_poll processor；另开同进程内的 `MediaCleanupJob` claim/lease 分支，但绝不把媒体 cleanup 写成 `ModerationTask`。
- `app/services/moderation_targets.py`: 在既有 target adapter payload 白名单中加入 upload object ID，公开 URL只能由 saga 注入。

### Flutter

- `lib/models/upload_session.dart`: `UploadPurpose`、`UploadObject`、`UploadSession`、确认结果和 JSON。
- `lib/services/upload_transport.dart`: 可注入的精确 PUT transport，不拼 key、不推导公开 URL、不向 COS 发送 JWT。
- `lib/services/api/upload_service.dart`: create -> PUT -> confirm 编排和 typed error；替换现有通用 URL upload。
- `lib/services/pending_moderation_store.dart`: Drift v3-backed pending case registry，恢复帖子、资料、社群、漫展和聊天待审状态；阶段 1 的 v2 failure 列迁移保持原样，阶段 2 不再升级 Drift。
- 复用阶段 2 的 `ModerationStatus`、`ModeratedContentResponse<T>` 和用户文案；不得创建平行审核状态枚举。

## Canonical contracts

```python
class UploadPurpose(StrEnum):
    POST_IMAGE = "post_image"
    POST_VIDEO = "post_video"
    POST_VIDEO_THUMBNAIL = "post_video_thumbnail"
    USER_AVATAR = "user_avatar"
    USER_COVER = "user_cover"
    COMMUNITY_AVATAR = "community_avatar"
    COMMUNITY_BANNER = "community_banner"
    COMIC_EVENT_IMAGE = "comic_event_image"
    DIRECT_CHAT_IMAGE = "direct_chat_image"
    DIRECT_CHAT_VIDEO = "direct_chat_video"
    COMMUNITY_CHAT_IMAGE = "community_chat_image"
    COMMUNITY_CHAT_VIDEO = "community_chat_video"
    IDENTITY_EVIDENCE_IMAGE = "identity_evidence_image"
    ROLE_PORTFOLIO_IMAGE = "role_portfolio_image"

class MediaKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"

class UploadSessionStatus(StrEnum):
    OPEN = "open"
    CONFIRMED = "confirmed"
    ATTACHED = "attached"
    EXPIRED = "expired"
    REJECTED = "rejected"

class UploadObjectStatus(StrEnum):
    ISSUED = "issued"
    CONFIRMED = "confirmed"
    ATTACHED = "attached"
    APPROVED = "approved"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    LEGACY_IMPORTED = "legacy_imported"
    REJECTED = "rejected"
    DELETED = "deleted"

@dataclass(frozen=True)
class PurposePolicy:
    kind: MediaKind
    max_bytes: int
    max_objects: int
    allowed_declared_types: frozenset[str]
    max_pixels: int | None
    max_total_frame_pixels: int | None
    max_duration_seconds: float | None

PURPOSE_POLICIES = {
    UploadPurpose.POST_IMAGE: PurposePolicy(MediaKind.IMAGE, 15*1024*1024, 9, frozenset({"image/jpeg","image/png","image/webp","image/gif"}), 40_000_000, 120_000_000, None),
    UploadPurpose.POST_VIDEO: PurposePolicy(MediaKind.VIDEO, 200*1024*1024, 1, frozenset({"video/mp4","video/quicktime"}), None, None, 300.0),
    UploadPurpose.POST_VIDEO_THUMBNAIL: PurposePolicy(MediaKind.IMAGE, 8*1024*1024, 1, frozenset({"image/jpeg","image/png","image/webp"}), 16_000_000, 16_000_000, None),
    UploadPurpose.USER_AVATAR: PurposePolicy(MediaKind.IMAGE, 8*1024*1024, 1, frozenset({"image/jpeg","image/png","image/webp"}), 16_000_000, 16_000_000, None),
    UploadPurpose.USER_COVER: PurposePolicy(MediaKind.IMAGE, 12*1024*1024, 1, frozenset({"image/jpeg","image/png","image/webp"}), 32_000_000, 32_000_000, None),
    UploadPurpose.COMMUNITY_AVATAR: PurposePolicy(MediaKind.IMAGE, 8*1024*1024, 1, frozenset({"image/jpeg","image/png","image/webp"}), 16_000_000, 16_000_000, None),
    UploadPurpose.COMMUNITY_BANNER: PurposePolicy(MediaKind.IMAGE, 12*1024*1024, 1, frozenset({"image/jpeg","image/png","image/webp"}), 32_000_000, 32_000_000, None),
    UploadPurpose.COMIC_EVENT_IMAGE: PurposePolicy(MediaKind.IMAGE, 12*1024*1024, 9, frozenset({"image/jpeg","image/png","image/webp"}), 32_000_000, 32_000_000, None),
    UploadPurpose.DIRECT_CHAT_IMAGE: PurposePolicy(MediaKind.IMAGE, 12*1024*1024, 1, frozenset({"image/jpeg","image/png","image/webp","image/gif"}), 32_000_000, 96_000_000, None),
    UploadPurpose.DIRECT_CHAT_VIDEO: PurposePolicy(MediaKind.VIDEO, 100*1024*1024, 1, frozenset({"video/mp4","video/quicktime"}), None, None, 120.0),
    UploadPurpose.COMMUNITY_CHAT_IMAGE: PurposePolicy(MediaKind.IMAGE, 12*1024*1024, 1, frozenset({"image/jpeg","image/png","image/webp","image/gif"}), 32_000_000, 96_000_000, None),
    UploadPurpose.COMMUNITY_CHAT_VIDEO: PurposePolicy(MediaKind.VIDEO, 100*1024*1024, 1, frozenset({"video/mp4","video/quicktime"}), None, None, 120.0),
    UploadPurpose.IDENTITY_EVIDENCE_IMAGE: PurposePolicy(MediaKind.IMAGE, 10*1024*1024, 9, frozenset({"image/jpeg","image/png","image/webp"}), 24_000_000, 24_000_000, None),
    UploadPurpose.ROLE_PORTFOLIO_IMAGE: PurposePolicy(MediaKind.IMAGE, 12*1024*1024, 12, frozenset({"image/jpeg","image/png","image/webp"}), 32_000_000, 32_000_000, None),
}
```

## Locked HTTP and pending-ACK contracts

```python
MEDIA_HTTP_ERRORS = {
    "UPLOAD_REFERENCE_REQUIRED": (422, "请使用已确认的上传对象", False),
    "MEDIA_FORMAT_INVALID": (422, "媒体格式无效", False),
    "MEDIA_INSPECTION_UNAVAILABLE": (503, "媒体检测服务暂不可用，请稍后重试", True),
    "UPLOAD_OBJECT_CHANGED": (409, "上传对象已发生变化，请重新上传", False),
    "UPLOAD_OBJECT_FROZEN": (409, "上传对象已冻结，不能修改", False),
}
MEDIA_PENDING_REVIEW = (202, "媒体审核中", False)
```

Every failure uses exactly `{"detail":{"code":code,"message":message,"retryable":retryable}}`. `MEDIA_PENDING_REVIEW` is an accepted response and therefore must not be wrapped as `detail`, mapped to `ApiResponse.success == false`, persisted as `failureCode`, or sent through the failed-ACK callback. HTTP pending responses use status 202 and the exact top-level fields `code="MEDIA_PENDING_REVIEW"`, `message="媒体审核中"`, `retryable=false`, `moderation_status="pending_review"`, `case_id`, and the approved/current-safe `content` projection.

Locked WebSocket pending ACK:

```json
{
  "type": "ack",
  "request_id": "original request id",
  "client_msg_id": "client-7",
  "clientMsgId": "client-7",
  "status": 202,
  "code": "MEDIA_PENDING_REVIEW",
  "msg": "媒体审核中",
  "message": "媒体审核中",
  "case_id": 321,
  "message_id": 987,
  "moderation_status": "pending_review"
}
```

The two client-ID spellings carry the identical original value. `message_id` is the durable server pending `Message.id`, not a local optimistic ID. Flutter locates and settles exactly one queued/local item by `client_msg_id`/`clientMsgId`, then replaces its ID with and persists `message_id`; it must not locate by `message_id`, classify status 202 as sent/failed, or invoke the failure callback.

```python
@dataclass(frozen=True)
class CosHead:
    key: str
    content_length: int
    content_type: str
    etag: str
    version_id: str | None
    metadata: Mapping[str, str]

@dataclass(frozen=True)
class PresignedPut:
    url: str
    method: Literal["PUT"]
    expires_at: datetime
    headers: Mapping[str, str]

class CosStorage(Protocol):
    def presign_put(self, *, key: str, content_type: str, content_length: int,
                    upload_token: str, expires_seconds: int = 600) -> PresignedPut: ...
    def head(self, *, key: str) -> CosHead: ...
    def download_limited(self, *, key: str, destination: BinaryIO,
                         max_bytes: int) -> int: ...
    def presign_get(self, *, key: str, expires_seconds: int = 300) -> str: ...
    def copy_if_absent(self, *, source_key: str, source_etag: str,
                       destination_key: str) -> CosHead: ...
    def delete(self, *, key: str, version_id: str | None = None) -> None: ...
```

```python
from app.services.moderation_types import (
    NormalizedTaskResult, ProviderStillProcessing, RiskCategory, Severity, TaskType,
)

class ProviderDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    MANUAL_REVIEW = "manual_review"
    PROCESSING = "processing"

@dataclass(frozen=True)
class ProviderResult:
    decision: ProviderDecision
    risk_category: RiskCategory | None
    severity: Severity
    confidence: float | None
    request_id: str | None
    provider_job_id: str | None = None
    poll_after_seconds: int | None = None

class ModerationProvider(Protocol):
    def moderate_text(self, *, text: str, data_id: str) -> ProviderResult: ...
    def moderate_image(self, *, temporary_url: str, data_id: str) -> ProviderResult: ...
    def submit_video(self, *, temporary_url: str, data_id: str) -> ProviderResult: ...
    def poll_video(self, *, provider_job_id: str, data_id: str) -> ProviderResult: ...

def normalize_provider_result(
    result: ProviderResult, *, policy_version: str, rule_version: int,
) -> NormalizedTaskResult | ProviderStillProcessing: ...
```

`ProviderResult` is adapter-internal and can never be passed to phase-2 `complete_task()`. Pure `normalize_provider_result()` preserves phase-2 `RiskCategory`, `Severity` and integer `rule_version`; terminal decisions become `NormalizedTaskResult`, while `PROCESSING` requires a non-empty job ID and becomes `ProviderStillProcessing`. The Worker branch that receives `ProviderStillProcessing` stores its job ID, changes/schedules the claimed task as `TaskType.VIDEO_POLL`, clears the current claim, and leaves the task/case pending without calling `complete_task()`.

The only client media references accepted by business APIs are:

```json
{
  "image_upload_ids": [101, 102],
  "video_upload_id": 103,
  "video_thumbnail_upload_id": 108,
  "avatar_upload_id": 104,
  "cover_upload_id": 105,
  "banner_upload_id": 106,
  "media_upload_id": 107
}
```

---

### Task 1: Enforce the phase-2 hard prerequisite

**Files:**
- Verify: `app/models/moderation.py`
- Verify: `app/services/moderation_types.py`
- Verify: `app/services/moderation_state_machine.py`
- Verify: `app/services/moderation_repository.py`
- Verify: `app/services/moderation_governance_service.py`
- Verify: `app/services/moderation_targets.py`
- Verify: `app/workers/moderation_worker.py`
- Verify: `tests/test_moderation_phase2_acceptance.py`
- Verify: `lib/models/moderation.dart`
- Verify: `lib/services/api/moderation_service.dart`
- Verify: `test/moderation_phase2_acceptance_test.dart`

- [ ] **Step 1: Verify exact phase-2 symbols without editing them**

```python
from app.services.moderation_types import (
    CaseStatus, NormalizedTaskResult, ProviderStillProcessing, PublicModerationStatus,
    Severity, TaskStatus, TaskType,
)
from app.services.moderation_repository import ModerationRepository
from app.services.moderation_governance_service import ModerationGovernanceService
from app.services.moderation_targets import TARGET_ADAPTERS

assert {s.value for s in CaseStatus} == {"pending","approved","rejected","manual_review","cancelled"}
assert {s.value for s in TaskStatus} == {"pending","processing","succeeded","failed","dead"}
assert {t.value for t in TaskType} >= {"text","image","video","video_poll"}
assert {s.value for s in Severity} == {"none","low","medium","high"}
assert NormalizedTaskResult is not ProviderStillProcessing
assert {"post","user_profile","community","comic_event","message"} <= set(TARGET_ADAPTERS)
```

- [ ] **Step 2: Run phase-2 backend and Flutter gates**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_phase2_acceptance.py tests/test_moderation_worker.py tests/test_content_revisions.py -q
./.venv/Scripts/python.exe -m alembic heads
cd /d/FlutterProject/nonto
flutter test test/moderation_phase2_acceptance_test.dart test/moderation_models_test.dart
```

Expected: all tests PASS and Alembic prints exactly one head. Missing files/imports, multiple heads, a Worker running in API lifespan, or failed phase-2 acceptance stops this plan.

- [ ] **Step 3: Confirm phase boundaries**

Search phase-2 production code and confirm routes call the one `ModerationGovernanceService`, task claims commit before processing, and case transitions are only performed through `require_case_transition()`. Confirm `NormalizedTaskResult`, `ProviderStillProcessing`, `Severity`, integer `rule_version`, and `TaskType` are the only cross-stage task contracts. Record the actual constructor/dependency names in the implementation branch and use those exact names in Tasks 5–7; do not add aliases, a second state machine, or media-specific copies of those types.

- [ ] **Step 4: Run existing media-path regressions as a baseline**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_post_visibility_contracts.py tests/test_chat_message_type_contracts.py tests/test_community_chat_contracts.py -q
cd /d/FlutterProject/nonto
flutter test test/nonto_create_post_phase5a_regression_test.dart test/nonto_edit_profile_phase4d_regression_test.dart test/nonto_community_chat_media_mentions_regression_test.dart
```

Expected: baseline tests PASS before media behavior changes.

- [ ] **Step 5: Do not commit a passing gate**

Expected: both `git status --short` outputs are unchanged. Any prerequisite repair belongs to the phase-2 plan and must be completed before Task 2.

---

### Task 2: Add upload session/object ORM and a dynamic Alembic migration

**Files:**
- Create: `app/models/media.py`
- Create: `app/services/media_types.py`
- Modify: `app/models/models.py:97-181,457-510`
- Modify: `app/models/__init__.py`
- Modify: `alembic/env.py`
- Create: `alembic/versions/2026_07_18_0500_add_moderated_media_uploads.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_media_models.py`
- Create: `tests/test_media_migration.py`

**Depends on:** Task 1 only. Tasks 3–19 depend on this schema task; Task 12's legacy import must run only after Tasks 8–11 have closed business write bypasses.

- [ ] **Step 1: Write failing metadata and migration tests**

```python
def test_media_schema_is_owned_and_idempotent():
    tables = Base.metadata.tables
    assert {"upload_sessions", "upload_objects", "media_cleanup_jobs",
            "legacy_media_manual_inventory"} <= set(tables)
    assert unique("upload_objects", "quarantine_key")
    assert unique("upload_objects", "upload_token_hash")
    assert unique("upload_objects", "moderation_task_id")
    assert index("upload_sessions", "user_id", "status", "expires_at")
    assert index("upload_objects", "status", "confirmed_at")
    assert index("upload_objects", "moderation_case_id", "sort_order")
    assert unique("media_cleanup_jobs", "idempotency_key")
    assert index("media_cleanup_jobs", "status", "next_attempt_at", "lease_expires_at")
    assert unique("legacy_media_manual_inventory", "target_type", "target_id", "target_field", "sort_order")
    assert unique("upload_objects", "legacy_import_key")
```

Also assert `ModerationTask` has nullable `provider_job_id`, `provider_submission_started_at`, and `provider_submitted_at`; `Post` has nullable `thumbnail_url`; async media `Message` has `moderation_status`, `moderation_case_id`, and `upload_object_id`; every FK is named; timestamps are UTC; IDs and keys are bounded; no column stores a signed URL or raw Provider response. `MediaCleanupJob` has no `case_id` and every row binds one `upload_object_id`; `LegacyMediaManualInventory` has `sort_order` but no URL/key/body column. `UploadObject.status="legacy_imported"` requires null session/token/quarantine fields, non-null trusted `public_key/public_url` and immutable target provenance; every non-legacy object requires its normal session/token/quarantine fields.

- [ ] **Step 2: Run the new tests and verify RED**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_media_models.py tests/test_media_migration.py -q
```

Expected: FAIL because `app.models.media` and all four media tables are absent.

- [ ] **Step 3: Implement the exact ORM and generate from the live head**

```python
class UploadSession(Base):
    __tablename__ = "upload_sessions"
    __table_args__ = (
        Index("ix_upload_sessions_owner_status_expiry", "user_id", "status", "expires_at"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", name="fk_upload_sessions_user"), nullable=False)
    purpose = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default=UploadSessionStatus.OPEN.value)
    object_count = Column(SmallInteger, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    confirmed_at = Column(DateTime)
    attached_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    version = Column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version}


class UploadObject(Base):
    __tablename__ = "upload_objects"
    __table_args__ = (
        UniqueConstraint("quarantine_key", name="uq_upload_objects_quarantine_key"),
        UniqueConstraint("upload_token_hash", name="uq_upload_objects_token_hash"),
        UniqueConstraint("moderation_task_id", name="uq_upload_objects_task"),
        UniqueConstraint("session_id", "sort_order", name="uq_upload_objects_session_order"),
        UniqueConstraint("legacy_import_key", name="uq_upload_objects_legacy_import_key"),
        Index("ix_upload_objects_status_confirmed", "status", "confirmed_at"),
        Index("ix_upload_objects_case_order", "moderation_case_id", "sort_order"),
    )

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("upload_sessions.id", name="fk_upload_objects_session"))
    user_id = Column(Integer, ForeignKey("users.id", name="fk_upload_objects_user"), nullable=False)
    purpose = Column(String(64), nullable=False)
    kind = Column(String(16), nullable=False)
    status = Column(String(32), nullable=False, default=UploadObjectStatus.ISSUED.value)
    sort_order = Column(SmallInteger, nullable=False)
    original_extension = Column(String(16), nullable=False)
    declared_content_type = Column(String(127))
    declared_size = Column(BigInteger)
    max_bytes = Column(BigInteger)
    quarantine_key = Column(String(1024))
    upload_token_hash = Column(String(64))
    actual_content_type = Column(String(127))
    actual_size = Column(BigInteger)
    etag = Column(String(128))
    cos_version_id = Column(String(255))
    detected_format = Column(String(32))
    width = Column(Integer)
    height = Column(Integer)
    frame_count = Column(Integer)
    duration_seconds = Column(Numeric(12, 3))
    video_codec = Column(String(64))
    audio_codec = Column(String(64))
    moderation_case_id = Column(Integer, ForeignKey("moderation_cases.id", name="fk_upload_objects_case"))
    moderation_task_id = Column(Integer, ForeignKey("moderation_tasks.id", name="fk_upload_objects_task"))
    target_type = Column(String(64))
    target_id = Column(Integer)
    target_field = Column(String(64))
    public_key = Column(String(1024))
    public_url = Column(String(1024))
    legacy_import_key = Column(String(255))
    confirmed_at = Column(DateTime)
    attached_at = Column(DateTime)
    published_at = Column(DateTime)
    rejected_at = Column(DateTime)
    deleted_at = Column(DateTime)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    version = Column(Integer, nullable=False, default=1)

    __mapper_args__ = {"version_id_col": version}


class MediaCleanupJob(Base):
    __tablename__ = "media_cleanup_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_media_cleanup_jobs_idempotency"),
        Index("ix_media_cleanup_due", "status", "next_attempt_at", "lease_expires_at"),
    )
    id = Column(Integer, primary_key=True)
    upload_object_id = Column(Integer, ForeignKey("upload_objects.id", name="fk_media_cleanup_jobs_object"), nullable=False)
    operation = Column(String(32), nullable=False)  # delete_quarantine/delete_public
    reason = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    idempotency_key = Column(String(255), nullable=False)
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=8)
    next_attempt_at = Column(DateTime, nullable=False)
    claim_token = Column(String(64))
    worker_id = Column(String(128))
    claimed_at = Column(DateTime)
    lease_expires_at = Column(DateTime)
    last_error_code = Column(String(128))
    last_error_message = Column(String(500))
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    completed_at = Column(DateTime)


class LegacyMediaManualInventory(Base):
    __tablename__ = "legacy_media_manual_inventory"
    __table_args__ = (
        UniqueConstraint("target_type", "target_id", "target_field", "sort_order", name="uq_legacy_media_manual_target"),
    )
    id = Column(Integer, primary_key=True)
    target_type = Column(String(64), nullable=False)
    target_id = Column(Integer, nullable=False)
    target_field = Column(String(64), nullable=False)
    sort_order = Column(SmallInteger, nullable=False, default=0)
    reason_code = Column(String(128), nullable=False)
    observed_at = Column(DateTime, nullable=False, default=utcnow)
    resolved_at = Column(DateTime)
    # deliberately no URL, host, bucket, key or body
```

Use the phase-2 UTC helper for `utcnow`. Add named migration check constraints for positive session-upload `object_count/declared_size/max_bytes`, non-negative `sort_order`, `actual_size <= max_bytes`, the legacy/non-legacy shape described in Step 1, and positive cleanup attempts. Add indexes exactly as the ORM declares. `public_url` is allowed only after saga publication or safe legacy import and must never contain `?`; no pre-signed URL, original filename or raw Provider response column exists. ORM/repository guards make `legacy_imported` object ownership, target provenance, public key/URL and actual metadata read-only; it cannot attach to a new revision, enter quarantine publication, or be deleted by phase-3 cleanup.

Run immediately before generating:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m alembic heads
```

Expected: one line ending `(head)`. Then:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m alembic revision --rev-id 2026_07_18_0500 -m "add moderated media uploads"
```

Rename to the locked filename if necessary. Set `down_revision` to the single head printed immediately before generation. Add all four tables, indexes, FKs, task provider columns, nullable `posts.thumbnail_url VARCHAR(1024)`, and async-media message columns. Backfill existing `Message.moderation_status` to `approved` before making it non-null. Migration tests upgrade from captured parent, inspect schema, downgrade to parent, and upgrade to head.

- [ ] **Step 4: Run model, migration and head checks**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_media_models.py tests/test_media_migration.py -q
./.venv/Scripts/python.exe -m alembic upgrade head
./.venv/Scripts/python.exe -m alembic heads
```

Expected: tests PASS, upgrade succeeds, and exactly one head is printed.

- [ ] **Step 5: Commit the schema**

```bash
cd /d/NanTuPy
git add app/models/media.py app/services/media_types.py app/models/models.py app/models/__init__.py alembic/env.py alembic/versions/2026_07_18_0500_add_moderated_media_uploads.py tests/conftest.py tests/test_media_models.py tests/test_media_migration.py
git commit -m "feat: add moderated media upload schema"
```

---

### Task 3: Implement the private COS adapter and exact one-shot PUT contract

**Files:**
- Modify: `.env.example`
- Modify: `app/core/config.py`
- Create: `app/services/cos_storage.py`
- Modify: `app/utils.py`
- Create: `tests/test_cos_storage.py`
- Create: `tests/test_media_config.py`

- [ ] **Step 1: Write failing adapter and configuration tests**

Use a recording fake SDK client. Assert generated keys match only `quarantine/{user_id}/{session_id}/{32-lower-hex}.{ext}`; a caller cannot supply a key; PUT expires in exactly 600 seconds and signs these exact headers:

```python
expected_headers = {
    "Content-Type": "image/jpeg",
    "Content-Length": "12345",
    "x-cos-meta-upload-token": upload_token,
    "x-cos-forbid-overwrite": "true",
}
```

Assert `presign_get()` expires in 300 seconds, query strings are never logged, `copy_if_absent()` verifies source ETag and an existing destination's ETag/size, and `download_limited()` aborts at `max_bytes + 1`. Assert production startup reports unconfigured without exposing ID/key/bucket; tests can inject a fake.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_cos_storage.py tests/test_media_config.py -q
```

Expected: FAIL because `CosStorage` and secure media settings do not exist.

- [ ] **Step 3: Implement the adapter and secure settings**

```python
def build_quarantine_key(*, user_id: int, session_id: int,
                         object_name: str, extension: str) -> str:
    assert re.fullmatch(r"[0-9a-f]{32}", object_name)
    assert extension in {"jpg", "png", "webp", "gif", "mp4", "mov"}
    return f"quarantine/{user_id}/{session_id}/{object_name}.{extension}"
```

`TencentCosStorage.presign_put()` calls SDK `get_presigned_url(Method="PUT", Bucket=..., Key=..., Expired=600, Headers=expected_headers)` and returns the same headers for the client to send. The signed metadata token is 32 random bytes encoded URL-safe; persist only HMAC-SHA256 using `UPLOAD_TOKEN_HASH_KEY`. `x-cos-forbid-overwrite:true` is mandatory and the bucket must have versioning disabled for this guard; an operator-run preflight outside automated tests verifies the documented bucket setting and performs two PUTs to a disposable key, requiring the second to fail before enabling production traffic. A HEAD preflight must also see no object before URL issuance.

Add settings `COS_ID`, `COS_KEY`, `COS_REGION`, `COS_BUCKET_NAME`, `COS_DOMAIN`, `UPLOAD_TOKEN_HASH_KEY`, `COS_PUT_EXPIRES_SECONDS=600`, `COS_PROVIDER_GET_EXPIRES_SECONDS=300`; production rejects missing secrets and non-HTTPS `COS_DOMAIN`. `.env.example` instructs generating hash key with `python -c "import secrets; print(secrets.token_urlsafe(48))"` and contains no credential-shaped sample.

Reduce `app/utils.py::FileUploader` to a deprecated wrapper that cannot generate new public-prefix uploads; Tasks 8–11 remove its callers before final deletion.

- [ ] **Step 4: Run adapter and secret-redaction tests**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_cos_storage.py tests/test_media_config.py tests/test_moderation_privacy.py -q
```

Expected: PASS; captured logs contain no signing URL, query, secret, upload token or DSN.

- [ ] **Step 5: Commit the COS boundary**

```bash
cd /d/NanTuPy
git add .env.example app/core/config.py app/services/cos_storage.py app/utils.py tests/test_cos_storage.py tests/test_media_config.py
git commit -m "feat: isolate COS media uploads"
```

---

### Task 4: Create, confirm and freeze typed uploads with real media inspection

**Files:**
- Modify: `app/routers/upload.py`
- Create: `app/services/upload_service.py`
- Create: `app/services/media_inspector.py`
- Modify: `app/main.py:140`
- Create: `tests/test_upload_sessions.py`
- Create: `tests/test_media_inspector.py`
- Create: `tests/fixtures/media_factory.py`

- [ ] **Step 1: Write failing session, ownership and inspection tests**

Cover all fourteen purposes (including `post_video_thumbnail`, separated direct/community chat, private identity evidence and role portfolio); purpose count/size/MIME policy; expired session; another user's session/key; HEAD missing; wrong prefix/token metadata/size/MIME/ETag; confirm retry; confirm after attach; and PUT overwrite. Assert exact response triples and JSON: malformed/unsupported bytes return 422 `{"detail":{"code":"MEDIA_FORMAT_INVALID","message":"媒体格式无效","retryable":false}}`; Pillow/ffprobe timeout, missing binary or inspector failure returns 503 `{"detail":{"code":"MEDIA_INSPECTION_UNAVAILABLE","message":"媒体检测服务暂不可用，请稍后重试","retryable":true}}`; changed second HEAD returns 409 `UPLOAD_OBJECT_CHANGED`/`上传对象已发生变化，请重新上传`/`false`; attempted mutation after confirmation returns 409 `UPLOAD_OBJECT_FROZEN`/`上传对象已冻结，不能修改`/`false`. Generate valid JPEG/PNG/WEBP/GIF and malformed/polyglot/renamed files in memory. Create a 40,000,001-pixel header and assert `Image.DecompressionBombError`; create an animated GIF whose summed frame pixels exceed policy.

Stub `subprocess.run` for ffprobe and assert accepted video has container intersecting `mov,mp4,m4a,3gp,3g2,mj2`, exactly one video stream, codec `h264` or `hevc`, optional audio codec `aac`, positive dimensions, duration within policy and no attached picture-only stream. Unknown/malformed/multiple-video-stream/over-duration output fails closed.

- [ ] **Step 2: Run tests and verify RED**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_upload_sessions.py tests/test_media_inspector.py -q
```

Expected: FAIL because typed upload orchestration and inspectors are missing.

- [ ] **Step 3: Implement exact API, confirmation and freeze semantics**

```text
POST /api/upload/sessions
POST /api/upload/sessions/{session_id}/confirm
GET  /api/upload/sessions/{session_id}
DELETE /api/upload/sessions/{session_id}
```

Create body:

```json
{"purpose":"post_image","items":[{"filename":"a.jpg","content_type":"image/jpeg","size":12345}]}
```

Response contains `id,purpose,status,expires_at,objects[{id,method,url,headers,expires_at}]`; it contains no public URL/key/bucket/secret. Generate DB rows and keys first, then sign. Sessions expire after 15 minutes; PUT URLs after 10 minutes.

Confirmation sequence is exact: lock session/object; validate owner/open/not expired; HEAD generated key; require exact length, declared Content-Type, `x-cos-meta-upload-token` HMAC, non-empty normalized ETag, and no prior confirmation; download to `NamedTemporaryFile` with `max_bytes + 1`; inspect; HEAD again and require same ETag/length; atomically write immutable actual metadata and `CONFIRMED`. A repeated confirmation with identical frozen metadata returns the existing result; changed HEAD returns the locked 409 `UPLOAD_OBJECT_CHANGED` detail. Any update of key, declared/actual metadata or ETag after confirmed raises the locked 409 `UPLOAD_OBJECT_FROZEN` detail through ORM guards and repository conditions. Route exception mapping must use the canonical table rather than reconstructing messages or retryability at call sites.

Image inspection uses `warnings.simplefilter("error", Image.DecompressionBombWarning)`, `Image.open`, `verify()`, reopen/load each frame, `Image.format` mapping `{JPEG:image/jpeg,PNG:image/png,WEBP:image/webp,GIF:image/gif}`, dimensions and frame pixel budget. ffprobe command is:

```python
[
 "ffprobe", "-v", "error", "-print_format", "json",
 "-show_format", "-show_streams", temp_path,
]
```

with a 15-second timeout and capped captured output. Unknown/malformed formats return the locked 422 `MEDIA_FORMAT_INVALID`; inspector dependency/timeouts return the locked retryable 503 `MEDIA_INSPECTION_UNAVAILABLE` and do not confirm. No exception text is copied into `detail.message`.

- [ ] **Step 4: Run upload and inspector suites**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_upload_sessions.py tests/test_media_inspector.py tests/test_cos_storage.py -q
```

Expected: PASS, including true decode, pixel bomb, ffprobe schema and TOCTOU HEAD tests.

- [ ] **Step 5: Commit upload confirmation**

```bash
cd /d/NanTuPy
git add app/routers/upload.py app/services/upload_service.py app/services/media_inspector.py app/main.py tests/test_upload_sessions.py tests/test_media_inspector.py tests/fixtures/media_factory.py
git commit -m "feat: verify and freeze quarantined media"
```

---

### Task 5: Add injectable Tencent TMS/IMS/VM Provider contracts

**Files:**
- Modify: `requirements.txt`
- Modify: `.env.example`
- Modify: `app/core/config.py`
- Modify: `app/dependencies.py`
- Create: `app/services/tencent_moderation_provider.py`
- Create: `tests/test_tencent_moderation_provider.py`
- Create: `tests/fixtures/tencent_moderation_responses.py`

**Depends on:** Tasks 1–4. It imports phase-2 `Severity`, `TaskType`, `NormalizedTaskResult` and `ProviderStillProcessing`; it does not define substitutes.

- [ ] **Step 1: Write failing Provider contract tests**

Inject fake TMS/IMS/VM clients and verify exact SDK methods: `TextModeration(TextModerationRequest)`, `ImageModeration(ImageModerationRequest)`, `CreateVideoModerationTask(CreateVideoModerationTaskRequest)`, `DescribeTaskDetail(DescribeTaskDetailRequest)`. Assert text is UTF-8 then Base64 in `Content`; image/video receive only 300-second COS GET URLs; `DataId` is `case-{case_id}-object-{object_id}`; `BizType` comes from separate env settings.

Map `Pass -> approved`, `Block -> rejected` only when score meets configured block threshold, `Review`, unknown label, missing score, low-confidence Block and video `ERROR/CANCELLED -> manual_review`; video `PENDING/RUNNING -> processing` with bounded `TryInSeconds`. VM has no trusted top-level score: derive `confidence` as the maximum valid score across returned image/audio result segments, and return `manual_review` when a finished non-Pass result has no valid nested score. Normalize labels to phase-1 `RiskCategory` values `sexual,violence,illegal,abuse,hate,spam,privacy,other`, normalize severity to phase-1 `Severity`, and preserve the case integer `rule_version`. Parameterize `normalize_provider_result()` so terminal decisions yield the exact phase-2 `NormalizedTaskResult`, while processing yields `ProviderStillProcessing(provider_job_id,poll_after_seconds)` and never a completion value. Persist/request only request ID, normalized category/severity/score and provider job ID; never keywords, OCR, ASR, segments, raw JSON or temporary URL.

- [ ] **Step 2: Run Provider tests and verify RED**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_tencent_moderation_provider.py -q
```

Expected: FAIL because the Provider adapter/dependency is absent.

- [ ] **Step 3: Implement SDK adapter, classification and secure injection**

Add `tencentcloud-sdk-python==3.0.1400`. Construct SDK clients lazily from `TENCENT_MODERATION_SECRET_ID`, `TENCENT_MODERATION_SECRET_KEY`, `TENCENT_MODERATION_REGION`, endpoints `tms.tencentcloudapi.com`, `ims.tencentcloudapi.com`, `vm.tencentcloudapi.com`, connect timeout 3 seconds and request timeout 10 seconds. Use separate `TENCENT_TMS_BIZ_TYPE`, `TENCENT_IMS_BIZ_TYPE`, `TENCENT_VM_BIZ_TYPE`, `TENCENT_BLOCK_SCORE=90`, and `TENCENT_MODERATION_ENABLED=false` defaults.

Video submit uses `models.TaskInput(DataId=data_id, Name=data_id, Input=models.StorageInfo(Type="URL", Url=temporary_url))`; response `Results[0].TaskId` becomes `provider_job_id`. Poll sets `TaskId` and `ShowAllSegments=False`. Do not configure callbacks.

Define `RetryableProviderError(code,safe_message)` for timeout, `RequestLimitExceeded`, Tencent `InternalError`, 5xx and temporary resource errors; `PermanentProviderError` for auth, invalid parameter, unsupported media and policy errors. Implement the locked `normalize_provider_result()` boundary; every Worker processor calls it immediately after Provider I/O and never passes raw `ProviderResult` to the repository. Messages are sanitized/capped at 200 characters. `get_moderation_provider()` returns injected fake in tests, configured Tencent adapter when enabled, otherwise `ProviderNotConfigured`; health exposes booleans only.

- [ ] **Step 4: Run Provider and privacy tests**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_tencent_moderation_provider.py tests/test_moderation_privacy.py tests/test_moderation_health.py -q
```

Expected: PASS with zero network calls and no temporary URL/body/secret in captured state or logs.

- [ ] **Step 5: Commit Provider boundary**

```bash
cd /d/NanTuPy
git add requirements.txt .env.example app/core/config.py app/dependencies.py app/services/tencent_moderation_provider.py tests/test_tencent_moderation_provider.py tests/fixtures/tencent_moderation_responses.py
git commit -m "feat: add injectable Tencent moderation provider"
```

---

### Task 6: Bind media to phase-2 cases and process text/image/video tasks idempotently

**Files:**
- Create: `app/services/media_moderation_service.py`
- Modify: `app/services/moderation_repository.py`
- Modify: `app/services/moderation_governance_service.py`
- Modify: `app/workers/moderation_worker.py`
- Modify: `app/routers/health.py`
- Create: `tests/test_media_moderation_service.py`
- Create: `tests/test_media_worker.py`
- Create: `tests/test_video_polling.py`

**Depends on:** Tasks 1, 2 and 5. Task 7 consumes its aggregate outcome; Tasks 8–11 depend on Tasks 6–7.

- [ ] **Step 1: Write failing binding, lease, retry and aggregation tests**

Prove: only owner-confirmed purpose-compatible unattached objects bind; one object binds once; case/revision/object/task are created in one transaction; idempotency key is `media:{case_id}:{object_id}:{etag}`; two workers cannot submit the same video; retry after stored `provider_job_id` only polls; crash before Provider submit may retry; crash after `provider_submission_started_at` commit but before storing the returned TaskId becomes `manual_review` and never resubmits; image/text completion checks claim token; video submit schedules `video_poll`; poll resumes after lease expiry; retry uses stage-2 exponential backoff/jitter; max attempts becomes `manual_review`; stale case/version/hash/deleted target never publishes or scores a violation.

Aggregate two images plus one text task: all approved is ready to publish, first rejected rejects case once, any manual result moves case once, processing remains pending, and late results cannot reverse a terminal decision.

- [ ] **Step 2: Run Worker/service tests and verify RED**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_media_moderation_service.py tests/test_media_worker.py tests/test_video_polling.py -q
```

Expected: FAIL because stage-2 Worker has no media processors or object aggregation.

- [ ] **Step 3: Implement minimal extensions without changing the state machine**

```python
def attach_confirmed_objects(
    self, *, db: Session, case_id: int, author_id: int,
    bindings: Sequence[MediaBinding],
) -> list[UploadObject]: ...

def record_object_result(
    self, *, db: Session, task_id: int, claim_token: str,
    result: NormalizedTaskResult, now: datetime,
) -> MediaAggregateOutcome: ...
```

`MediaBinding` contains `upload_object_id,target_type,target_id,target_field,sort_order,expected_purpose`. Lock rows ordered by ID; require `CONFIRMED`, owner, exact purpose and not attached; set `ATTACHED`; create one `TaskType.IMAGE` or `TaskType.VIDEO` task using the existing repository. Public uncertain text continues using the existing phase-2 `TaskType.TEXT` task and TMS only when phase-2 policy requests it. Direct/community text never creates `TaskType.TEXT` and never reaches TMS; their initial image tasks may call IMS and initial video tasks may call VM using the exact existing purpose names `direct_chat_image`, `direct_chat_video`, `community_chat_image`, `community_chat_video`.

Worker processors generate temporary GET immediately before Provider call and immediately call `normalize_provider_result()`. Image calls once. Before VM submission, video processor conditionally persists `provider_submission_started_at` in a new transaction while its claim token is current. If normalization returns `ProviderStillProcessing`, conditionally persist `provider_job_id/provider_submitted_at`, change the existing task to `TaskType.VIDEO_POLL`, set `next_attempt_at=now+clamp(poll_after_seconds or 15,5,300)`, clear claim/lease fields, and commit; do **not** call phase-2 `complete_task()` and do not aggregate. Poll repeats that rule while processing. Only `NormalizedTaskResult` may be passed to `complete_task()`, followed by object aggregation only when `accepted` and not stale. Retry with a stored job only polls; retry with no submission marker may submit; retry with a submission marker but no job ID cannot prove whether Tencent accepted the request, so normalize a terminal `manual_review` result with safe code `VIDEO_SUBMISSION_OUTCOME_UNKNOWN` and never resubmit. Provider retryable errors call phase-2 `retry_task()`; permanent/unknown/attempt exhaustion normalizes to `manual_review`, never approval.

Do not add new case states. A fully approved aggregate emits `MediaAggregateOutcome.READY_TO_PUBLISH`; Task 7 handles publication before calling existing `apply_case_result(APPROVED)`.

- [ ] **Step 4: Run Worker, lease and state regressions**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_media_moderation_service.py tests/test_media_worker.py tests/test_video_polling.py tests/test_moderation_worker.py tests/test_moderation_repository.py tests/test_moderation_state_machine.py -q
```

Expected: PASS; duplicate claims/submits/polls/results create one vendor job, one aggregate transition and no duplicate violation.

- [ ] **Step 5: Commit Worker media processing**

```bash
cd /d/NanTuPy
git add app/services/media_moderation_service.py app/services/moderation_repository.py app/services/moderation_governance_service.py app/workers/moderation_worker.py app/routers/health.py tests/test_media_moderation_service.py tests/test_media_worker.py tests/test_video_polling.py
git commit -m "feat: process media moderation tasks"
```

---

### Task 7: Implement the publication saga and dedicated media cleanup jobs

**Files:**
- Create: `app/services/media_publish_saga.py`
- Modify: `app/services/moderation_governance_service.py`
- Modify: `app/services/moderation_targets.py`
- Modify: `app/services/moderation_effects.py`
- Modify: `app/workers/moderation_worker.py`
- Create: `tests/test_media_publish_saga.py`
- Create: `tests/test_media_cleanup.py`

**Depends on:** Tasks 2, 3 and 6. It must not change the phase-2 `ModerationTask` ownership contract.

- [ ] **Step 1: Write failing crash-point and cleanup tests**

For every crash point—before copy, after one of many copies, after all copies, before DB apply, after DB apply and before quarantine delete—retry and assert one public object and one revision application. Public keys must match `public/{target_type}/{YYYY}/{MM}/{32hex}.{ext}` and be backend-generated. Assert destination mismatch fails closed; stale/deleted/superseded cases compensate copied public objects. Expired unconfirmed objects become eligible immediately after session expiry, confirmed unattached orphans after 24 hours, published quarantine copies only after the business DB commit, and stale public compensation only after the stale decision is durable. Each candidate creates one `MediaCleanupJob` bound to `upload_object_id`; two workers cannot hold its lease, expired leases recover, crash before/after COS delete completes once, not-found is success, and permission/network errors retry safely. Assert no cleanup creates a `ModerationTask`, requires a case, parses a URL, or selects `legacy_imported`. Rejected media remains quarantined and receives no phase-3 retention-delete job; phase 4 owns the unified 30/180/365-day retention operation.

- [ ] **Step 2: Run saga tests and verify RED**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_media_publish_saga.py tests/test_media_cleanup.py -q
```

Expected: FAIL because no publish/copy checkpoint orchestration exists.

- [ ] **Step 3: Implement a re-entrant saga and dedicated cleanup queue**

```python
class MediaPublishSaga:
    def prepare_public_copies(self, *, case_id: int) -> list[PublishedCopy]: ...
    def apply_prepared_copies(self, *, case_id: int,
                              copies: Sequence[PublishedCopy]) -> list[ModerationEffect]: ...
    def compensate(self, *, case_id: int,
                   copies: Sequence[PublishedCopy], reason: str) -> None: ...
```

`prepare_public_copies()` reads immutable object snapshots, `copy_if_absent(source_etag=...)`, HEAD-verifies each destination, and conditionally checkpoints `PUBLISHING/public_key`; it never writes URL with a query. `apply_prepared_copies()` opens one transaction, locks case/revision/target/objects, rechecks phase-2 version/hash/status and every ETag, converts keys with trusted `public_url_for_key()`, injects only target whitelist fields, calls existing `apply_case_result(APPROVED)`, marks objects `PUBLISHED`, and commits. Existing effects run only after commit.

On rejection, mark each object `REJECTED`, preserve quarantine for the future phase-4 retention operation, and call the existing rejection/violation path once. `enqueue_media_cleanup()` inserts `MediaCleanupJob(idempotency_key="media-cleanup:{upload_object_id}:{operation}:{reason}")` with a named operation and no case dependency. The cleanup branch claims due/expired-lease rows by CAS token, commits before COS I/O, deletes only the selected `UploadObject.quarantine_key/cos_version_id` or checkpointed backend-generated `public_key`, and conditionally completes by job ID/token in a new transaction. It never calls `ModerationRepository.create_case_with_revision()`, `complete_task()` or `append_action(case_id=...)`. Object-not-found is success; retry/dead errors store only stable codes and sanitized messages. Never select `legacy_imported`, delete a legacy arbitrary URL, or parse any URL into a key. Phase 3 schedules only lifecycle cleanup (expired, orphan, published-quarantine and compensation); Task 12 imports legacy objects read-only, and phase 4 later unifies retention cleanup through its operations worker while invoking this exact object-delete primitive.

- [ ] **Step 4: Run saga, revisions and side-effect tests**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_media_publish_saga.py tests/test_media_cleanup.py tests/test_content_revisions.py tests/test_moderation_effects.py -q
```

Expected: PASS with exactly-once apply/effects and cleanup recovery; schema/source assertions prove lifecycle cleanup uses only `MediaCleanupJob`, and rejected retention remains untouched for phase 4.

- [ ] **Step 5: Commit media publication**

```bash
cd /d/NanTuPy
git add app/services/media_publish_saga.py app/services/moderation_governance_service.py app/services/moderation_targets.py app/services/moderation_effects.py app/workers/moderation_worker.py tests/test_media_publish_saga.py tests/test_media_cleanup.py
git commit -m "feat: publish moderated media safely"
```

---

### Task 8: Integrate post media and remove multipart/arbitrary-URL post bypasses

**Files:**
- Modify: `app/routers/posts.py:72-200,302-355,358-410`
- Modify: `app/models/models.py:171-274`
- Modify: `app/services/moderation_targets.py`
- Modify: `app/services/post_visibility_service.py`
- Modify: `app/services/recommendation_service.py`
- Modify: `app/services/search_service.py`
- Create: `tests/test_post_media_moderation.py`
- Create: `tests/test_post_media_bypasses.py`

- [ ] **Step 1: Write failing create/edit/read and bypass tests**

Test 1–9 images, one video with one required `post_video_thumbnail`, text+media, media-only, pending create, pending edit retaining old media, approval, rejection, superseded edit and delete race. Reject a video without its typed thumbnail and reject a thumbnail without its paired video. Public feed/search/recommendation/topic/community must not expose pending URLs. Assert another user's object, wrong purpose, unconfirmed ID, reused ID, raw `image_urls`, `video_url`, multipart `image`/`video`, signed URL and COS-looking URL all return exactly HTTP 422 `{"detail":{"code":"UPLOAD_REFERENCE_REQUIRED","message":"请使用已确认的上传对象","retryable":false}}` before mutation. Assert accepted pending creates/edits return HTTP 202 top-level `code="MEDIA_PENDING_REVIEW"`, `message="媒体审核中"`, `retryable=false`, `moderation_status="pending_review"`, `case_id`, and safe `content`, never a failure `detail`.

- [ ] **Step 2: Run post media tests and verify RED**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_post_media_moderation.py tests/test_post_media_bypasses.py -q
```

Expected: FAIL because current route accepts multipart files and arbitrary image/video URLs.

- [ ] **Step 3: Route posts through upload IDs and the existing revision lifecycle**

Keep the existing endpoint URL but replace media input with `image_upload_ids` JSON array, nullable `video_upload_id`, and nullable `video_thumbnail_upload_id`; require video and thumbnail as a pair, reject simultaneous images/video and all legacy media fields. Remove `FileUploader.save_file/delete_file` and direct URL persistence. The revision payload stores object IDs under private keys `_image_upload_ids/_video_upload_id/_video_thumbnail_upload_id`; only `MediaPublishSaga` converts these to `Post.images/video_url/thumbnail_url` during approval.

Approved create/edit keeps the phase-2 envelope. Media-pending create/edit returns HTTP 202 with the locked accepted fields:

```json
{"code":"MEDIA_PENDING_REVIEW","message":"媒体审核中","retryable":false,"moderation_status":"pending_review","case_id":321,"content":{"id":12,"images":[],"video_url":null}}
```

For pending edit, `content` is the old approved post/media. Author detail may include `pending_media_count` but no quarantine key/URL. Delete schedules exact upload-object cleanup, not URL parsing. Every public predicate requires approved phase-2 status and non-hidden state.

- [ ] **Step 4: Run post and feed-defense regressions**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_post_media_moderation.py tests/test_post_media_bypasses.py tests/test_post_moderation_governance.py tests/test_post_visibility_contracts.py -q
```

Expected: PASS and no route-facing `FileUploader` call remains in `posts.py`.

- [ ] **Step 5: Commit post media integration**

```bash
cd /d/NanTuPy
git add app/routers/posts.py app/models/models.py app/services/moderation_targets.py app/services/post_visibility_service.py app/services/recommendation_service.py app/services/search_service.py tests/test_post_media_moderation.py tests/test_post_media_bypasses.py
git commit -m "feat: moderate post media before publication"
```

---

### Task 9: Integrate profile, community and comic-event media

**Files:**
- Modify: `app/routers/auth.py:33-62,162-166,442-464`
- Modify: `app/serializers/user.py`
- Modify: `app/routers/upload.py:136-181`
- Modify: `app/routers/communities.py:116-182`
- Modify: `app/services/community_service.py:110-197`
- Modify: `app/models/community.py:22-91`
- Modify: `app/routers/comic.py:347-484`
- Modify: `app/models/models.py:786-830`
- Modify: `app/services/moderation_targets.py`
- Create: `tests/test_profile_media_moderation.py`
- Create: `tests/test_community_media_moderation.py`
- Create: `tests/test_comic_media_moderation.py`

- [ ] **Step 1: Write failing target lifecycle and old-version tests**

For avatar, user cover, community avatar/banner and 1–9 comic images, assert correct purpose/owner, pending response, old public image retention, approve swap, reject retention, supersession cleanup, stale result defense and public-list exclusion for never-published pending creates. Assert direct `avatar_url`, `cover_photo_url`, `banner_url`, `imageUrls`, `image_urls`, `/upload/avatar/confirm` and `/upload/cover/confirm` cannot mutate a business row.

- [ ] **Step 2: Run target media tests and verify RED**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_profile_media_moderation.py tests/test_community_media_moderation.py tests/test_comic_media_moderation.py -q
```

Expected: FAIL because current routes persist client URLs immediately.

- [ ] **Step 3: Apply exact upload-ID fields through phase-2 target adapters**

Profile accepts `avatar_upload_id`/`cover_upload_id`; community accepts `avatar_upload_id`/`banner_upload_id`; comic accepts `image_upload_ids`. Mixed text+media creates one revision/case whose required task set includes TMS only when phase-2 policy requested it and one media task per object. Existing approved fields stay visible during edit review.

Remove `_validate_avatar_url()` as a write authorization mechanism; trusted serializer still permits the DiceBear registration default and saga-produced public COS URLs. Delete dedicated avatar/cover confirm mutation behavior. Comic image rows are inserted/replaced only in `ComicEventTargetAdapter.apply_revision()` after saga publication, preserving sort order and cover index 0. Community welcome message/conversation creation and comic tags/follow-visible effects run once after approval through existing `ModerationEffects`.

- [ ] **Step 4: Run target and existing feature regressions**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_profile_media_moderation.py tests/test_community_media_moderation.py tests/test_comic_media_moderation.py tests/test_governed_write_paths.py tests/test_community_service_sql.py -q
```

Expected: PASS; old approved media remains public while replacement is pending.

- [ ] **Step 5: Commit profile/community/comic media integration**

```bash
cd /d/NanTuPy
git add app/routers/auth.py app/serializers/user.py app/routers/upload.py app/routers/communities.py app/services/community_service.py app/models/community.py app/routers/comic.py app/models/models.py app/services/moderation_targets.py tests/test_profile_media_moderation.py tests/test_community_media_moderation.py tests/test_comic_media_moderation.py
git commit -m "feat: moderate profile community and comic media"
```

---

### Task 10: Integrate private/community chat media without leaking or premature fanout

**Files:**
- Modify: `app/services/message_type_service.py:89-150`
- Modify: `app/routers/chat.py:614-672`
- Modify: `app/routers/ws.py:442-520`
- Modify: `app/routers/communities.py:590-680`
- Modify: `app/services/moderation_targets.py`
- Modify: `app/services/moderation_effects.py`
- Create: `tests/test_chat_media_moderation.py`
- Create: `tests/test_community_chat_media_moderation.py`

- [ ] **Step 1: Write failing HTTP/WS/community media tests**

Assert image/video messages require one `media_upload_id` whose purpose is exactly `direct_chat_image`/`direct_chat_video` for private chat or `community_chat_image`/`community_chat_video` for community chat; do not rename these values. URL in `content`, `media_url` or `file_url` fails with the locked 422 `UPLOAD_REFERENCE_REQUIRED` detail. Pending media creates an author-owned pending `Message` only, with no recipient list result, sequence success log, `last_message_at`, preview, sound event, fanout, notification or push. Approval publishes/fanouts once with final URL; rejection never delivers and preserves only phase-2 case metadata. Direct/community text still never calls TMS, IMS or VM; an initial image media task may call IMS and an initial video media task may call VM.

For WebSocket media send, assert deep equality with the locked pending ACK: `type`, `request_id`, identical original `client_msg_id` and `clientMsgId`, numeric `status=202`, `code=MEDIA_PENDING_REVIEW`, `msg=message=媒体审核中`, `case_id`, server pending `message_id`, and `moderation_status=pending_review`. It is an accepted ACK, not a failed ACK. Replayed client ID returns the same case/message IDs and never attaches a second object.

- [ ] **Step 2: Run chat media tests and verify RED**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_chat_media_moderation.py tests/test_community_chat_media_moderation.py -q
```

Expected: FAIL because existing normalizer accepts arbitrary media URL and sends immediately.

- [ ] **Step 3: Stage async media messages and defer effects**

`normalize_user_message_payload()` accepts `media_upload_id` for image/video and rejects URL fallback. Text/post-card behavior remains phase 2. Create pending Message with `moderation_status="pending_review"`, `upload_object_id`, null `media_url`, safe placeholder content `[图片审核中]`/`[视频审核中]`, and no public side effect. Bind object/case with target `message`.

On saga approval, `MessageTargetAdapter` writes final `media_url`, approved status and original safe media type, then `ModerationEffects` atomically updates conversation timestamp/preview and emits fanout/push exactly once. On reject, mark/delete pending business row after retention policy while preserving case/action/hash; never retain a temporary GET URL. HTTP returns the locked 202 accepted envelope (without WS-only `type/request_id/client_msg_id/clientMsgId/msg`), and WS returns the exact ACK JSON above. `WSAckDedup` stores the durable pending `message_id` only after the pending row/object binding commits; replay returns that pending ID without fanout.

- [ ] **Step 4: Run chat privacy and reliability regressions**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_chat_media_moderation.py tests/test_community_chat_media_moderation.py tests/test_chat_moderation_side_effects.py tests/test_chat_message_type_contracts.py tests/test_community_chat_contracts.py -q
```

Expected: PASS; spies prove no third-party text call and no preapproval delivery.

- [ ] **Step 5: Commit chat media integration**

```bash
cd /d/NanTuPy
git add app/services/message_type_service.py app/routers/chat.py app/routers/ws.py app/routers/communities.py app/services/moderation_targets.py app/services/moderation_effects.py tests/test_chat_media_moderation.py tests/test_community_chat_media_moderation.py
git commit -m "feat: moderate chat media before delivery"
```

---

### Task 11: Close every legacy upload and arbitrary-URL bypass

**Files:**
- Modify: `app/routers/upload.py`
- Modify: `app/utils.py`
- Modify: `app/main.py:140`
- Modify: `app/routers/roles.py:34-77,137-203,370-444`
- Modify: `app/services/moderation_targets.py`
- Create: `tests/test_upload_bypass_matrix.py`
- Create: `tests/test_no_legacy_media_writes.py`
- Create: `tests/test_role_media_moderation.py`

- [ ] **Step 1: Write a failing route and source-boundary matrix**

Enumerate `/api/upload/presign`, `/confirm`, `/avatar/confirm`, `/cover/confirm`, `/delete`, `/multiple`, `/info`, `/uploads/*`, multipart post upload, profile URL fields, community URL fields, comic URLs, chat URLs, and role `proof_images/portfolio_images`. Assert no endpoint returns a public URL before approval, deletes by client URL, accepts `general`, or accepts arbitrary media URL. Identity evidence uses `identity_evidence_image`: private retention, no public saga, administrator-authorized temporary GET. Coser/photographer/service portfolio images use `role_portfolio_image`, the normal IMS and publication saga, while unrelated `portfolio_links` remain bounded HTTPS links.

- [ ] **Step 2: Run bypass tests and verify RED**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_upload_bypass_matrix.py tests/test_no_legacy_media_writes.py tests/test_role_media_moderation.py -q
```

Expected: FAIL on old endpoints and raw role URLs.

- [ ] **Step 3: Remove old routes and finish typed purpose coverage**

Delete legacy methods, redirects and `FileUploader`. Keep only Task 4 session endpoints. Use canonical `IDENTITY_EVIDENCE_IMAGE` policy `(IMAGE,10 MiB,9,{jpeg,png,webp},24M pixels,24M frame pixels)` and `ROLE_PORTFOLIO_IMAGE` policy `(IMAGE,12 MiB,12,{jpeg,png,webp},32M pixels,32M frame pixels)`. Role application accepts `proof_upload_ids`, attaches evidence privately, and serializes no proof URL to ordinary users; admin retrieval signs a 300-second URL on demand after access audit. Role profile update accepts `portfolio_upload_ids`, creates a normal phase-2 revision/case, preserves the old portfolio while pending, and lets the saga write ordered public URLs only after IMS approval.

Add an AST-based test that fails if route body fields include media keys ending `_url/_urls` except response serializers and trusted saga code, or if `FileUploader`, `generate_presigned_urls`, `save_file`, `confirm_upload` is imported by a route.

- [ ] **Step 4: Run bypass and route regressions**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_upload_bypass_matrix.py tests/test_no_legacy_media_writes.py tests/test_role_media_moderation.py tests/test_identity_role_contracts.py -q
```

Expected: PASS; all client media writes are upload IDs and no legacy upload route remains.

- [ ] **Step 5: Commit bypass closure**

```bash
cd /d/NanTuPy
git add app/routers/upload.py app/utils.py app/main.py app/routers/roles.py app/services/media_types.py app/services/moderation_targets.py tests/test_upload_bypass_matrix.py tests/test_no_legacy_media_writes.py tests/test_role_media_moderation.py
git commit -m "security: close legacy media upload bypasses"
```

---

### Task 12: Safely inventory and import trusted pre-phase-3 business media

**Files:**
- Modify: `.env.example`
- Modify: `app/core/config.py`
- Create: `app/services/legacy_media_import.py`
- Create: `app/cli/import_legacy_media.py`
- Create: `tests/test_legacy_media_import.py`
- Create: `tests/test_legacy_media_import_cli.py`

**Depends on:** Tasks 2–3 for the immutable legacy schema/COS boundary and Tasks 8–11 for complete post/profile/community/comic target coverage and closed URL write bypasses. Run it after the phase-3 deployment migration but before enabling new media writes. Phase 4 backfill depends on this import/inventory being complete.

- [ ] **Step 1: Write failing allowlist, mapping, idempotency and privacy tests**

Seed only pre-phase-3 business media fields: post images/video/thumbnail, user avatar/cover, community avatar/banner and comic-event ordered images. Parameterize exact mappings to existing purposes `post_image`, `post_video`, `post_video_thumbnail`, `user_avatar`, `user_cover`, `community_avatar`, `community_banner`, and `comic_event_image`; owner and sort order come from the trusted business row, never the URL. A safely mapped URL must be HTTPS, have no user-info/port/query/fragment, use one exact configured domain, resolve to the exact configured bucket, have a normalized non-empty key under one configured prefix, contain no backslash/dot segment/double decoding, and pass COS HEAD for that exact bucket/key. The target row must still hold the same URL when the import transaction locks it.

Assert a valid source creates one immutable `UploadObject(status="legacy_imported",session_id=None,quarantine_key=None,upload_token_hash=None,target_type,target_id,target_field,purpose,kind,public_key,public_url,actual_size,actual_content_type,etag,cos_version_id)` and re-running returns that row. Assert mismatched bucket/domain, unconfigured prefix, HTTP, port, query, fragment, encoded slash/backslash/dot segment, malformed host, missing object, changed business row and unsupported target field create no object. Every unsafe/unmappable non-empty field creates exactly one `LegacyMediaManualInventory(target_type,target_id,target_field,sort_order,reason_code)` whose schema, logs and CLI output contain no URL, host, bucket or key. Spies prove import never calls COS copy/delete/presign and no parser is exported to routes or cleanup.

- [ ] **Step 2: Run legacy import tests and verify RED**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_legacy_media_import.py tests/test_legacy_media_import_cli.py -q
```

Expected: FAIL because the bounded legacy importer and CLI do not exist.

- [ ] **Step 3: Implement a read-only, allowlisted importer and manual inventory**

```python
@dataclass(frozen=True)
class LegacyMediaSource:
    target_type: Literal["post", "user_profile", "community", "comic_event"]
    target_id: int
    target_field: str
    owner_id: int
    purpose: UploadPurpose
    kind: MediaKind
    sort_order: int
    stored_url: str

@dataclass(frozen=True)
class TrustedLegacyObject:
    bucket: str
    domain: str
    key: str


def resolve_trusted_legacy_object(
    source: LegacyMediaSource, *, policy: LegacyImportPolicy,
) -> TrustedLegacyObject | None: ...
```

Add required staging/production settings `LEGACY_MEDIA_IMPORT_BUCKET`, comma-separated `LEGACY_MEDIA_IMPORT_DOMAINS`, and comma-separated `LEGACY_MEDIA_IMPORT_KEY_PREFIXES`; startup/import rejects empty values, wildcard domains, IP literals, non-HTTPS domain configuration, overlapping/empty prefixes, or a bucket different from the configured COS bucket. Parse with `urllib.parse.urlsplit`, IDNA-normalize and exact-compare the host, reject credentials/port/query/fragment and any percent-encoded separator/control/dot segment, decode once, then require `key == prefix` or `key.startswith(prefix + "/")`. Resolve bucket only from the matched configured domain-to-bucket policy, never from URL text or a caller parameter.

`LegacyMediaImporter.scan_page(after_id,limit)` uses fixed adapter functions for the four target types and keyset pagination; no model/column comes from CLI input. For a trusted mapping, perform HEAD outside the transaction, then lock/recheck the source field and insert the immutable `legacy_imported` object with `legacy_import_key=HMAC-SHA256(server log key, canonical target_type/target_id/target_field/sort_order)` as its bounded uniqueness value; the HMAC input contains no URL. For any unsafe, missing, changed or unsupported source, insert only a stable reason code (`UNTRUSTED_DOMAIN`, `UNTRUSTED_BUCKET`, `UNTRUSTED_KEY`, `MALFORMED_REFERENCE`, `OBJECT_NOT_FOUND`, `SOURCE_CHANGED`, `UNSUPPORTED_FIELD`) into manual inventory. Do not rewrite the business URL, fetch bytes, copy, delete, presign, moderate, create a case/task, or infer arbitrary URLs for future cleanup.

The operator CLI is exact:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m app.cli.import_legacy_media --target all --batch-size 200 --dry-run
./.venv/Scripts/python.exe -m app.cli.import_legacy_media --target all --batch-size 200 --apply
```

`--target` accepts only `all,post,user_profile,community,comic_event`; output contains aggregate scanned/imported/manual/skipped counts and reason-code counts only. It never prints source values, domains, buckets, keys, URLs, object metadata, credentials or exception text. `--dry-run` writes nothing; `--apply` is idempotent and commits one bounded page at a time.

- [ ] **Step 4: Run importer, bypass, cleanup and privacy regressions**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest   tests/test_legacy_media_import.py tests/test_legacy_media_import_cli.py   tests/test_upload_bypass_matrix.py tests/test_no_legacy_media_writes.py   tests/test_media_cleanup.py tests/test_media_privacy.py -q
```

Expected: PASS; valid trusted rows map once, unsafe rows produce URL-free manual inventory, and neither cleanup nor bypass code can delete/copy/accept an arbitrary legacy URL.

- [ ] **Step 5: Commit the bounded legacy import**

```bash
cd /d/NanTuPy
git add .env.example app/core/config.py app/services/legacy_media_import.py app/cli/import_legacy_media.py tests/test_legacy_media_import.py tests/test_legacy_media_import_cli.py
git commit -m "feat: inventory trusted legacy media safely"
```

---

### Task 13: Add Flutter UploadSession models and an injectable exact PUT transport

**Files:**
- Create: `lib/models/upload_session.dart`
- Create: `lib/services/upload_transport.dart`
- Modify: `lib/services/api/upload_service.dart`
- Modify: `lib/services/api/api_client.dart:409-716`
- Modify: `lib/services/api/services.dart`
- Modify: `lib/services/api/role_service.dart`
- Modify: `lib/screens/profile/identity_application_screen.dart:115-156`
- Create: `test/upload_session_model_test.dart`
- Create: `test/upload_transport_test.dart`
- Create: `test/upload_service_contract_test.dart`
- Create: `test/identity_evidence_upload_test.dart`

- [ ] **Step 1: Write failing JSON, header and failure tests**

Assert all typed purposes parse; unknown purpose/status fails closed; 64-bit IDs stay integers; exact returned headers are forwarded unchanged; PUT uses an isolated Dio without API base URL/interceptors/JWT/query mutation; upload then confirm order; cancellation/progress; retry creates a new session only when PUT URL expired and never retries a confirmed object. Parameterize all five HTTP failures and assert `ApiResponse.statusCode/errorCode/message/isRetryable` matches the locked status/code/message/retryable tuple exactly, including non-retryable 409 frozen/changed. Assert HTTP 202 `MEDIA_PENDING_REVIEW` parses as a successful accepted moderation response—not an error/failure—with `caseId`, `moderationStatus.pendingReview`, safe content and `isRetryable == false`. Assert models contain no public URL/key. Assert identity application uploads with `identityEvidenceImage`, submits `proof_upload_ids`, retains external `portfolio_links` as link-only data, and never extracts a proof-image URL.

- [ ] **Step 2: Run Flutter contract tests and verify RED**

```bash
cd /d/FlutterProject/nonto
flutter test test/upload_session_model_test.dart test/upload_transport_test.dart test/upload_service_contract_test.dart test/identity_evidence_upload_test.dart
```

Expected: FAIL because typed models and transport are missing.

- [ ] **Step 3: Implement immutable models and transport**

```dart
enum UploadPurpose {
  postImage, postVideo, postVideoThumbnail, userAvatar, userCover,
  communityAvatar, communityBanner, comicEventImage,
  directChatImage, directChatVideo,
  communityChatImage, communityChatVideo,
  identityEvidenceImage, rolePortfolioImage,
}

class UploadObject {
  final int id;
  final String method;
  final Uri putUrl;
  final Map<String, String> headers;
  final DateTime expiresAt;
}

abstract interface class UploadTransport {
  Future<void> put({required Uri url, required List<int> bytes,
    required Map<String, String> headers,
    CancelToken? cancelToken, ProgressCallback? onProgress});
}
```

`DioUploadTransport` creates a dedicated `Dio(BaseOptions(followRedirects:false))`, uses `Options(method:'PUT',headers:headers,validateStatus: 200/204)`, and never accesses `ApiClient.token`. `UploadService.upload()` validates local byte length equals declared size, POSTs `/upload/sessions`, PUTs each object, POSTs `/confirm`, and returns `ConfirmedUpload(objectId,purpose,status)`. Delete all old presign/public URL/confirm fallback and path-derived upload type logic from `ApiClient`. Change `IdentityApplicationScreen._uploadProofImages()` to return IDs from `UploadPurpose.identityEvidenceImage`; `RoleService.applyIdentity()` accepts `List<int> proofUploadIds` and sends `proof_upload_ids`, while `portfolio_links` remains a bounded HTTPS link list and is not treated as uploaded media.

- [ ] **Step 4: Run tests and analyzer**

```bash
cd /d/FlutterProject/nonto
flutter test test/upload_session_model_test.dart test/upload_transport_test.dart test/upload_service_contract_test.dart test/identity_evidence_upload_test.dart
flutter analyze lib/models/upload_session.dart lib/services/upload_transport.dart lib/services/api/upload_service.dart lib/services/api/api_client.dart lib/services/api/role_service.dart lib/screens/profile/identity_application_screen.dart
```

Expected: PASS and analyzer reports no issues.

- [ ] **Step 5: Commit Flutter upload contracts**

```bash
cd /d/FlutterProject/nonto
git add lib/models/upload_session.dart lib/services/upload_transport.dart lib/services/api/upload_service.dart lib/services/api/api_client.dart lib/services/api/services.dart lib/services/api/role_service.dart lib/screens/profile/identity_application_screen.dart test/upload_session_model_test.dart test/upload_transport_test.dart test/upload_service_contract_test.dart test/identity_evidence_upload_test.dart
git commit -m "feat: add typed quarantined upload transport"
```

---

### Task 14: Persist and restore Flutter pending moderation state defensively

**Files:**
- Modify: `lib/models/moderation.dart`
- Modify: `lib/services/database/app_database.dart`
- Modify: `lib/services/database/app_database.g.dart`
- Create: `lib/services/pending_moderation_store.dart`
- Modify: `lib/providers/feed_notifier.dart`
- Create: `test/pending_moderation_store_test.dart`
- Create: `test/media_feed_defense_test.dart`
- Create: `test/local_db_media_migration_test.dart`

**Depends on:** Phase 1's already-published Drift v2 migration and Task 13 models. Phase 2 must not change Drift schema/version; if its branch contains a Drift upgrade, stop and remove that phase-boundary violation before this task.

- [ ] **Step 1: Write failing migration, recovery and feed-defense tests**

Build three real migration fixtures and round-trip pending records for `post,user_profile,community,comic_event,message` with `caseId,targetId,moderationStatus,createdAt` and no signed URL/key/body: a fresh database creates v3 directly; an exact v1 schema upgrades through the immutable phase-1 `if (from < 2)` branch and then v3; an exact v2 schema upgrades through only v3. Inspect `PRAGMA table_info(messages_table)` after every path and assert `failure_code` and `failure_message` each occur exactly once, all seeded v1/v2 message rows survive, `schemaVersion == 3`, and `pending_moderated_submissions` exists once. On app restart, refresh status through phase-2 `/api/moderation/me/cases/{id}`; approved removes pending record and refreshes target; rejected removes preview and shows standardized message; unknown/failed remains pending. Assert any Post with non-approved or missing-trust media state is refused by feed insertion, websocket merge and cached feed hydration.

- [ ] **Step 2: Run persistence tests and verify RED**

```bash
cd /d/FlutterProject/nonto
flutter test test/pending_moderation_store_test.dart test/media_feed_defense_test.dart test/local_db_media_migration_test.dart
```

Expected: FAIL because pending media submissions are not persisted/restored.

- [ ] **Step 3: Add one canonical pending registry and reuse phase-2 moderation types**

Set Drift `schemaVersion` from phase-1 v2 to v3 and add only `pending_moderated_submissions(case_id UNIQUE,target_type,target_id,status,created_at,updated_at)`. Preserve the published phase-1 branch byte-for-byte:

```dart
onUpgrade: (Migrator m, int from, int to) async {
  if (from < 2) {
    await m.addColumn(messagesTable, messagesTable.failureCode);
    await m.addColumn(messagesTable, messagesTable.failureMessage);
  }
  if (from < 3) {
    await m.createTable(pendingModeratedSubmissions);
  }
  await _writeMeta(metaKeySchemaVersion, to.toString());
}
```

Do not add either failure column in the v3 branch, recreate `messages_table`, or edit a phase-2 migration—phase 2 has no Drift upgrade. Fresh `createAll()` creates the v3 table set directly. Do not store local media bytes, filenames, PUT URLs, public URLs or rejected text. Regenerate `app_database.g.dart` with:

```bash
cd /d/FlutterProject/nonto
dart run build_runner build --delete-conflicting-outputs
```

`PendingModerationStore.record()`, `restoreAll()`, `resolveCase()` and `remove()` use phase-2 `ModerationStatus`; the existing phase-2 notifier `ModerationState` receives restored case summaries rather than introducing a second state class. `FeedNotifier.insertNewPost`, refresh merge and cache hydration require `ModerationStatus.approved`; legacy payloads default approved only when received from authenticated normal feed endpoints, never for local pending records.

- [ ] **Step 4: Run recovery, model and analyzer tests**

```bash
cd /d/FlutterProject/nonto
flutter test test/pending_moderation_store_test.dart test/media_feed_defense_test.dart test/local_db_media_migration_test.dart test/moderation_models_test.dart
flutter analyze lib/models/moderation.dart lib/services/database/app_database.dart lib/services/pending_moderation_store.dart lib/providers/feed_notifier.dart
```

Expected: PASS; fresh v3, v1→v2→v3 and v2→v3 all preserve rows, contain one copy of each failure column, and a restart cannot promote a pending item into feed.

- [ ] **Step 5: Commit pending-state persistence**

```bash
cd /d/FlutterProject/nonto
git add lib/models/moderation.dart lib/services/database/app_database.dart lib/services/database/app_database.g.dart lib/services/pending_moderation_store.dart lib/providers/feed_notifier.dart test/pending_moderation_store_test.dart test/media_feed_defense_test.dart test/local_db_media_migration_test.dart
git commit -m "feat: restore pending media moderation state"
```

---

### Task 15: Switch Flutter post and profile media flows to upload object IDs

**Files:**
- Modify: `lib/services/api/post_service.dart`
- Modify: `lib/services/api/auth_service.dart`
- Modify: `lib/screens/post/create_post_screen.dart:325-579`
- Modify: `lib/screens/profile/edit_profile_screen.dart:77-271`
- Modify: `lib/models/post.dart`
- Modify: `lib/models/user.dart`
- Modify: `lib/widgets/post_card.dart`
- Create: `test/post_media_upload_flow_test.dart`
- Create: `test/profile_media_moderation_flow_test.dart`

- [ ] **Step 1: Write failing orchestration and pending-edit tests**

Mock typed UploadService. Assert post images/video upload sequentially with correct purpose, business request sends IDs and no URL, pending create stores case but does not notify feed, pending edit keeps old media, reject restores draft, and app restart resolves case. Profile keeps approved avatar/cover while local preview shows “审核中”; approval refreshes authenticated user/cache; rejection restores old image. No widget may display a PUT/quarantine URL.

- [ ] **Step 2: Run post/profile tests and verify RED**

```bash
cd /d/FlutterProject/nonto
flutter test test/post_media_upload_flow_test.dart test/profile_media_moderation_flow_test.dart
```

Expected: FAIL because current code extracts and submits public URLs.

- [ ] **Step 3: Submit confirmed object IDs and consume phase-2 envelopes**

`PostService.createPost/updatePost` accept `List<int>? imageUploadIds,int? videoUploadId,int? videoThumbnailUploadId` and require both video IDs together; `AuthService.updateProfile` accepts `avatarUploadId/coverUploadId`. Remove `_extractUrl`, URL optimistic Post creation and URL preservation in profile text saves. Upload progress comes from typed service.

On `pendingReview`, record `caseId` in `PendingModerationStore`, keep draft metadata needed to reopen composer only in existing local draft keys, show standardized message and navigate safely. `PostCard` shows author-only “媒体审核中”; profile shows local in-memory preview until screen closes, then old approved network image plus pending badge. On approved response, use only server-returned public media projection.

- [ ] **Step 4: Run flow and feed regressions**

```bash
cd /d/FlutterProject/nonto
flutter test test/post_media_upload_flow_test.dart test/profile_media_moderation_flow_test.dart test/post_moderation_flow_test.dart test/media_feed_defense_test.dart test/nonto_create_post_phase5a_regression_test.dart test/nonto_edit_profile_phase4d_regression_test.dart
flutter analyze lib/services/api/post_service.dart lib/services/api/auth_service.dart lib/screens/post/create_post_screen.dart lib/screens/profile/edit_profile_screen.dart lib/models/post.dart lib/models/user.dart lib/widgets/post_card.dart
```

Expected: PASS and analyzer reports no issues.

- [ ] **Step 5: Commit post/profile Flutter integration**

```bash
cd /d/FlutterProject/nonto
git add lib/services/api/post_service.dart lib/services/api/auth_service.dart lib/screens/post/create_post_screen.dart lib/screens/profile/edit_profile_screen.dart lib/models/post.dart lib/models/user.dart lib/widgets/post_card.dart test/post_media_upload_flow_test.dart test/profile_media_moderation_flow_test.dart
git commit -m "feat: submit post and profile media for review"
```

---

### Task 16: Switch Flutter community and comic-event flows and restore pending cases

**Files:**
- Modify: `lib/services/api/community_service.dart`
- Modify: `lib/screens/community/community_create_screen.dart`
- Modify: `lib/screens/community/community_manage_screen.dart`
- Modify: `lib/screens/community/community_detail_screen.dart`
- Modify: `lib/models/community.dart`
- Modify: `lib/services/comic_service.dart`
- Modify: `lib/screens/comic/comic_upload_page.dart`
- Modify: `lib/screens/comic/comic_my_events_page.dart`
- Modify: `lib/models/comic_event.dart`
- Create: `test/community_media_moderation_flow_test.dart`
- Create: `test/comic_media_moderation_flow_test.dart`

- [ ] **Step 1: Write failing create/edit/restart tests**

Community avatar/banner and comic 1–9 images must use typed purposes and submit IDs only. Test pending create does not enter public list/detail, pending edit shows old approved media plus manager/owner badge, approval refreshes, rejection restores, reorder survives IDs, and restart restores each case through `PendingModerationStore`. Assert raw URL fields never appear in request maps.

- [ ] **Step 2: Run community/comic tests and verify RED**

```bash
cd /d/FlutterProject/nonto
flutter test test/community_media_moderation_flow_test.dart test/comic_media_moderation_flow_test.dart
```

Expected: FAIL because current screens submit uploaded URLs.

- [ ] **Step 3: Replace URL flows with upload IDs and status-aware UI**

Community create/update bodies use `avatar_upload_id/banner_upload_id`; comic bodies use ordered `image_upload_ids`. Services parse `ModeratedContentResponse<Community/ComicEvent>`. Pending create records case and routes to an author-only “审核中” card rather than public detail; pending edit retains existing model. `Community` and `ComicEvent` parse phase-2 `moderation_status/case_id` and default legacy server list entries to approved. Remove direct `/upload/presign` and `ApiClient.dio.put` from comic page.

- [ ] **Step 4: Run affected feature regressions and analyzer**

```bash
cd /d/FlutterProject/nonto
flutter test test/community_media_moderation_flow_test.dart test/comic_media_moderation_flow_test.dart test/nonto_community_phase6b_regression_test.dart test/nonto_community_phase7_regression_test.dart
flutter analyze lib/services/api/community_service.dart lib/screens/community/community_create_screen.dart lib/screens/community/community_manage_screen.dart lib/screens/community/community_detail_screen.dart lib/models/community.dart lib/services/comic_service.dart lib/screens/comic/comic_upload_page.dart lib/screens/comic/comic_my_events_page.dart lib/models/comic_event.dart
```

Expected: PASS; pending items are restored but never injected into public lists.

- [ ] **Step 5: Commit community/comic Flutter integration**

```bash
cd /d/FlutterProject/nonto
git add lib/services/api/community_service.dart lib/screens/community/community_create_screen.dart lib/screens/community/community_manage_screen.dart lib/screens/community/community_detail_screen.dart lib/models/community.dart lib/services/comic_service.dart lib/screens/comic/comic_upload_page.dart lib/screens/comic/comic_my_events_page.dart lib/models/comic_event.dart test/community_media_moderation_flow_test.dart test/comic_media_moderation_flow_test.dart
git commit -m "feat: show community and comic media review states"
```

---

### Task 17: Switch Flutter private/community chat media to pending delivery

**Files:**
- Modify: `lib/models/message.dart`
- Modify: `lib/services/websocket_service.dart`
- Modify: `lib/services/chat_send_queue.dart`
- Modify: `lib/providers/chat_notifiers.dart`
- Modify: `lib/services/api/community_service.dart`
- Modify: `lib/screens/chat/chat_room_screen.dart`
- Modify: `lib/screens/community/community_chat_screen.dart`
- Create: `test/chat_media_pending_test.dart`
- Create: `test/community_chat_media_pending_test.dart`

- [ ] **Step 1: Write failing upload/ACK/restart/delivery tests**

Assert image/video is uploaded with the exact existing direct/community chat purpose, send payload contains `media_upload_id` and no URL, and the full locked 202/WS `MEDIA_PENDING_REVIEW` JSON settles the reliable send attempt as pending (not failed/sent). Matching uses `client_msg_id`/`clientMsgId`; after matching, the local row/outbox stores the server pending `message_id` and uses it for later approved-event dedupe. Pending message has local preview and badge but does not alter conversation preview, restart restores case and server pending ID, approved server event replaces local preview with public URL once, rejected removes/marks undelivered and cannot retry the same confirmed object. Text rejection/retry semantics from phase 2 remain unchanged.

- [ ] **Step 2: Run chat tests and verify RED**

```bash
cd /d/FlutterProject/nonto
flutter test test/chat_media_pending_test.dart test/community_chat_media_pending_test.dart
```

Expected: FAIL because current chat sends URL immediately and treats only success/failure.

- [ ] **Step 3: Add pending media settlement without weakening reliable text chat**

Add existing Message status value `pendingReview` and nullable `caseId/uploadObjectId`; do not persist PUT URL. Queue recognizes only the full locked ACK `status=202/code=MEDIA_PENDING_REVIEW`, locates exactly one current/early/waiting entry by `client_msg_id` or `clientMsgId`, cancels its ACK timer, removes transport retry eligibility, replaces/persists its ID with `message_id`, records `case_id` and pending moderation, and waits for server-approved message event or case refresh. It never calls `onFailedAck`, never writes `failureCode`, and never locates a local item by `message_id` before client-ID settlement. Local preview uses selected bytes only while process lives; after restart show type icon and “媒体审核中”. Rejection uses standardized moderation message and no resend button; a user may select/upload a new file as a new session.

Community HTTP sender follows the same accepted-pending state and saves the server pending `message_id`. Approved fanout dedupes by saved server message ID plus client message ID and updates preview once. No chat UI renders case internals, provider labels or temporary URL.

- [ ] **Step 4: Run reliability and analyzer checks**

```bash
cd /d/FlutterProject/nonto
flutter test test/chat_media_pending_test.dart test/community_chat_media_pending_test.dart test/chat_moderation_failure_test.dart test/chat_reliability_regression_test.dart test/nonto_community_chat_media_mentions_regression_test.dart
flutter analyze lib/models/message.dart lib/services/websocket_service.dart lib/services/chat_send_queue.dart lib/providers/chat_notifiers.dart lib/services/api/community_service.dart lib/screens/chat/chat_room_screen.dart lib/screens/community/community_chat_screen.dart
```

Expected: PASS; text queue ordering and moderation failure behavior remain intact.

- [ ] **Step 5: Commit Flutter chat media review**

```bash
cd /d/FlutterProject/nonto
git add lib/models/message.dart lib/services/websocket_service.dart lib/services/chat_send_queue.dart lib/providers/chat_notifiers.dart lib/services/api/community_service.dart lib/screens/chat/chat_room_screen.dart lib/screens/community/community_chat_screen.dart test/chat_media_pending_test.dart test/community_chat_media_pending_test.dart
git commit -m "feat: hold chat media pending moderation"
```

---

### Task 18: Deploy ffprobe and the moderation Worker with safe health/logging

**Files:**
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Modify: `deploy/moderation-worker-entrypoint.sh`
- Modify: `app/routers/health.py`
- Modify: `app/workers/moderation_worker.py`
- Create: `tests/test_media_deployment.py`
- Create: `tests/test_media_privacy.py`

- [ ] **Step 1: Write failing image/Compose/health/privacy tests**

Assert Docker installs `ffmpeg`, runs `ffprobe -version`, and includes executable Worker entrypoint. Compose has separate `moderation-worker`, same env/database, no published port, API healthy dependency, and Worker command. Health reports `cos_configured,tms_configured,ims_configured,vm_configured,ffprobe_available,queue_depth,oldest_task_age,dead_tasks,worker_heartbeat_age` without endpoint/region/bucket/secret.

Inject sentinels into filename, body, JWT, COS/Tencent secret, signed URL, SDK raw JSON, OCR/ASR and exception; assert logs, task errors, actions, API JSON and DB rows contain none. Allowed logs contain IDs, purpose, kind, task/case/object IDs, stable code, attempt, latency and request ID only.

- [ ] **Step 2: Run deployment/privacy tests and verify RED**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_media_deployment.py tests/test_media_privacy.py -q
```

Expected: FAIL because ffmpeg/health sections and complete redaction are absent.

- [ ] **Step 3: Install runtime tools and enforce metadata-only observability**

Add `ffmpeg` to apt packages and `RUN ffprobe -version`. Ensure both entrypoints are executable. Worker entrypoint executes `python -m app.workers.moderation_worker` and never Alembic; API entrypoint remains the only migration owner. Compose Worker depends on healthy API and has a heartbeat health command; API healthcheck uses `/api/health/`.

All logging uses parameterized metadata:

```python
logger.info(
  "media_task case_id=%s task_id=%s object_id=%s purpose=%s code=%s request_id=%s latency_ms=%s",
  case_id, task_id, object_id, purpose, code, request_id, latency_ms,
)
```

Use phase-2 `sanitize_error_message()` extended to strip query strings, `Authorization`, token-like values, COS/Tencent parameter names, paths containing original filenames, OCR/ASR/keywords and control characters; cap at 200 characters for Provider and 500 for task storage. Metrics contain no user ID or key.

- [ ] **Step 4: Build and run deployment/privacy verification**

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_media_deployment.py tests/test_media_privacy.py tests/test_moderation_health.py -q
docker compose build nantupy-backend moderation-worker
docker compose run --rm nantupy-backend ffprobe -version
docker compose config --quiet
```

Expected: tests PASS, both images build, ffprobe exits 0, Compose config is valid. No Provider call is made.

- [ ] **Step 5: Commit deployment and logging controls**

```bash
cd /d/NanTuPy
git add Dockerfile docker-compose.yml deploy/moderation-worker-entrypoint.sh app/routers/health.py app/workers/moderation_worker.py tests/test_media_deployment.py tests/test_media_privacy.py
git commit -m "ops: deploy secure media moderation worker"
```

---

### Task 19: Run full phase-3 acceptance, migration and security verification

**Files:**
- Create: `tests/test_moderation_phase3_acceptance.py`
- Create: `test/moderation_phase3_acceptance_test.dart`

- [ ] **Step 1: Write acceptance tests before final fixes**

Backend test covers: typed session including paired video thumbnail; all five exact HTTP error detail triples and accepted 202 `MEDIA_PENDING_REVIEW`; exact PUT headers; overwrite rejection; HEAD ownership/purpose/size/token; Pillow real format/pixel bomb; ffprobe video; raw Provider normalization to phase-2 `NormalizedTaskResult`/`ProviderStillProcessing`; image approve/reject/review; video submit/poll/retry/lease recovery with no `complete_task()` while processing; public uncertain text via TMS; direct/community text no Provider and initial chat media IMS/VM; multi-object aggregation; publish crash recovery; case-free `MediaCleanupJob` recovery; post/profile/community/comic/chat integration; exact pending WS ACK; all arbitrary URL/legacy bypasses; safe legacy import and URL-free manual inventory; stale version/deletion/idempotency; no preapproval feed/fanout/push; and sentinel privacy.

Flutter test covers: UploadSession JSON, exact transport headers, post/profile/community/comic/chat upload IDs, pending/rejected/approved UI, fresh v3/v1→v2→v3/v2→v3 migration paths without duplicate failure columns, restart restoration, exact reliable media ACK settlement by client ID plus persisted server pending ID, old approved media retention, and feed/cache/WS defense.

- [ ] **Step 2: Prove both acceptance tests RED at their production boundary**

Temporarily inject `UnwiredPhase3Boundary` as fake `CosStorage`/`UploadTransport`, run, observe that exact exception, then remove only the throwing stubs:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_phase3_acceptance.py -q
cd /d/FlutterProject/nonto
flutter test test/moderation_phase3_acceptance_test.dart
```

Expected: each fails first at `UnwiredPhase3Boundary`, proving the full path executes.

- [ ] **Step 3: Bind acceptance tests only to fakes and production interfaces**

```python
app.dependency_overrides[get_cos_storage] = lambda: FakeCosStorage()
app.dependency_overrides[get_moderation_provider] = lambda: FakeTencentModerationProvider()
app.dependency_overrides[get_clock] = lambda: fake_clock
```

```dart
final upload = UploadService(
  api: fakeApi,
  transport: RecordingUploadTransport(),
);
```

Use generated Pillow fixtures and stub ffprobe JSON; no `.env`, network, SDK client or real credential. Do not edit production state enums or add acceptance-only bypasses.

- [ ] **Step 4: Run complete backend and Flutter verification**

Backend:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest \
  tests/test_moderation_phase2_acceptance.py \
  tests/test_media_models.py tests/test_media_migration.py \
  tests/test_cos_storage.py tests/test_media_config.py \
  tests/test_upload_sessions.py tests/test_media_inspector.py \
  tests/test_tencent_moderation_provider.py \
  tests/test_media_moderation_service.py tests/test_media_worker.py tests/test_video_polling.py \
  tests/test_media_publish_saga.py tests/test_media_cleanup.py \
  tests/test_post_media_moderation.py tests/test_post_media_bypasses.py \
  tests/test_profile_media_moderation.py tests/test_community_media_moderation.py tests/test_comic_media_moderation.py \
  tests/test_chat_media_moderation.py tests/test_community_chat_media_moderation.py \
  tests/test_upload_bypass_matrix.py tests/test_no_legacy_media_writes.py tests/test_role_media_moderation.py \
  tests/test_legacy_media_import.py tests/test_legacy_media_import_cli.py \
  tests/test_media_deployment.py tests/test_media_privacy.py \
  tests/test_moderation_phase3_acceptance.py -q
./.venv/Scripts/python.exe -m pytest -q
./.venv/Scripts/python.exe -m alembic upgrade head
./.venv/Scripts/python.exe -m alembic heads
```

Expected: all tests PASS, migration succeeds, and exactly one head is printed.

Flutter:

```bash
cd /d/FlutterProject/nonto
flutter test \
  test/upload_session_model_test.dart test/upload_transport_test.dart test/upload_service_contract_test.dart \
  test/pending_moderation_store_test.dart test/media_feed_defense_test.dart test/local_db_media_migration_test.dart \
  test/post_media_upload_flow_test.dart test/profile_media_moderation_flow_test.dart \
  test/community_media_moderation_flow_test.dart test/comic_media_moderation_flow_test.dart \
  test/chat_media_pending_test.dart test/community_chat_media_pending_test.dart \
  test/moderation_phase3_acceptance_test.dart
flutter test
flutter analyze
```

Expected: all tests PASS and analyzer reports no issues.

Offline production-like validation uses Compose with explicit fake adapters and dummy non-secret configuration only:

```bash
cd /d/NanTuPy
MEDIA_STORAGE_BACKEND=fake MODERATION_PROVIDER_BACKEND=fake docker compose build
MEDIA_STORAGE_BACKEND=fake MODERATION_PROVIDER_BACKEND=fake docker compose config --quiet
MEDIA_STORAGE_BACKEND=fake MODERATION_PROVIDER_BACKEND=fake docker compose up -d nantupy-backend moderation-worker
MEDIA_STORAGE_BACKEND=fake MODERATION_PROVIDER_BACKEND=fake docker compose ps
```

Expected: API and Worker healthy, ffprobe available, fake storage rejects the second identical PUT, fake quarantine is anonymous-read denied, and fake approval publishes only after a fake Provider result. No automated command loads or calls COS/TMS/IMS/VM.

- [ ] **Step 5: Run a separate staging-only real-cloud acceptance manually**

This is an authorized operator checklist, not pytest/flutter_test/CI, not a production bucket, and not part of the automated commands above. Use a dedicated staging account, private staging bucket, staging-only TMS/IMS/VM BizTypes, disposable synthetic image/video/text with no personal data, least-privilege short-lived credentials injected by the deployment secret manager, and a pre-approved staging case/object prefix. Never paste credentials into a shell command/history or plan; never enable SDK HTTP debug; never print/dump environment, request/response bodies, signed URLs, COS domain/bucket/key, Provider raw response, OCR/ASR/labels, or exception text. Logs/notes contain only a timestamp, pass/fail, stable safe code, case/task/object IDs and provider request/job IDs.

The operator verifies in order: anonymous HEAD/GET of quarantine is denied; the signed PUT accepts exact headers once and rejects the second identical PUT; HEAD/confirm freezes metadata; IMS returns one terminal image result; VM submit returns a job ID and at least one `PROCESSING` poll leaves the task pending without `complete_task()`, then a terminal poll completes; optional public uncertain text exercises TMS, while direct/community text proves zero TMS call; approval publishes and deletes only quarantine through `MediaCleanupJob`; rejection never publishes; deleting the disposable staging target cannot delete an unconfigured key. After recording redacted pass/fail evidence, delete disposable staging objects/jobs through organization tooling and rotate/revoke the short-lived credential.

If any control cannot be observed without revealing a credential or URL, stop and use cloud-console/audit metadata instead; do not weaken redaction. This manual staging acceptance is required before production traffic but is never imported into, invoked by, or mocked as an automated test.

- [ ] **Step 6: Commit acceptance coverage**

```bash
cd /d/NanTuPy
git add tests/test_moderation_phase3_acceptance.py
git commit -m "test: verify media moderation phase three"
cd /d/FlutterProject/nonto
git add test/moderation_phase3_acceptance_test.dart
git commit -m "test: verify Flutter media moderation"
```

Expected: both repositories are clean after implementation commits; no real credential, signed URL, fixture media or Provider response is tracked.

---

## Final self-review checklist

- [ ] **Phase-2 boundary:** Task 1 gates the existing state machine/repository/Worker and exact `RiskCategory/Severity/rule_version/TaskType/NormalizedTaskResult/ProviderStillProcessing`; Tasks 5–7 reuse them without defining a parallel result, enum or state machine.
- [ ] **Schema and migration:** Task 2 covers upload/session ownership, Provider job persistence, async chat media, `MediaCleanupJob`, immutable `legacy_imported` provenance, URL-free manual inventory and the dynamically observed Alembic parent.
- [ ] **HTTP/ACK contracts:** Tasks 4, 8, 10, 13 and 17 assert all five locked status/code/message/retryable details, treat `MEDIA_PENDING_REVIEW` as accepted 202, deep-compare both client-ID spellings and save the server pending `message_id` only after client-ID settlement.
- [ ] **Typed upload security:** Tasks 3–4 cover backend-generated keys, private quarantine, exact ten-minute PUT, signed headers, overwrite prevention, HEAD/owner/purpose/size/MIME/token/ETag checks and confirmed-object freezing.
- [ ] **Real media validation:** Task 4 covers Pillow format/decode/frame/pixel bomb checks and ffprobe container/stream/codec/duration checks with bounded resources.
- [ ] **Cloud moderation:** Tasks 5–6 cover injectable TMS/IMS/VM, raw-result normalization, text/image/video submit/poll, retry, lease and crash recovery; processing stores the VM job ID/schedules `video_poll` without `complete_task()`. Direct/community text never calls TMS; initial chat media may call IMS/VM.
- [ ] **Publication and cleanup:** Task 7 covers copy/verify/apply saga and case-free `MediaCleanupJob` lifecycle cleanup. No orphan/compensation/expiry cleanup uses `ModerationTask`; rejected retention waits for phase 4.
- [ ] **Backend entry coverage:** Tasks 8–11 preserve exact `direct_chat_image/video` and `community_chat_image/video`, cover all media targets, reject arbitrary URLs and remove legacy upload bypasses.
- [ ] **Legacy safety:** Task 12 maps only configured trusted COS bucket/domain/prefix references into immutable read-only objects; all unsafe sources produce URL-free manual inventory, and import never copies/deletes arbitrary URLs.
- [ ] **Flutter coverage:** Tasks 13–17 cover `UploadSession`, exact `UploadTransport`, reuse of phase-2 `ModerationStatus/ModerationState`, pending recovery, exact ACK settlement and defensive feed/cache/WS handling. Task 14 owns the sole v3 Drift change and tests fresh v3, v1→v2→v3 and v2→v3 without duplicate failure columns; phase 2 has no Drift upgrade.
- [ ] **Deployment/privacy:** Task 18 covers FFmpeg/ffprobe, separate Worker, health, metadata-only logs, secret/signed-URL/body/OCR/ASR redaction and Compose.
- [ ] **Full acceptance:** Task 19 covers focused/full suites, analyzer, migration single-head, fake-only automated Docker validation and a separate manual staging-only real-cloud checklist that prints no credential or URL.
- [ ] **Placeholder scan:** Run from `D:\NanTuPy`; expected output is `placeholder scan clean`:

```bash
./.venv/Scripts/python.exe - <<'PY'
from pathlib import Path
p = Path("docs/superpowers/plans/2026-07-18-content-moderation-phase3-media.md")
text = p.read_text(encoding="utf-8")
forbidden = [
    "TB" + "D", "TO" + "DO", "implement " + "later", "fill in " + "details",
    "similar to " + "Task", "appropriate error " + "handling", "类似" + "任务",
]
found = [value for value in forbidden if value in text]
assert not found, found
print("placeholder scan clean")
PY
```

- [ ] **Type consistency:** Verify every task uses exact `UploadPurpose`, `MediaKind`, `UploadSessionStatus`, `UploadObjectStatus`, `PurposePolicy`, `CosHead`, `PresignedPut`, `CosStorage`, `ProviderDecision`, adapter-internal `ProviderResult`, `ModerationProvider`, and phase-2 `RiskCategory/Severity/CaseStatus/PublicModerationStatus/TaskStatus/TaskType/NormalizedTaskResult/ProviderStillProcessing`; Flutter uses `UploadSession/UploadTransport/ModerationStatus`.
- [ ] **HTTP/ACK consistency:** Search every producer/parser/test for the five locked errors and `MEDIA_PENDING_REVIEW`; status, `detail.code/detail.message/detail.retryable`, accepted-vs-failure handling, both client IDs, `case_id`, `message_id`, and `moderation_status` must match the canonical section exactly.
- [ ] **No real cloud tests:** Search automated tests for Tencent/COS client construction and external URLs; only fake clients/fixtures are allowed. The credentialed Task 19 staging checklist is manual, staging-only, separately authorized, and emits neither credential nor URL.
- [ ] **No arbitrary URL or public-before-review:** Search route request schemas and Flutter request maps; new business media writes contain only typed upload IDs. Search DB writes; new `public_url` is written only by `MediaPublishSaga`, while Task 12 may preserve a strictly allowlisted trusted legacy public URL with no query. Cleanup/import cannot parse arbitrary URLs into copy/delete keys.
- [ ] **Plan diff scope:** Before handing off this plan revision, run `git -C /d/NanTuPy diff --name-only -- docs/superpowers/plans/2026-07-18-content-moderation-phase3-media.md` and `git -C /d/NanTuPy status --short`; inspect `git -C /d/NanTuPy diff --check -- docs/superpowers/plans/2026-07-18-content-moderation-phase3-media.md`. Expected: no whitespace errors, this task edits only `docs/superpowers/plans/2026-07-18-content-moderation-phase3-media.md`, and no commit is created.
- [ ] **Implementation working-tree scope:** During implementation inspect `git diff --stat` and `git status --short` in both repositories before every task commit; after Task 19 commits, both contain only intentional phase-3 changes.
