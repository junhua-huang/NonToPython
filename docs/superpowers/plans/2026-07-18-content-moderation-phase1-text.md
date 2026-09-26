# Phase 1 Unified Text Moderation Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a synchronous, local, fail-closed text-moderation boundary for every current user-authored text write path, with dynamic database rules, immediate account deactivation enforcement, stable HTTP/WebSocket failures, and Flutter rollback support.

**Architecture:** Every backend route calls one `ModerationService`, which combines an immutable `LocalTextModerator` snapshot with `ModerationPolicy`; phase 1 can return only `approve` or `reject`, and any moderator/snapshot failure becomes `MODERATION_UNAVAILABLE`. Dynamic rule mutations advance a database-global version, while each API process polls and atomically replaces its snapshot; Flutter parses the stable HTTP contract and treats a non-200 WebSocket ACK as a terminal outbox settlement for that attempt.

**Tech Stack:** Python 3, FastAPI, Pydantic 2, SQLAlchemy 2.x synchronous ORM, Alembic, MySQL/PyMySQL, `regex`, pytest; Flutter/Dart, Dio, Riverpod/StateNotifier, Drift/SQLite, workspace package `reliable_websocket`, flutter_test.

---

## Phase boundary and locked contracts

- Phase 1 is **synchronous local text moderation only**. A write either proceeds immediately or is rejected before mutation and side effects.
- `ModerationResult.rule_version` is locked to `int` in every phase-1 producer, consumer, fixture, and serialized contract. `Severity` has exactly `low`, `medium`, and `high`; an approved result has `severity=None`. Phase 1 defines no severity-to-score mapping because later phases own that mapping.
- Phase 1 creates no `moderation_cases`, `content_revisions`, `moderation_tasks`, Provider, worker, Tencent Cloud integration, media quarantine, pending-review state, appeals, penalties, audit trail, historical backfill, or administrator UI.
- Images, videos, proof images, portfolio images, upload URLs, avatars, and covers keep their existing phase-1 behavior. Only user-authored text fields and URL-like text explicitly listed in the inventory are moderated.
- System-generated notifications, recall notices, welcome messages, and the automatic friend-acceptance `Hi` messages are not moderated.
- Administrators and organizers do not bypass text moderation when creating user-visible or governance text.
- A rejected edit never overwrites the existing value; no revision row is necessary because moderation runs before assignment.
- Business routes must not import or call `ContentModeration`, `ContentFilter`, or `content_filter`; the only route-level entry is `ModerationService`.
- Phase 1 makes no external moderation-provider call. Private/direct-message bodies must never be sent to Tencent Cloud.
- Logs must never contain JWTs, cloud credentials, complete bodies, body previews, matched terms, regex expressions, complete Provider responses, signed URLs, or internal exception text/stack traces.
- Locked backend imports for phase 2 reuse:

```python
from app.services.moderation_service import ModerationService, moderation_service
from app.services.local_text_moderator import LocalTextModerator
from app.services.moderation_policy import ModerationPolicy
from app.services.moderation_types import (
    ModerationContext,
    ModerationDecision,
    ModerationResult,
    RiskCategory,
    Severity,
)
```

- Phase 2 extends this same `ModerationService` with `moderate_realtime_text` and `moderate_public_text`. Phase 1 defines only `moderate_text` and `moderate_fields`; it must not predefine either phase-2 method, an alias with those names, or a competing service class. Later phase-2 work adds the two methods to this class rather than replacing or wrapping the phase-1 service.
- Phase 1 is the sole owner of Flutter Drift schema migration v1→v2. Its v2 adds exactly the nullable `failure_code`, `failure_message`, and `retryable` columns to `messages_table` while preserving v1 rows. Phase 2 starts from schema v2 and must not repeat the v1→v2 branch, add these columns again, or claim ownership of this migration.

- Locked HTTP failures keep FastAPI's minimal `detail` envelope and one consistent payload shape:

```json
{"detail":{"code":"CONTENT_REJECTED","message":"内容未通过审核","retryable":false}}
```

```json
{"detail":{"code":"MODERATION_UNAVAILABLE","message":"内容审核服务暂不可用，请稍后重试","retryable":true}}
```

```json
{"detail":{"code":"ACCOUNT_DISABLED","message":"账号已停用","retryable":false}}
```

  `CONTENT_REJECTED` uses 422, `MODERATION_UNAVAILABLE` uses 503, and `ACCOUNT_DISABLED` uses 403. The locked retryability values are respectively `false`, `true`, and `false`; backend JSON, Flutter `ApiResponse`, tests, and status descriptions must agree. Ordinary responses expose no matched word, regular expression, threshold, normalized body, or exception text.

- Locked failed WebSocket ACK (a failed ACK is not a generic `error` frame):

```json
{
  "type": "ack",
  "request_id": "original request id",
  "client_msg_id": "original client_msg_id",
  "clientMsgId": "original client_msg_id",
  "status": 422,
  "code": "CONTENT_REJECTED",
  "retryable": false,
  "msg": "内容未通过审核",
  "message": "内容未通过审核"
}
```

  `MODERATION_UNAVAILABLE` uses the same shape with status 503 and `retryable=true`. A failed ACK has no `message_id` and creates no success dedup record.

- Locked Flutter behavior:
  - `CONTENT_REJECTED`: `isRetryable == false`, roll back optimistic UI, show `内容未通过审核`.
  - `MODERATION_UNAVAILABLE`: `isRetryable == true`, roll back non-chat mutations; retain a failed chat item and allow a user-initiated retry.
  - `ACCOUNT_DISABLED`: HTTP 403, `isRetryable == false`, never refresh, clear token/persisted session, close WebSocket through the existing token-clear hook, exit to the logged-out flow, and invoke `ApiClient.onTokenExpired` once.

## File responsibility map

### Backend repository `D:\NanTuPy`

- `app/services/moderation_types.py`: immutable enums, context, rule, snapshot, and result values.
- `app/services/moderation_errors.py`: stable error codes/messages/statuses and HTTP conversion.
- `app/services/moderation_policy.py`: phase-1 approve/reject-only policy.
- `app/services/local_text_moderator.py`: normalization, baseline and dynamic literal/regex matching, spam/abuse/hate rules.
- `app/services/moderation_snapshot.py`: database snapshot load, safe-regex validation, global-version polling, atomic replacement.
- `app/services/moderation_service.py`: sole business entry, field-batch checking, and fail-closed conversion.
- `app/services/moderation_inventory.py`: machine-verifiable endpoint-to-field inventory and explicit exclusions.
- `app/models/models.py`: expanded `SensitiveWord` plus singleton `SensitiveWordVersion` ORM.
- `alembic/versions/2026_07_18_0200_content_moderation_phase1.py`: schema and data migration whose `down_revision` is generated from the execution-time real head.
- `app/main.py`: moderation snapshot process lifecycle and canonical administrator router registration.
- `app/core/auth_core.py`, `app/dependencies.py`, `app/routers/auth.py`, `app/routers/ws.py`: active-account enforcement.
- `app/routers/admin.py`: canonical dynamic-rule CRUD plus legacy-path compatibility.
- Existing write routers: moderation before all mutation/commit/fanout/notification/email work.
- Existing filters: compatibility shims only, with body-revealing logs removed.
- New focused tests under `tests/test_moderation_*.py` and `tests/test_inactive_auth.py`.

### Flutter repository `D:\FlutterProject\nonto`

- `lib/services/api/api_client.dart`: structured error code/message/retryability and disabled-account interception.
- Mutation services/providers/screens: preserve codes and roll back failed optimistic state.
- `packages/reliable_websocket/lib/src/models/send_failure.dart`: typed protocol failure.
- Reliable WebSocket codec/client/sender/outbox: non-200 ACK terminal settlement.
- `lib/services/websocket_service.dart`, `lib/services/chat_send_queue.dart`, `lib/providers/chat_notifiers.dart`: typed failure propagation and exact `client_msg_id` settlement.
- `lib/models/message.dart`, `lib/services/database/app_database.dart`, generated `app_database.g.dart`, and `lib/services/local_db_service.dart`: persisted failure code/message/retryability and Phase-1-owned schema v1→v2 migration.
- Focused tests under root `test/` and package `packages/reliable_websocket/test/`.

### - [ ] Task 1: Lock moderation types, errors, policy, service, and inventory contracts

**Files:**
- Create: `D:\NanTuPy\app\services\moderation_types.py`
- Create: `D:\NanTuPy\app\services\moderation_errors.py`
- Create: `D:\NanTuPy\app\services\moderation_policy.py`
- Create: `D:\NanTuPy\app\services\moderation_service.py`
- Create: `D:\NanTuPy\app\services\moderation_inventory.py`
- Create: `D:\NanTuPy\tests\test_moderation_phase1_contracts.py`

- [ ] **Step 1: Write the RED contract tests**

```python
# D:\NanTuPy\tests\test_moderation_phase1_contracts.py
from dataclasses import FrozenInstanceError, asdict
import pytest
from fastapi import HTTPException

from app.services.moderation_errors import (
    AppContractError,
    ErrorCode,
    ModerationUnavailable,
    content_rejected,
    to_http_exception,
)
from app.services.moderation_inventory import MODERATED_TEXT_FIELDS
from app.services.moderation_policy import ModerationPolicy
from app.services.moderation_service import ModerationService
from app.services.moderation_types import (
    ModerationContext,
    ModerationDecision,
    ModerationResult,
    RiskCategory,
    Severity,
)


def approved_result() -> ModerationResult:
    return ModerationResult(
        decision=ModerationDecision.APPROVE,
        risk_category=RiskCategory.OTHER,
        severity=None,
        confidence=1.0,
        policy_version="phase1-local-v1",
        rule_version=0,
    )


def test_result_is_immutable_and_phase1_has_only_two_decisions():
    assert {item.value for item in ModerationDecision} == {"approve", "reject"}
    assert {item.value for item in Severity} == {"low", "medium", "high"}
    result = approved_result()
    assert result.severity is None
    assert type(result.rule_version) is int
    assert type(asdict(result)["rule_version"]) is int
    with pytest.raises(TypeError, match="rule_version must be int"):
        ModerationResult(
            decision=ModerationDecision.APPROVE,
            risk_category=RiskCategory.OTHER,
            severity=None,
            confidence=1.0,
            policy_version="phase1-local-v1",
            rule_version="1",  # type: ignore[arg-type]
        )
    with pytest.raises(FrozenInstanceError):
        result.confidence = 0.1


def test_policy_never_introduces_pending_or_manual_review():
    context = ModerationContext(target_type="post", actor_user_id=7, is_public=True)
    assert ModerationPolicy().decide(context, approved_result()) is ModerationDecision.APPROVE


def test_phase1_service_does_not_predefine_phase2_entrypoints():
    assert not hasattr(ModerationService, "moderate_realtime_text")
    assert not hasattr(ModerationService, "moderate_public_text")


def test_http_error_contracts_do_not_expose_internal_details():
    rejected = to_http_exception(content_rejected())
    unavailable = to_http_exception(ModerationUnavailable(RuntimeError("database password secret")))
    assert (rejected.status_code, rejected.detail) == (
        422,
        {"code": "CONTENT_REJECTED", "message": "内容未通过审核", "retryable": False},
    )
    assert (unavailable.status_code, unavailable.detail) == (
        503,
        {
            "code": "MODERATION_UNAVAILABLE",
            "message": "内容审核服务暂不可用，请稍后重试",
            "retryable": True,
        },
    )
    disabled = to_http_exception(AppContractError(ErrorCode.ACCOUNT_DISABLED))
    assert (disabled.status_code, disabled.detail) == (
        403,
        {"code": "ACCOUNT_DISABLED", "message": "账号已停用", "retryable": False},
    )
    assert "secret" not in str(unavailable.detail)


def test_inventory_is_complete_and_has_no_media_fields():
    assert set(MODERATED_TEXT_FIELDS) == {
        "POST /api/auth/register", "PUT /api/auth/profile",
        "POST /api/posts", "PUT /api/posts/{post_id}",
        "POST /api/posts/{post_id}/comments", "PUT /api/comments/{comment_id}",
        "POST /api/chat/conversations/{conversation_id}/messages",
        "WS send_message",
        "POST /api/communities", "PATCH /api/communities/{community_id}",
        "POST /api/communities/{community_id}/join",
        "POST /api/communities/{community_id}/announcements",
        "PATCH /api/communities/{community_id}/announcements/{announcement_id}",
        "POST /api/communities/{community_id}/bans",
        "POST /api/communities/{community_id}/chat/messages",
        "POST /api/comic/events", "PUT /api/comic/events/{event_id}",
        "POST /api/comic/events/{event_id}/comments",
        "POST /api/roles/apply", "PUT /api/roles/profiles/coser",
        "PUT /api/roles/profiles/photographer", "PUT /api/roles/profiles/service",
        "POST /api/roles/applications/{application_id}/approve",
        "POST /api/roles/applications/{application_id}/reject",
        "POST /api/roles/applications/{application_id}/suspend",
        "POST /role-applications/{application_id}/approve",
        "POST /role-applications/{application_id}/reject",
        "POST /role-applications/{application_id}/suspend",
        "POST /api/topics", "PUT /api/topics/{topic_id}",
        "POST /api/reports", "POST /api/reports/post",
        "POST /api/reports/comment", "POST /api/reports/user",
    }
    flattened = {field for fields in MODERATED_TEXT_FIELDS.values() for field in fields}
    assert not flattened.intersection({"image", "video", "avatar_url", "banner_url", "proof_images"})
```

- [ ] **Step 2: Run the contract tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_phase1_contracts.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'app.services.moderation_types'` (or the first other new moderation module).

- [ ] **Step 3: Add the minimal locked types, errors, policy, service signature, and full inventory**

```python
# D:\NanTuPy\app\services\moderation_types.py
from dataclasses import dataclass
from enum import Enum
from typing import Any

class ModerationDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"

class RiskCategory(str, Enum):
    SEXUAL = "sexual"
    VIOLENCE = "violence"
    ILLEGAL = "illegal"
    ABUSE = "abuse"
    HATE = "hate"
    SPAM = "spam"
    PRIVACY = "privacy"
    OTHER = "other"

class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

@dataclass(frozen=True, slots=True)
class ModerationContext:
    target_type: str
    actor_user_id: int | None
    is_public: bool

@dataclass(frozen=True, slots=True)
class ModerationRule:
    id: int | None
    expression: str
    match_type: str
    category: RiskCategory
    severity: Severity
    row_version: int

@dataclass(frozen=True, slots=True)
class CompiledRegexRule:
    rule: ModerationRule
    pattern: Any  # regex.Pattern; kept Any because the package does not export a stable generic type

@dataclass(frozen=True, slots=True)
class ModerationSnapshot:
    version: int
    literal_rules: tuple[ModerationRule, ...]
    regex_rules: tuple[CompiledRegexRule, ...]
    loaded_at: float
    verified_at: float

@dataclass(frozen=True, slots=True)
class ModerationResult:
    decision: ModerationDecision
    risk_category: RiskCategory
    severity: Severity | None
    confidence: float
    policy_version: str
    rule_version: int
    matched_rule_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if type(self.rule_version) is not int:
            raise TypeError("rule_version must be int")
```

```python
# D:\NanTuPy\app\services\moderation_errors.py
from enum import Enum
from fastapi import HTTPException

class ErrorCode(str, Enum):
    CONTENT_REJECTED = "CONTENT_REJECTED"
    MODERATION_UNAVAILABLE = "MODERATION_UNAVAILABLE"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"

_ERROR_CONTRACT = {
    ErrorCode.CONTENT_REJECTED: (422, "内容未通过审核", False),
    ErrorCode.MODERATION_UNAVAILABLE: (503, "内容审核服务暂不可用，请稍后重试", True),
    ErrorCode.ACCOUNT_DISABLED: (403, "账号已停用", False),
}

class AppContractError(Exception):
    def __init__(self, code: ErrorCode, cause: Exception | None = None):
        self.error_code = code
        self.status_code, self.public_message, self.retryable = _ERROR_CONTRACT[code]
        self.cause = cause
        super().__init__(self.public_message)

class ContentRejected(AppContractError):
    def __init__(self):
        super().__init__(ErrorCode.CONTENT_REJECTED)

class ModerationUnavailable(AppContractError):
    def __init__(self, cause: Exception | None = None):
        super().__init__(ErrorCode.MODERATION_UNAVAILABLE, cause)

def content_rejected() -> ContentRejected:
    return ContentRejected()

def to_http_exception(error: AppContractError) -> HTTPException:
    return HTTPException(
        status_code=error.status_code,
        detail={
            "code": error.error_code.value,
            "message": error.public_message,
            "retryable": error.retryable,
        },
    )
```

```python
# D:\NanTuPy\app\services\moderation_policy.py
from app.services.moderation_types import ModerationContext, ModerationDecision, ModerationResult

class ModerationPolicy:
    version = "phase1-local-v1"

    def decide(self, context: ModerationContext, local_result: ModerationResult) -> ModerationDecision:
        del context
        return local_result.decision
```

```python
# D:\NanTuPy\app\services\moderation_service.py
from collections.abc import Mapping
from app.services.moderation_errors import ContentRejected, ModerationUnavailable
from app.services.moderation_policy import ModerationPolicy
from app.services.moderation_types import ModerationContext, ModerationDecision, ModerationResult

class ModerationService:
    def __init__(self, local_moderator=None, policy: ModerationPolicy | None = None):
        self.local_moderator = local_moderator
        self.policy = policy or ModerationPolicy()

    def moderate_text(self, text: str | None, context: ModerationContext) -> ModerationResult:
        if self.local_moderator is None:
            raise ModerationUnavailable(RuntimeError("local moderator not initialized"))
        try:
            result = self.local_moderator.moderate(text or "")
            decision = self.policy.decide(context, result)
        except ContentRejected:
            raise
        except Exception as exc:
            raise ModerationUnavailable(exc) from exc
        if decision is ModerationDecision.REJECT:
            raise ContentRejected()
        return result

    def moderate_fields(
        self,
        fields: Mapping[str, str | None],
        context: ModerationContext,
    ) -> tuple[ModerationResult, ...]:
        return tuple(
            self.moderate_text(value, context)
            for value in fields.values()
            if value is not None and value.strip()
        )

moderation_service = ModerationService()
```

```python
# D:\NanTuPy\app\services\moderation_inventory.py
from collections.abc import Sequence

MODERATED_TEXT_FIELDS: dict[str, Sequence[str]] = {
    "POST /api/auth/register": ("username",),
    "PUT /api/auth/profile": ("display_name", "bio"),
    "POST /api/posts": ("content",),
    "PUT /api/posts/{post_id}": ("content",),
    "POST /api/posts/{post_id}/comments": ("content",),
    "PUT /api/comments/{comment_id}": ("content",),
    "POST /api/chat/conversations/{conversation_id}/messages": ("content",),
    "WS send_message": ("content",),
    "POST /api/communities": ("name", "description", "rules"),
    "PATCH /api/communities/{community_id}": ("name", "description", "rules"),
    "POST /api/communities/{community_id}/join": ("message",),
    "POST /api/communities/{community_id}/announcements": ("title", "content"),
    "PATCH /api/communities/{community_id}/announcements/{announcement_id}": ("title", "content"),
    "POST /api/communities/{community_id}/bans": ("reason",),
    "POST /api/communities/{community_id}/chat/messages": ("content",),
    "POST /api/comic/events": ("name", "venue", "ticket_info", "website", "intro"),
    "PUT /api/comic/events/{event_id}": ("name", "venue", "ticket_info", "website", "intro"),
    "POST /api/comic/events/{event_id}/comments": ("content",),
    "POST /api/roles/apply": ("reason", "application_text", "contact_info", "extra_note", "portfolio_links[]"),
    "PUT /api/roles/profiles/coser": ("cosname", "bio", "styles", "city", "social_links"),
    "PUT /api/roles/profiles/photographer": ("equipment", "styles", "city", "social_links"),
    "PUT /api/roles/profiles/service": ("service_type", "description", "city", "price_info"),
    "POST /api/roles/applications/{application_id}/approve": ("review_comment",),
    "POST /api/roles/applications/{application_id}/reject": ("review_comment",),
    "POST /api/roles/applications/{application_id}/suspend": ("review_comment",),
    "POST /role-applications/{application_id}/approve": ("review_comment",),
    "POST /role-applications/{application_id}/reject": ("review_comment",),
    "POST /role-applications/{application_id}/suspend": ("review_comment",),
    "POST /api/topics": ("name", "description"),
    "PUT /api/topics/{topic_id}": ("description",),
    "POST /api/reports": ("reason",),
    "POST /api/reports/post": ("reason",),
    "POST /api/reports/comment": ("reason",),
    "POST /api/reports/user": ("reason",),
}

EXCLUDED_TEXT_INPUTS: dict[str, tuple[str, ...]] = {
    "POST /api/auth/register": ("email", "password", "email_code"),
    "POST /api/auth/login": ("login", "email", "password", "email_code"),
    "POST /api/auth/change-password": ("old_password", "new_password"),
    "POST /api/auth/forgot-password": ("email",),
    "POST /api/auth/reset-password": ("email", "email_code", "new_password"),
    "message media metadata": ("media_url", "avatar_url", "banner_url", "cover_photo_url"),
    "role media evidence": ("proof_images", "portfolio_images"),
    "read-only queries": ("keyword", "search_query", "pagination", "sort"),
    "system-generated text": (
        "friend_acceptance_hi", "community_welcome_message",
        "notification_text", "recall_notice",
    ),
}
```

`MODERATED_TEXT_FIELDS` is the complete inventory of persistent/user-visible or governance text writes present at implementation time. `EXCLUDED_TEXT_INPUTS` records the other text-shaped inputs: authentication secrets/identifiers, media locations/evidence, read-only filters, and system-generated text. Before finishing Task 12, compare all FastAPI `Body`/Pydantic write fields against the union of these maps; any newly discovered text field must be added to one map with a rejection test or an explicit security/system rationale.

- [ ] **Step 4: Run the contract tests and verify GREEN**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_phase1_contracts.py -q
```

Expected: `5 passed`.

- [ ] **Step 5: Commit Task 1**

```bash
cd /d/NanTuPy
git add app/services/moderation_types.py app/services/moderation_errors.py app/services/moderation_policy.py app/services/moderation_service.py app/services/moderation_inventory.py tests/test_moderation_phase1_contracts.py
git commit -m "feat: define phase one moderation contracts"
```

### - [ ] Task 2: Implement normalized local rules and fail-closed service behavior

**Files:**
- Create: `D:\NanTuPy\app\services\local_text_moderator.py`
- Modify: `D:\NanTuPy\app\services\moderation_service.py`
- Modify: `D:\NanTuPy\requirements.txt`
- Create: `D:\NanTuPy\tests\test_local_text_moderator.py`

- [ ] **Step 1: Write RED tests for normalization, baseline rules, spam rules, and failures**

```python
# D:\NanTuPy\tests\test_local_text_moderator.py
import pytest
from app.services.local_text_moderator import LocalTextModerator, normalize_text
from app.services.moderation_errors import ContentRejected, ModerationUnavailable
from app.services.moderation_service import ModerationService
from app.services.moderation_types import (
    CompiledRegexRule, ModerationContext, ModerationDecision, ModerationRule,
    ModerationSnapshot, RiskCategory, Severity,
)

CONTEXT = ModerationContext(target_type="chat_message", actor_user_id=3, is_public=False)


def empty_snapshot() -> ModerationSnapshot:
    return ModerationSnapshot(
        version=7, literal_rules=(), regex_rules=(), loaded_at=1.0, verified_at=1.0
    )


def test_unicode_and_zero_width_are_normalized():
    assert normalize_text("Ａ\u200bＢ  C") == "ab c"


@pytest.mark.parametrize("text,category", [
    ("黄色网站", "sexual"),
    ("百家乐", "illegal"),
    ("海洛因", "illegal"),
    ("恐怖袭击", "violence"),
    ("种族歧视", "hate"),
    ("kill yourself", "abuse"),
])
def test_existing_baseline_categories_are_preserved(text, category):
    result = LocalTextModerator(lambda: empty_snapshot()).moderate(text)
    assert result.decision is ModerationDecision.REJECT
    assert result.risk_category.value == category


def test_baseline_literal_and_obfuscated_spam_reject_without_leaking_match():
    moderator = LocalTextModerator(lambda: empty_snapshot())
    assert moderator.moderate("normal fictional text").decision is ModerationDecision.APPROVE
    assert moderator.moderate("加  微  信 wx123456").decision is ModerationDecision.REJECT


def test_service_raises_stable_rejection():
    moderator = LocalTextModerator(lambda: empty_snapshot())
    with pytest.raises(ContentRejected):
        ModerationService(moderator).moderate_text("加微信 wx123456", CONTEXT)


def test_snapshot_or_rule_failure_is_fail_closed():
    def broken_snapshot():
        raise RuntimeError("database unavailable with private body")
    with pytest.raises(ModerationUnavailable) as caught:
        ModerationService(LocalTextModerator(broken_snapshot)).moderate_text("hello", CONTEXT)
    assert "private body" not in caught.value.public_message


def test_regex_runtime_timeout_is_fail_closed():
    class TimingOutPattern:
        def search(self, text: str, timeout: float):
            raise TimeoutError("private regex expression")

    rule = ModerationRule(
        id=8, expression="redacted", match_type="regex",
        category=RiskCategory.SPAM, severity=Severity.HIGH, row_version=1,
    )
    snapshot = ModerationSnapshot(
        version=8,
        literal_rules=(),
        regex_rules=(CompiledRegexRule(rule=rule, pattern=TimingOutPattern()),),
        loaded_at=1.0,
        verified_at=1.0,
    )
    with pytest.raises(ModerationUnavailable) as caught:
        ModerationService(LocalTextModerator(lambda: snapshot)).moderate_text("hello", CONTEXT)
    assert caught.value.error_code.value == "MODERATION_UNAVAILABLE"
    assert "private regex" not in caught.value.public_message
```

- [ ] **Step 2: Run the local moderator tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_local_text_moderator.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'app.services.local_text_moderator'`.

- [ ] **Step 3: Add `regex` and implement the smallest local moderator**

Add this exact dependency:

```text
regex>=2024.11.6
```

```python
# D:\NanTuPy\app\services\local_text_moderator.py
import re
import unicodedata
from collections.abc import Callable

from app.services.moderation_types import (
    ModerationDecision, ModerationResult, ModerationSnapshot,
    RiskCategory, Severity,
)

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)
_SEXUAL_LITERALS = frozenset({
    "色情", "淫秽", "裸体", "裸聊", "嫖娼", "卖淫", "黄色网站", "约炮",
    "porn", "xxx", "escort", "naked", "nude", "hentai",
})
_ILLEGAL_LITERALS = frozenset({
    "赌博", "赌场", "赌球", "六合彩", "百家乐", "博彩", "毒品", "吸毒",
    "大麻", "海洛因", "冰毒", "可卡因", "代开发票", "casino", "gambling",
    "cocaine", "heroin", "marijuana", "meth", "hacking", "fake id", "counterfeit",
})
_VIOLENCE_LITERALS = frozenset({
    "杀人", "绑架", "恐怖袭击", "爆炸", "枪支", "kill you", "murder", "massacre", "terrorist",
})
_HATE_LITERALS = frozenset({
    "种族歧视", "性别歧视", "地域歧视", "宗教歧视", "hate speech", "nazi", "法西斯", "种族主义",
})
_ABUSE_LITERALS = frozenset({
    "kill yourself", "去死", "弄死你", "人肉", "人身攻击", "dox",
})
_BASELINE_LITERAL_GROUPS = (
    (_SEXUAL_LITERALS, RiskCategory.SEXUAL, Severity.HIGH),
    (_ILLEGAL_LITERALS, RiskCategory.ILLEGAL, Severity.HIGH),
    (_VIOLENCE_LITERALS, RiskCategory.VIOLENCE, Severity.HIGH),
    (_HATE_LITERALS, RiskCategory.HATE, Severity.HIGH),
    (_ABUSE_LITERALS, RiskCategory.ABUSE, Severity.HIGH),
)
_SPAM = re.compile(r"(?:加\s*(?:微\s*信|v)|wx)\s*[:：-]?\s*[a-z0-9_-]{5,}", re.I)
_HATE = re.compile(r"(?:全部|都)\s*(?:滚|去死)", re.I)


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "").translate(_ZERO_WIDTH).casefold()
    return " ".join(normalized.split())


class LocalTextModerator:
    def __init__(self, snapshot_provider: Callable[[], ModerationSnapshot]):
        self._snapshot_provider = snapshot_provider

    def moderate(self, text: str) -> ModerationResult:
        normalized = normalize_text(text)
        snapshot = self._snapshot_provider()
        category, severity, confidence = RiskCategory.OTHER, None, 1.0
        matched_ids: list[int] = []

        for literals, literal_category, literal_severity in _BASELINE_LITERAL_GROUPS:
            if any(normalize_text(literal) in normalized for literal in literals):
                category, severity, confidence = literal_category, literal_severity, 1.0
                break
        else:
            if _SPAM.search(normalized):
                category, severity, confidence = RiskCategory.SPAM, Severity.MEDIUM, 0.98
            elif _HATE.search(normalized):
                category, severity, confidence = RiskCategory.HATE, Severity.HIGH, 0.98
            else:
                for rule in snapshot.literal_rules:
                    if normalize_text(rule.expression) in normalized:
                        category, severity, confidence = rule.category, rule.severity, 1.0
                        if rule.id is not None:
                            matched_ids.append(rule.id)
                        break
                else:
                    for compiled_rule in snapshot.regex_rules:
                        rule = compiled_rule.rule
                        if compiled_rule.pattern.search(normalized, timeout=0.02):
                            category, severity, confidence = rule.category, rule.severity, 0.99
                            if rule.id is not None:
                                matched_ids.append(rule.id)
                            break

        rejected = severity is not None
        return ModerationResult(
            decision=ModerationDecision.REJECT if rejected else ModerationDecision.APPROVE,
            risk_category=category,
            severity=severity,
            confidence=confidence,
            policy_version="phase1-local-v1",
            rule_version=snapshot.version,
            matched_rule_ids=tuple(matched_ids),
        )
```

Keep `ModerationService.moderate_text()` as the fail-closed boundary from Task 1: it catches snapshot, regex timeout, normalization, and rule evaluation exceptions and raises `ModerationUnavailable` without including `str(exc)` in public output.

- [ ] **Step 4: Install, run focused tests, and verify GREEN**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv/Scripts/python.exe -m pytest tests/test_local_text_moderator.py tests/test_moderation_phase1_contracts.py -q
```

Expected: dependency installation succeeds and `16 passed` (11 local-moderator cases plus 5 contract cases).

- [ ] **Step 5: Commit Task 2**

```bash
cd /d/NanTuPy
git add requirements.txt app/services/local_text_moderator.py app/services/moderation_service.py tests/test_local_text_moderator.py
git commit -m "feat: add fail closed local text moderator"
```

### - [ ] Task 3: Add dynamic sensitive-word ORM and a migration based on the real Alembic head

**Files:**
- Modify: `D:\NanTuPy\app\models\models.py`
- Create: `D:\NanTuPy\alembic\versions\2026_07_18_0200_content_moderation_phase1.py`
- Create: `D:\NanTuPy\tests\test_moderation_migration.py`

- [ ] **Step 1: Write RED ORM schema tests**

```python
# D:\NanTuPy\tests\test_moderation_migration.py
from sqlalchemy import inspect
from app.models.models import SensitiveWord, SensitiveWordVersion


def test_sensitive_word_model_has_versioned_dynamic_rule_columns():
    columns = {column.name for column in inspect(SensitiveWord).columns}
    assert columns == {
        "id", "word", "match_type", "category", "severity", "is_active",
        "row_version", "created_by", "created_at", "updated_at",
    }
    assert SensitiveWord.__table__.c.word.type.length == 500


def test_global_version_model_is_singleton_shaped():
    assert SensitiveWordVersion.__tablename__ == "sensitive_word_versions"
    assert {c.name for c in inspect(SensitiveWordVersion).columns} == {
        "id", "version", "updated_at"
    }
```

- [ ] **Step 2: Run the ORM tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_migration.py -q
```

Expected: collection fails because `SensitiveWordVersion` does not exist.

- [ ] **Step 3: Expand the ORM with explicit constraints**

```python
# Replace the existing SensitiveWord model and add the version model in
# D:\NanTuPy\app\models\models.py
class SensitiveWord(Base):
    __tablename__ = "sensitive_words"
    __table_args__ = (
        UniqueConstraint("word", "match_type", name="uq_sensitive_word_expression_type"),
        CheckConstraint("match_type IN ('literal','regex')", name="ck_sensitive_word_match_type"),
        CheckConstraint("category IN ('sexual','violence','illegal','abuse','hate','spam','privacy','other')", name="ck_sensitive_word_category"),
        CheckConstraint("severity IN ('low','medium','high')", name="ck_sensitive_word_severity"),
        Index("ix_sensitive_word_active_version", "is_active", "row_version"),
    )

    id = Column(Integer, primary_key=True)
    word = Column(String(500), nullable=False)
    match_type = Column(String(16), nullable=False, default="literal")
    category = Column(String(32), nullable=False, default="other")
    severity = Column(String(16), nullable=False, default="medium")
    is_active = Column(Boolean, nullable=False, default=True)
    row_version = Column(Integer, nullable=False, default=1)
    created_by = Column(Integer, ForeignKey("users.id", name="fk_sensitive_words_creator"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "word": self.word, "match_type": self.match_type,
            "category": self.category, "severity": self.severity,
            "is_active": bool(self.is_active), "row_version": self.row_version,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SensitiveWordVersion(Base):
    __tablename__ = "sensitive_word_versions"
    __table_args__ = (CheckConstraint("id = 1", name="ck_sensitive_word_version_singleton"),)

    id = Column(Integer, primary_key=True)
    version = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
```

Add `CheckConstraint` to the existing SQLAlchemy imports in this file.

- [ ] **Step 4: Query the execution-time head before generating the migration**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m alembic heads
```

Expected: exactly one line ending in `(head)`. Stop and merge heads before continuing if zero or multiple heads are printed. Do not copy a revision observed while this plan was written.

- [ ] **Step 5: Generate from that head, rename to the locked path, and audit the script**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m alembic revision --autogenerate --rev-id 2026_07_18_0200 -m "content moderation phase 1"
GENERATED="$(find alembic/versions -maxdepth 1 -type f -name '*2026_07_18_0200*.py' -print -quit)"
test -n "$GENERATED"
mv "$GENERATED" alembic/versions/2026_07_18_0200_content_moderation_phase1.py
./.venv/Scripts/python.exe -c "from pathlib import Path; p=Path('alembic/versions/2026_07_18_0200_content_moderation_phase1.py'); s=p.read_text(encoding='utf-8'); assert 'down_revision' in s and '2026_07_18_0200' in s"
```

Expected: a single file exists at the locked path. Its generated `down_revision` equals the sole head printed in Step 4; it is not manually replaced with a revision from this document.

Edit `upgrade()` so existing rows are preserved and backfilled before non-null constraints, then create the singleton version row:

```python
def upgrade() -> None:
    op.add_column("sensitive_words", sa.Column("match_type", sa.String(16), nullable=True))
    op.add_column("sensitive_words", sa.Column("category", sa.String(32), nullable=True))
    op.add_column("sensitive_words", sa.Column("severity", sa.String(16), nullable=True))
    op.add_column("sensitive_words", sa.Column("is_active", sa.Boolean(), nullable=True))
    op.add_column("sensitive_words", sa.Column("row_version", sa.Integer(), nullable=True))
    op.add_column("sensitive_words", sa.Column("created_by", sa.Integer(), nullable=True))
    op.add_column("sensitive_words", sa.Column("updated_at", sa.DateTime(), nullable=True))
    op.alter_column("sensitive_words", "word", existing_type=sa.String(100), type_=sa.String(500), nullable=False)
    op.execute("UPDATE sensitive_words SET match_type='literal', category='other', severity='medium', is_active=1, row_version=1, updated_at=COALESCE(created_at, UTC_TIMESTAMP())")
    for name in ("match_type", "category", "severity", "is_active", "row_version", "updated_at"):
        op.alter_column("sensitive_words", name, nullable=False)
    unique_constraints = sa.inspect(op.get_bind()).get_unique_constraints("sensitive_words")
    word_unique = next(
        item["name"]
        for item in unique_constraints
        if item.get("column_names") == ["word"]
    )
    op.drop_constraint(word_unique, "sensitive_words", type_="unique")
    op.create_unique_constraint("uq_sensitive_word_expression_type", "sensitive_words", ["word", "match_type"])
    op.create_foreign_key("fk_sensitive_words_creator", "sensitive_words", "users", ["created_by"], ["id"])
    op.create_index("ix_sensitive_word_active_version", "sensitive_words", ["is_active", "row_version"])
    op.create_check_constraint("ck_sensitive_word_match_type", "sensitive_words", "match_type IN ('literal','regex')")
    op.create_check_constraint("ck_sensitive_word_category", "sensitive_words", "category IN ('sexual','violence','illegal','abuse','hate','spam','privacy','other')")
    op.create_check_constraint("ck_sensitive_word_severity", "sensitive_words", "severity IN ('low','medium','high')")
    op.create_table(
        "sensitive_word_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_sensitive_word_version_singleton"),
    )
    op.execute("INSERT INTO sensitive_word_versions (id, version, updated_at) VALUES (1, 1, UTC_TIMESTAMP())")
```

`downgrade()` must reverse these operations in exact dependency order and restore `word` to `String(100)` only after asserting no row exceeds 100 characters; raise `RuntimeError` instead of truncating data.

- [ ] **Step 6: Run model, migration, and head verification**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_migration.py -q
./.venv/Scripts/python.exe -m alembic heads
./.venv/Scripts/python.exe -m alembic check
```

Expected: `2 passed`; exactly one `2026_07_18_0200 (head)`; `No new upgrade operations detected.`

- [ ] **Step 7: Commit Task 3**

```bash
cd /d/NanTuPy
git add app/models/models.py alembic/versions/2026_07_18_0200_content_moderation_phase1.py tests/test_moderation_migration.py
git commit -m "feat: persist versioned moderation rules"
```

### - [ ] Task 4: Build safe regex validation, immutable snapshots, and cross-process polling

**Files:**
- Create: `D:\NanTuPy\app\services\moderation_snapshot.py`
- Modify: `D:\NanTuPy\app\services\moderation_service.py`
- Modify: `D:\NanTuPy\app\main.py`
- Create: `D:\NanTuPy\tests\test_moderation_snapshot.py`

- [ ] **Step 1: Write RED snapshot and regex tests**

```python
# D:\NanTuPy\tests\test_moderation_snapshot.py
import time
import pytest
from app.services.moderation_errors import ModerationUnavailable
from app.services.moderation_snapshot import (
    RegexValidationError, SensitiveWordSnapshotStore, validate_safe_regex,
)

@pytest.mark.parametrize("pattern", [r"(a+)+$", r"(a|aa)+$", r"(a*)*$", r"(.)\\1+"])
def test_unsafe_regex_is_rejected(pattern):
    with pytest.raises(RegexValidationError):
        validate_safe_regex(pattern)


def test_valid_regex_is_accepted():
    assert validate_safe_regex(r"wx[0-9]{5,12}") == r"wx[0-9]{5,12}"


def test_refresh_builds_before_atomic_swap(fake_rule_repository):
    store = SensitiveWordSnapshotStore(fake_rule_repository, max_stale_seconds=120)
    first = store.refresh(force=True)
    fake_rule_repository.version = 2
    fake_rule_repository.rows = [fake_rule_repository.regex_row(r"(a+)+$")]
    with pytest.raises(RegexValidationError):
        store.refresh()
    assert store.current_snapshot() is first
    assert store.current_snapshot().version == 1


def test_missing_or_stale_snapshot_fails_closed(fake_rule_repository):
    store = SensitiveWordSnapshotStore(fake_rule_repository, max_stale_seconds=0.001)
    with pytest.raises(ModerationUnavailable):
        store.current_snapshot()
    store.refresh(force=True)
    time.sleep(0.01)
    with pytest.raises(ModerationUnavailable):
        store.current_snapshot()
```

In the same file, define `fake_rule_repository` as an in-memory fixture returning objects with `id`, `word`, `match_type`, `category`, `severity`, and `row_version`; initialize version 1 with one literal row. This makes the test independent of MySQL.

- [ ] **Step 2: Run snapshot tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_snapshot.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'app.services.moderation_snapshot'`.

- [ ] **Step 3: Implement validation, repository, atomic snapshot store, and poll loop**

```python
# Key interfaces in D:\NanTuPy\app\services\moderation_snapshot.py
import asyncio
from dataclasses import replace
import logging
import re
import threading
import time
import regex
from app.database import SessionLocal
from app.models.models import SensitiveWord, SensitiveWordVersion
from app.services.moderation_errors import ModerationUnavailable
from app.services.moderation_types import (
    CompiledRegexRule, ModerationRule, ModerationSnapshot, RiskCategory, Severity,
)

class RegexValidationError(ValueError):
    """A rule expression failed bounded validation."""


logger = logging.getLogger(__name__)

_NESTED_REPEAT = re.compile(r"\((?:[^()\\]|\\.)*[+*{](?:[^()\\]|\\.)*\)[+*{]")
_AMBIGUOUS_BRANCH_REPEAT = re.compile(r"\((?:[^()\\]|\\.)*\|(?:[^()\\]|\\.)*\)[+*{]")
_BACK_REFERENCE = re.compile(r"\\(?:[1-9]|g<)")


def validate_safe_regex(pattern: str) -> str:
    expression = pattern.strip()
    if not expression or len(expression) > 256:
        raise RegexValidationError("regex length must be 1..256")
    if _NESTED_REPEAT.search(expression) or _AMBIGUOUS_BRANCH_REPEAT.search(expression):
        raise RegexValidationError("nested or ambiguous repeated groups are forbidden")
    if _BACK_REFERENCE.search(expression) or "(?<=" in expression or "(?<!" in expression:
        raise RegexValidationError("backreferences and lookbehind are forbidden")
    try:
        compiled = regex.compile(expression)
        for probe in ("a" * 2048 + "!", "ab" * 1024 + "!", "0" * 2048 + "!"):
            compiled.search(probe, timeout=0.02)
    except (regex.error, TimeoutError) as exc:
        raise RegexValidationError("regex is invalid or exceeds the time budget") from exc
    return expression


class SqlAlchemyRuleRepository:
    def get_version(self) -> int:
        with SessionLocal() as db:
            row = db.get(SensitiveWordVersion, 1)
            if row is None:
                raise RuntimeError("missing sensitive word version singleton")
            return row.version

    def get_active_rules(self) -> list[SensitiveWord]:
        with SessionLocal() as db:
            rows = (
                db.query(SensitiveWord)
                .filter(SensitiveWord.is_active.is_(True))
                .order_by(SensitiveWord.id)
                .all()
            )
            for row in rows:
                db.expunge(row)
            return rows


class SensitiveWordSnapshotStore:
    def __init__(self, repository, max_stale_seconds: float = 120.0):
        self._repository = repository
        self._max_stale_seconds = max_stale_seconds
        self._lock = threading.Lock()
        self._snapshot: ModerationSnapshot | None = None

    def refresh(self, force: bool = False) -> ModerationSnapshot:
        version = self._repository.get_version()
        now = time.monotonic()
        current = self._snapshot
        if not force and current is not None and current.version == version:
            verified = replace(current, verified_at=now)
            with self._lock:
                if self._snapshot is current:
                    self._snapshot = verified
                    return verified
            return self._snapshot or verified

        rows = self._repository.get_active_rules()
        if self._repository.get_version() != version:
            raise RuntimeError("rule version changed while snapshot was loading")

        literals: list[ModerationRule] = []
        expressions: list[CompiledRegexRule] = []
        for row in rows:
            expression = row.word if row.match_type == "literal" else validate_safe_regex(row.word)
            rule = ModerationRule(
                id=row.id, expression=expression, match_type=row.match_type,
                category=RiskCategory(row.category), severity=Severity(row.severity),
                row_version=row.row_version,
            )
            if row.match_type == "literal":
                literals.append(rule)
            else:
                expressions.append(CompiledRegexRule(rule=rule, pattern=regex.compile(expression)))
        candidate = ModerationSnapshot(
            version=version,
            literal_rules=tuple(literals),
            regex_rules=tuple(expressions),
            loaded_at=now,
            verified_at=now,
        )
        with self._lock:
            self._snapshot = candidate
        return candidate

    def current_snapshot(self) -> ModerationSnapshot:
        snapshot = self._snapshot
        if snapshot is None or time.monotonic() - snapshot.verified_at > self._max_stale_seconds:
            raise ModerationUnavailable(RuntimeError("moderation snapshot absent or stale"))
        return snapshot


async def poll_snapshots(store: SensitiveWordSnapshotStore, stop: asyncio.Event, interval: float = 30.0):
    while not stop.is_set():
        try:
            await asyncio.to_thread(store.refresh)
        except Exception:
            # Never log the exception, rule text, database URL, or stack trace.
            logger.error("moderation_snapshot_refresh_failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue

snapshot_store = SensitiveWordSnapshotStore(SqlAlchemyRuleRepository())
```

Wire `moderation_service.local_moderator = LocalTextModerator(snapshot_store.current_snapshot)` once, then use FastAPI lifespan to call one forced load and start/cancel one poll task per process:

```python
# D:\NanTuPy\app\main.py lifespan key code
from app.services.local_text_moderator import LocalTextModerator
from app.services.moderation_service import moderation_service
from app.services.moderation_snapshot import poll_snapshots, snapshot_store

moderation_service.local_moderator = LocalTextModerator(snapshot_store.current_snapshot)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    stop = asyncio.Event()
    try:
        await asyncio.to_thread(snapshot_store.refresh, True)
    except Exception:
        logger.error("moderation_snapshot_initial_load_failed")
    poller = asyncio.create_task(poll_snapshots(snapshot_store, stop))
    try:
        yield
    finally:
        stop.set()
        await poller
```

Import `asyncio`. Do not log rule expressions or exception strings from regex/database failures.

- [ ] **Step 4: Run snapshot and local moderator tests**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_snapshot.py tests/test_local_text_moderator.py -q
```

Expected: all tests pass; the exact count equals the tests present after adding the complete fake repository fixture.

- [ ] **Step 5: Commit Task 4**

```bash
cd /d/NanTuPy
git add app/services/moderation_snapshot.py app/services/moderation_service.py app/main.py tests/test_moderation_snapshot.py
git commit -m "feat: refresh immutable moderation snapshots"
```

### - [ ] Task 5: Replace the administrator word API and remove body-revealing moderation logs

**Files:**
- Modify: `D:\NanTuPy\app\routers\admin.py`
- Modify: `D:\NanTuPy\app\main.py`
- Modify: `D:\NanTuPy\app\services\content_filter.py`
- Modify: `D:\NanTuPy\app\services\content_moderation.py`
- Create: `D:\NanTuPy\tests\test_moderation_admin_api.py`
- Create: `D:\NanTuPy\tests\test_moderation_log_privacy.py`

- [ ] **Step 1: Write RED administrator API and privacy tests**

```python
# D:\NanTuPy\tests\test_moderation_admin_api.py
from app.models.models import SensitiveWordVersion


def test_canonical_create_validates_regex_and_advances_version(admin_client, db):
    before = db.get(SensitiveWordVersion, 1).version
    response = admin_client.post("/api/admin/moderation/sensitive-words", json={
        "word": r"wx[0-9]{5,12}", "match_type": "regex",
        "category": "spam", "severity": "medium", "is_active": True,
    })
    assert response.status_code == 201
    body = response.json()["word"]
    assert body["row_version"] == before + 1
    db.expire_all()
    assert db.get(SensitiveWordVersion, 1).version == before + 1


def test_unsafe_regex_does_not_change_version(admin_client, db):
    before = db.get(SensitiveWordVersion, 1).version
    response = admin_client.post("/api/admin/moderation/sensitive-words", json={
        "word": r"(a+)+$", "match_type": "regex",
        "category": "spam", "severity": "high", "is_active": True,
    })
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_MODERATION_RULE"
    db.expire_all()
    assert db.get(SensitiveWordVersion, 1).version == before


def test_patch_and_delete_each_advance_version_once(admin_client, db, created_rule):
    first = db.get(SensitiveWordVersion, 1).version
    assert admin_client.patch(
        f"/api/admin/moderation/sensitive-words/{created_rule.id}",
        json={"is_active": False},
    ).status_code == 200
    db.expire_all()
    assert db.get(SensitiveWordVersion, 1).version == first + 1
    assert admin_client.delete(
        f"/api/admin/moderation/sensitive-words/{created_rule.id}"
    ).status_code == 200
    db.expire_all()
    assert db.get(SensitiveWordVersion, 1).version == first + 2


def test_legacy_paths_remain_available(admin_client):
    assert admin_client.get("/sensitive-words").status_code == 200
```

```python
# D:\NanTuPy\tests\test_moderation_log_privacy.py
import logging
from app.services.content_filter import content_filter


def test_rejection_logs_never_contain_body_or_matched_expression(caplog):
    secret_body = "PRIVATE_BODY_8392"
    with caplog.at_level(logging.WARNING):
        content_filter.log_block(9, secret_body, ["MATCH_4481"])
    text = caplog.text
    assert secret_body not in text
    assert "MATCH_4481" not in text
    assert "user_id=9" in text
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_admin_api.py tests/test_moderation_log_privacy.py -q
```

Expected: administrator fixtures/endpoints are missing or return 404, and the privacy test finds the body/matched expression in current logs.

- [ ] **Step 3: Implement canonical CRUD, transactional version increments, and compatibility decorators**

Use a dedicated router while preserving old endpoints:

```python
# D:\NanTuPy\app\routers\admin.py key code
from pydantic import BaseModel, Field
from sqlalchemy import select
from app.models.models import SensitiveWordVersion
from app.services.moderation_snapshot import RegexValidationError, validate_safe_regex

moderation_router = APIRouter(prefix="/api/admin/moderation", tags=["Admin Moderation"])

class SensitiveWordCreate(BaseModel):
    word: str = Field(min_length=1, max_length=500)
    match_type: str = "literal"
    category: str = "other"
    severity: str = "medium"
    is_active: bool = True

class SensitiveWordPatch(BaseModel):
    word: str | None = Field(default=None, min_length=1, max_length=500)
    match_type: str | None = None
    category: str | None = None
    severity: str | None = None
    is_active: bool | None = None


def _advance_rule_version(db: Session) -> int:
    row = db.execute(
        select(SensitiveWordVersion).where(SensitiveWordVersion.id == 1).with_for_update()
    ).scalar_one()
    row.version += 1
    row.updated_at = datetime.utcnow()
    db.flush()
    return row.version


def _validated_expression(word: str, match_type: str) -> str:
    value = word.strip()
    if match_type not in {"literal", "regex"}:
        raise HTTPException(422, detail={"code": "INVALID_MODERATION_RULE", "message": "匹配类型无效"})
    if match_type == "regex":
        try:
            return validate_safe_regex(value)
        except RegexValidationError:
            raise HTTPException(422, detail={"code": "INVALID_MODERATION_RULE", "message": "正则规则不安全或无效"})
    return value


def _validate_metadata(category: str, severity: str) -> None:
    if category not in {item.value for item in RiskCategory}:
        raise HTTPException(422, detail={"code": "INVALID_MODERATION_RULE", "message": "风险分类无效"})
    if severity not in {"low", "medium", "high"}:
        raise HTTPException(422, detail={"code": "INVALID_MODERATION_RULE", "message": "风险等级无效"})


@moderation_router.get("/sensitive-words")
@router.get("/sensitive-words")
def get_sensitive_words(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    del user
    rows = db.query(SensitiveWord).order_by(SensitiveWord.id).all()
    return {"words": [row.to_dict() for row in rows]}

@moderation_router.post("/sensitive-words", status_code=201)
@router.post("/sensitive-words", include_in_schema=False)
def add_sensitive_word(data: SensitiveWordCreate, user=Depends(require_admin), db=Depends(get_db)):
    expression = _validated_expression(data.word, data.match_type)
    _validate_metadata(data.category, data.severity)
    version = _advance_rule_version(db)
    row = SensitiveWord(
        word=expression, match_type=data.match_type, category=data.category,
        severity=data.severity, is_active=data.is_active,
        row_version=version, created_by=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"message": "Sensitive word added", "word": row.to_dict()}

@moderation_router.patch("/sensitive-words/{word_id}")
def patch_sensitive_word(word_id: int, data: SensitiveWordPatch, user=Depends(require_admin), db=Depends(get_db)):
    row = db.get(SensitiveWord, word_id)
    if row is None:
        raise HTTPException(404, detail="Sensitive word not found")
    values = data.model_dump(exclude_unset=True)
    next_type = values.get("match_type", row.match_type)
    if "word" in values or "match_type" in values:
        values["word"] = _validated_expression(values.get("word", row.word), next_type)
    _validate_metadata(
        values.get("category", row.category),
        values.get("severity", row.severity),
    )
    for key, value in values.items():
        setattr(row, key, value)
    row.row_version = _advance_rule_version(db)
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return {"message": "Sensitive word updated", "word": row.to_dict()}

@moderation_router.delete("/sensitive-words/{word_id}")
@router.delete("/sensitive-words/{word_id}", include_in_schema=False)
def delete_sensitive_word(
    word_id: int,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    del user
    row = db.get(SensitiveWord, word_id)
    if row is None:
        raise HTTPException(404, detail="Sensitive word not found")
    _advance_rule_version(db)
    db.delete(row)
    db.commit()
    return {"message": "Sensitive word deleted"}
```

Wrap create/patch/delete commits with `except IntegrityError`: roll back and raise `HTTPException(409, detail={"code": "MODERATION_RULE_CONFLICT", "message": "规则已存在或发生冲突"})` without `str(exc)`. The concrete `_validate_metadata()` call occurs before changing the version, and each successful create, patch, or delete calls `_advance_rule_version()` exactly once in its transaction.

Register the canonical router:

```python
# D:\NanTuPy\app\main.py
app.include_router(admin.router, prefix="", tags=["Admin"])
app.include_router(admin.moderation_router)
```

- [ ] **Step 4: Remove sensitive content from legacy logs**

Use metadata-only events in both existing methods while retaining their public signatures for compatibility:

```python
# D:\NanTuPy\app\services\content_filter.py

def log_block(self, user_id, original_text: str, hit_words: list):
    del original_text
    logger.warning(
        "content_moderation_rejected user_id=%s content_type=legacy_filter rule_count=%s",
        user_id or "anonymous",
        len(hit_words),
    )

# D:\NanTuPy\app\services\content_moderation.py

@staticmethod
def log_moderation(user_id, content_type: str, original_text: str, reasons: list[str]):
    del original_text
    logger.warning(
        "content_moderation_rejected user_id=%s content_type=%s rule_count=%s",
        user_id or "anonymous",
        content_type,
        len(reasons),
    )
```

Also remove the debug event that interpolates `hit_words` and the add/remove events that interpolate the rule expression; log only action, actor ID when available, rule ID, and total count. Never pass body text, a preview, `hit_words`, `reasons`, regex text, credentials, signed URLs, or exception text to a logger. Keep compatibility methods only for callers outside business routers; later inventory tests prohibit route imports.

- [ ] **Step 5: Run administrator and privacy tests**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_admin_api.py tests/test_moderation_log_privacy.py -q
```

Expected: all tests pass; create, patch, and delete each increment the global version exactly once.

- [ ] **Step 6: Commit Task 5**

```bash
cd /d/NanTuPy
git add app/routers/admin.py app/main.py app/services/content_filter.py app/services/content_moderation.py tests/test_moderation_admin_api.py tests/test_moderation_log_privacy.py
git commit -m "feat: manage safe dynamic moderation rules"
```

### - [ ] Task 6: Enforce `is_active` across login, tokens, optional auth, roles, refresh, and WebSocket

**Files:**
- Modify: `D:\NanTuPy\app\core\auth_core.py`
- Modify: `D:\NanTuPy\app\dependencies.py`
- Modify: `D:\NanTuPy\app\routers\auth.py`
- Modify: `D:\NanTuPy\app\routers\ws.py`
- Create: `D:\NanTuPy\tests\test_inactive_auth.py`

- [ ] **Step 1: Write RED active-account tests**

```python
# D:\NanTuPy\tests\test_inactive_auth.py
import pytest
from app.core.auth_core import AuthError, require_active_user, verify_token
from app.dependencies import get_optional_user


def assert_disabled(response):
    assert response.status_code == 403
    assert response.json() == {
        "detail": {"code": "ACCOUNT_DISABLED", "message": "账号已停用", "retryable": False}
    }


def test_login_rejects_inactive_user(client, inactive_credentials):
    assert_disabled(client.post("/api/auth/login", json=inactive_credentials))


def test_existing_token_and_refresh_reject_after_deactivation(client, inactive_token):
    assert_disabled(client.get("/api/auth/me", params={"access_token": inactive_token}))
    assert_disabled(client.post("/api/auth/refresh", params={"access_token": inactive_token}))


def test_optional_auth_is_none_only_when_token_absent(client, inactive_token):
    assert client.get("/api/comic/events").status_code == 200
    assert_disabled(client.get("/api/comic/events", params={"access_token": inactive_token}))


def test_role_dependency_rejects_inactive_admin(client, inactive_admin_token):
    assert_disabled(client.get("/sensitive-words", params={"access_token": inactive_admin_token}))


def test_ws_auth_rejects_inactive_existing_token(ws_client, inactive_token):
    frame = ws_client.authenticate(inactive_token)
    assert frame["type"] == "auth_result"
    assert frame["success"] is False
    assert frame["status"] == 403
    assert frame["code"] == "ACCOUNT_DISABLED"
    assert frame["retryable"] is False
```

Fixtures must create active users, issue tokens, then set `is_active=False` in the database so these tests verify immediate revocation rather than token claims.

- [ ] **Step 2: Run inactive-account tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_inactive_auth.py -q
```

Expected: login and existing tokens are accepted, optional auth silently becomes anonymous, or WS lacks `ACCOUNT_DISABLED`.

- [ ] **Step 3: Centralize active-account enforcement and structured errors**

```python
# D:\NanTuPy\app\core\auth_core.py
class AuthError(Exception):
    def __init__(self, message: str, code: int = 401, error_code: str = "AUTH_INVALID"):
        self.message = message
        self.code = code
        self.error_code = error_code
        super().__init__(message)


def require_active_user(user: User) -> User:
    if user.is_active is not True:
        raise AuthError("账号已停用", 403, "ACCOUNT_DISABLED")
    return user

# In verify_token(), after the database lookup and before expunge/return:
return require_active_user(user)
```

Replace JWT exception detail with `AuthError("Token invalid or expired", 401, "AUTH_INVALID")`; never include JOSE exception text.

```python
# D:\NanTuPy\app\dependencies.py
def _auth_http_error(error: AuthError) -> HTTPException:
    detail = (
        {"code": error.error_code, "message": error.message, "retryable": False}
        if error.error_code == "ACCOUNT_DISABLED"
        else error.message
    )
    return HTTPException(status_code=error.code, detail=detail)

# get_current_user and require_role use: raise _auth_http_error(e)
# get_optional_user:
if not access_token:
    return None
try:
    return verify_token(access_token)
except AuthError as error:
    raise _auth_http_error(error)
```

In `login()`, call `require_active_user(user)` after the password check and before recording success or creating a token, converting its error with the same structured detail. `refresh` remains protected by `get_current_user`, so it cannot mint a new token for a disabled user.

In the WebSocket authentication branch, continue to use `verify_token()`. Convert `AuthError.error_code == "ACCOUNT_DISABLED"` into an `auth_result` containing `success=false`, `status=403`, `code="ACCOUNT_DISABLED"`, `retryable=false`, and `msg/message="账号已停用"`, then close the socket without joining rooms.

- [ ] **Step 4: Run active-account tests and authentication regressions**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_inactive_auth.py tests/test_ws_validation_unit.py tests/test_identity_role_contracts.py -q
```

Expected: all tests pass; optional endpoints remain anonymous only when no token was supplied.

- [ ] **Step 5: Commit Task 6**

```bash
cd /d/NanTuPy
git add app/core/auth_core.py app/dependencies.py app/routers/auth.py app/routers/ws.py tests/test_inactive_auth.py
git commit -m "fix: revoke disabled accounts across authentication"
```

### - [ ] Task 7: Moderate posts and ordinary comments on create and edit

**Files:**
- Modify: `D:\NanTuPy\app\routers\posts.py`
- Modify: `D:\NanTuPy\app\routers\interactions.py`
- Create: `D:\NanTuPy\tests\test_moderation_posts_comments.py`

- [ ] **Step 1: Write RED create/edit and old-value-preservation tests**

```python
# D:\NanTuPy\tests\test_moderation_posts_comments.py
import pytest
from app.services.moderation_errors import ContentRejected, ModerationUnavailable

@pytest.mark.parametrize("method,path,payload", [
    ("post", "/api/posts", {"content": "blocked create"}),
    ("put", "/api/posts/{post_id}", {"content": "blocked edit"}),
    ("post", "/api/posts/{post_id}/comments", {"content": "blocked comment"}),
    ("put", "/api/comments/{comment_id}", {"content": "blocked comment edit"}),
])
def test_write_path_returns_structured_rejection(
    authed_client, seeded_post_comment, rejecting_moderation, method, path, payload
):
    path = path.format(post_id=seeded_post_comment.post.id, comment_id=seeded_post_comment.comment.id)
    response = getattr(authed_client, method)(path, json=payload)
    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "CONTENT_REJECTED", "message": "内容未通过审核", "retryable": False
    }


def test_rejected_edits_leave_old_values_and_counts_unchanged(
    authed_client, db, seeded_post_comment, rejecting_moderation
):
    post, comment = seeded_post_comment.post, seeded_post_comment.comment
    old_post, old_comment = post.content, comment.content
    authed_client.put(f"/api/posts/{post.id}", json={"content": "blocked"})
    authed_client.put(f"/api/comments/{comment.id}", json={"content": "blocked"})
    db.expire_all()
    assert (post.content, comment.content) == (old_post, old_comment)


def test_moderator_failure_is_503_and_creates_nothing(authed_client, db, unavailable_moderation):
    before = db.execute(text("SELECT COUNT(*) FROM posts")).scalar_one()
    response = authed_client.post("/api/posts", json={"content": "harmless"})
    assert response.status_code == 503
    assert db.execute(text("SELECT COUNT(*) FROM posts")).scalar_one() == before
```

The moderation fixtures monkeypatch `app.routers.posts.moderation_service` and `app.routers.interactions.moderation_service` with deterministic fakes; they do not rely on production vocabulary.

- [ ] **Step 2: Run the write-path tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_posts_comments.py -q
```

Expected: creates still use legacy unstructured moderation and edits bypass the service.

- [ ] **Step 3: Insert the sole moderation entry before all ORM mutation**

Use this concrete pattern in both create and edit handlers, before constructing an ORM object or assigning `post.content`/`comment.content`:

```python
from app.services.moderation_errors import AppContractError, to_http_exception
from app.services.moderation_service import moderation_service
from app.services.moderation_types import ModerationContext


def _moderate_content(content: str | None, user_id: int, target_type: str) -> None:
    try:
        moderation_service.moderate_fields(
            {"content": content},
            ModerationContext(target_type=target_type, actor_user_id=user_id, is_public=True),
        )
    except AppContractError as error:
        raise to_http_exception(error)
```

Call with target types `post`, `post_edit`, `comment`, and `comment_edit`. Remove all direct `ContentModeration` imports, `filtered_text` replacement, hit-reason responses, and direct logger calls. Preserve the submitted text after validation; phase 1 rejects rather than silently rewriting it. For edits, moderate only when `"content" in payload`, and do it before any field assignment, topic relinking, mention processing, commit, notification, or count change.

- [ ] **Step 4: Run focused and existing post/comment regressions**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_posts_comments.py tests/test_comment_like_contract.py tests/test_post_visibility_contracts.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 7**

```bash
cd /d/NanTuPy
git add app/routers/posts.py app/routers/interactions.py tests/test_moderation_posts_comments.py
git commit -m "fix: moderate post and comment edits"
```

### - [ ] Task 8: Moderate HTTP and WebSocket direct/community messages with zero rejection side effects

**Files:**
- Modify: `D:\NanTuPy\app\routers\chat.py`
- Modify: `D:\NanTuPy\app\routers\communities.py`
- Modify: `D:\NanTuPy\app\routers\ws.py`
- Create: `D:\NanTuPy\tests\test_moderation_messages.py`

- [ ] **Step 1: Write RED tests for HTTP and WebSocket rejection atomicity**

```python
# D:\NanTuPy\tests\test_moderation_messages.py
import pytest
from sqlalchemy import text

SIDE_EFFECT_COUNTS = {
    "messages": "SELECT COUNT(*) FROM messages",
    "dedup": "SELECT COUNT(*) FROM ws_ack_dedup",
    "logs": "SELECT COUNT(*) FROM ws_message_log",
    "notifications": "SELECT COUNT(*) FROM notifications",
}


def counts(db):
    return {name: db.execute(text(sql)).scalar_one() for name, sql in SIDE_EFFECT_COUNTS.items()}

@pytest.mark.parametrize("path", [
    "/api/chat/conversations/{conversation_id}/messages",
    "/api/communities/{community_id}/chat/messages",
])
def test_http_rejection_has_no_persistence_or_fanout(
    authed_client, seeded_conversation, rejecting_moderation, ws_spy, db, path
):
    before = counts(db)
    response = authed_client.post(path.format(
        conversation_id=seeded_conversation.direct_id,
        community_id=seeded_conversation.community_id,
    ), json={"content": "blocked", "message_type": "text"})
    assert response.status_code == 422
    assert counts(db) == before
    assert ws_spy.events == []

@pytest.mark.parametrize("conversation_attr", ["direct_id", "community_conversation_id"])
@pytest.mark.asyncio
async def test_ws_rejection_is_failed_ack_and_has_no_side_effects(
    ws_handler, seeded_conversation, rejecting_moderation, ws_spy, db, conversation_attr
):
    before = counts(db)
    await ws_handler({
        "type": "send_message", "request_id": "req-7",
        "payload": {"client_msg_id": "client-7", "conversation_id": getattr(seeded_conversation, conversation_attr),
                    "content": "blocked", "message_type": "text"},
    })
    ack = ws_spy.raw[-1]
    assert ack == {
        "type": "ack", "request_id": "req-7",
        "client_msg_id": "client-7", "clientMsgId": "client-7",
        "status": 422, "code": "CONTENT_REJECTED", "retryable": False,
        "msg": "内容未通过审核", "message": "内容未通过审核",
    }
    assert counts(db) == before
    assert "message_id" not in ack

@pytest.mark.asyncio
async def test_ws_unavailable_ack_is_retryable_attempt_failure(
    ws_handler, seeded_conversation, unavailable_moderation, ws_spy
):
    await ws_handler({"type": "send_message", "request_id": "req-8", "payload": {
        "client_msg_id": "client-8", "conversation_id": seeded_conversation.direct_id,
        "content": "hello", "message_type": "text"}})
    assert ws_spy.raw[-1]["status"] == 503
    assert ws_spy.raw[-1]["code"] == "MODERATION_UNAVAILABLE"
    assert ws_spy.raw[-1]["retryable"] is True
```

Use actual table names from the current ORM when completing the fixture; capture `conversation.last_message_at`, push enqueue calls, cache invalidation, and notification calls in the before/spy assertions too.

- [ ] **Step 2: Run message tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_messages.py -q
```

Expected: HTTP creates a message or returns an unstructured error; WebSocket records dedup before rejection and emits a generic error rather than a failed ACK.

- [ ] **Step 3: Moderate normalized message text before persistence and HTTP side effects**

In `chat.send_message()` and `communities.send_community_message()`, call after payload normalization but before constructing the `Message` ORM object:

```python
try:
    moderation_service.moderate_fields(
        {"content": content},
        ModerationContext(
            target_type="community_message" if conversation.type == "community" else "direct_message",
            actor_user_id=user.id,
            is_public=conversation.type == "community",
        ),
    )
except AppContractError as error:
    raise to_http_exception(error)
```

The failure exits before `db.add`, `last_message_at`, `commit`, serialization, fanout, notification preview creation, push, and cache invalidation.

- [ ] **Step 4: Move WebSocket moderation before successful dedup reservation and emit the locked ACK**

Add:

```python
async def _send_failed_ack(user_id: int, request_id: str, client_msg_id: str, error: AppContractError):
    await ws_manager.send_raw(user_id, {
        "type": "ack", "request_id": request_id,
        "client_msg_id": client_msg_id, "clientMsgId": client_msg_id,
        "status": error.status_code, "code": error.error_code.value,
        "retryable": error.retryable,
        "msg": error.public_message, "message": error.public_message,
    })
```

In `_handle_send_message`, validate conversation access and normalize the payload inside `_persist`, but perform `moderation_service.moderate_fields()` before creating `Message`. More importantly, remove the pre-persistence `check_and_record_dedup()` call. Use this order:

```python
# 1 validate/authorize/normalize/moderate; return contract error without mutation
# 2 check existing successful dedup record; duplicates return the stored successful message_id
# 3 reserve/process and persist message + conversation timestamp in one successful path
# 4 record successful dedup with message_id only after commit
# 5 success ACK, sequence/fanout/notification/cache work
```

If moderation raises `AppContractError`, `_persist` returns `{"contract_error": error}`; the async caller invokes `_send_failed_ack(user_id, request_id, client_msg_id, error)` and returns. The rejection path must not call `check_and_record_dedup`, `update_dedup_message_id`, `send_with_seq`, `enqueue_push_batch`, `NotificationService`, or cache invalidation, and must not write `Message` or `WSMessageLog`.

- [ ] **Step 5: Run focused chat regressions**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_messages.py tests/test_chat_message_type_contracts.py tests/test_community_chat_contracts.py tests/test_ws_validation_unit.py -q
```

Expected: all tests pass; failed ACKs have no `message_id` and no rejection side effects.

- [ ] **Step 6: Commit Task 8**

```bash
cd /d/NanTuPy
git add app/routers/chat.py app/routers/communities.py app/routers/ws.py tests/test_moderation_messages.py
git commit -m "fix: reject unsafe chat before side effects"
```

### - [ ] Task 9: Moderate community metadata, applications, announcements, and governance reasons

**Files:**
- Modify: `D:\NanTuPy\app\routers\communities.py`
- Create: `D:\NanTuPy\tests\test_moderation_community_writes.py`

- [ ] **Step 1: Write RED parameterized community tests**

```python
# D:\NanTuPy\tests\test_moderation_community_writes.py
import pytest

@pytest.mark.parametrize("method,path,payload,fields", [
    ("post", "/api/communities", {"name":"blocked","description":"d","rules":"r"}, {"name","description","rules"}),
    ("patch", "/api/communities/{community_id}", {"description":"blocked"}, {"description"}),
    ("post", "/api/communities/{community_id}/join", {"message":"blocked"}, {"message"}),
    ("post", "/api/communities/{community_id}/announcements", {"title":"blocked","content":"body"}, {"title","content"}),
    ("patch", "/api/communities/{community_id}/announcements/{announcement_id}", {"content":"blocked"}, {"content"}),
    ("post", "/api/communities/{community_id}/bans", {"user_id":22,"reason":"blocked"}, {"reason"}),
])
def test_community_text_writes_are_checked_before_mutation(
    community_admin_client, seeded_community, moderation_spy, method, path, payload, fields
):
    moderation_spy.reject = True
    response = getattr(community_admin_client, method)(path.format(
        community_id=seeded_community.id, announcement_id=seeded_community.announcement_id
    ), json=payload)
    assert response.status_code == 422
    assert set(moderation_spy.last_fields) == fields
    seeded_community.assert_unchanged()
```

- [ ] **Step 2: Run community tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_community_writes.py -q
```

Expected: no route invokes the moderation spy and database values change for at least one route.

- [ ] **Step 3: Add explicit field batches before every community service/mutation call**

Use target types and public flags exactly as follows:

```python
checks = {
    "community_create": ({"name": payload.get("name"), "description": payload.get("description"), "rules": payload.get("rules")}, True),
    "community_edit": ({k: payload.get(k) for k in ("name", "description", "rules") if k in payload}, True),
    "community_join_request": ({"message": payload.get("message")}, False),
    "community_announcement": ({k: payload.get(k) for k in ("title", "content") if k in payload}, True),
    "community_ban": ({"reason": payload.get("reason")}, False),
}
```

Select the tuple by the exact key for that handler, then call:

```python
fields, is_public = checks[target_type]
try:
    moderation_service.moderate_fields(
        fields,
        ModerationContext(
            target_type=target_type,
            actor_user_id=user.id,
            is_public=is_public,
        ),
    )
except AppContractError as error:
    raise to_http_exception(error)
```

Use `target_type` values `community_create`, `community_edit`, `community_join_request`, `community_announcement`, and `community_ban` exactly as keyed above. Execute this before calling `CommunityService`, assigning ORM fields, committing, notifying, or sending a welcome/system message. Do not moderate auto-generated slugs or system welcome messages.

- [ ] **Step 4: Run community tests and service regressions**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_community_writes.py tests/test_community_service_sql.py tests/test_community_chat_contracts.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 9**

```bash
cd /d/NanTuPy
git add app/routers/communities.py tests/test_moderation_community_writes.py
git commit -m "feat: moderate community text writes"
```

### - [ ] Task 10: Moderate comic events and comic comments

**Files:**
- Modify: `D:\NanTuPy\app\routers\comic.py`
- Create: `D:\NanTuPy\tests\test_moderation_comic_writes.py`

- [ ] **Step 1: Write RED event create/edit and comment tests**

```python
# D:\NanTuPy\tests\test_moderation_comic_writes.py
import pytest

@pytest.mark.parametrize("method,path,payload,expected_fields", [
    ("post", "/api/comic/events", {
        "name":"blocked", "venue":"venue", "ticket_info":"ticket",
        "website":"https://fiction.invalid", "intro":"intro",
        "city_id":1, "start_date":"2026-08-01", "end_date":"2026-08-02",
    }, {"name","venue","ticket_info","website","intro"}),
    ("put", "/api/comic/events/{event_id}", {"intro":"blocked"}, {"intro"}),
    ("post", "/api/comic/events/{event_id}/comments", {"content":"blocked"}, {"content"}),
])
def test_comic_text_is_rejected_before_write(
    authed_client, seeded_comic, moderation_spy, method, path, payload, expected_fields
):
    moderation_spy.reject = True
    response = getattr(authed_client, method)(
        path.format(event_id=seeded_comic.id), json=payload
    )
    assert response.status_code == 422
    assert set(moderation_spy.last_fields) == expected_fields
    seeded_comic.assert_unchanged()
```

- [ ] **Step 2: Run comic tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_comic_writes.py -q
```

Expected: routes persist without calling moderation.

- [ ] **Step 3: Add moderation before event/comment mutations**

```python
# Create: include all five fields. Edit: include only supplied fields.
event_text = {
    key: payload.get(key)
    for key in ("name", "venue", "ticket_info", "website", "intro")
    if key in payload
}
moderation_service.moderate_fields(
    event_text,
    ModerationContext(target_type="comic_event", actor_user_id=user.id, is_public=True),
)
```

For comment creation use `{"content": payload.get("content")}` and target type `comic_comment`. Convert contract errors before creating `ComicEvent`/`ComicComment`, changing event fields/counts, committing, or notifying. There is no current comic-comment edit endpoint; do not invent one in phase 1.

- [ ] **Step 4: Run comic tests**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_comic_writes.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 10**

```bash
cd /d/NanTuPy
git add app/routers/comic.py tests/test_moderation_comic_writes.py
git commit -m "feat: moderate comic event text"
```

### - [ ] Task 11: Moderate public user and role identity text

**Files:**
- Modify: `D:\NanTuPy\app\routers\auth.py`
- Modify: `D:\NanTuPy\app\routers\roles.py`
- Modify: `D:\NanTuPy\app\routers\admin.py`
- Create: `D:\NanTuPy\tests\test_moderation_profile_role_writes.py`

- [ ] **Step 1: Write RED profile, registration, application, and role-profile tests**

```python
# D:\NanTuPy\tests\test_moderation_profile_role_writes.py
import pytest

@pytest.mark.parametrize("method,path,payload,fields", [
    ("post", "/api/auth/register", {"username":"blocked","email":"new@example.invalid","password":"Password9","email_code":"123456"}, {"username"}),
    ("put", "/api/auth/profile", {"display_name":"blocked","bio":"bio"}, {"display_name","bio"}),
    ("post", "/api/roles/apply", {"role_name":"coser","reason":"blocked","application_text":"a","contact_info":"c","extra_note":"n","portfolio_links":["https://fiction.invalid/work"]}, {"reason","application_text","contact_info","extra_note","portfolio_links[0]"}),
    ("put", "/api/roles/profiles/coser", {"cosname":"blocked","bio":"b","styles":"s","city":"c","social_links":"l"}, {"cosname","bio","styles","city","social_links"}),
    ("put", "/api/roles/profiles/photographer", {"equipment":"blocked","styles":"s","city":"c","social_links":"l"}, {"equipment","styles","city","social_links"}),
    ("put", "/api/roles/profiles/service", {"service_type":"blocked","description":"d","city":"c","price_info":"p"}, {"service_type","description","city","price_info"}),
])
def test_public_and_identity_text_uses_service_before_write(
    appropriate_client, moderation_spy, method, path, payload, fields
):
    moderation_spy.reject = True
    response = getattr(appropriate_client, method)(path, json=payload)
    assert response.status_code == 422
    assert set(moderation_spy.last_fields) == fields

@pytest.mark.parametrize("path", [
    "/api/roles/applications/1/approve", "/api/roles/applications/1/reject",
    "/api/roles/applications/1/suspend", "/role-applications/1/approve",
    "/role-applications/1/reject", "/role-applications/1/suspend",
])
def test_user_visible_review_comment_is_moderated(admin_client, moderation_spy, path):
    moderation_spy.reject = True
    response = admin_client.post(path, json={"review_comment": "blocked"})
    assert response.status_code == 422
    assert moderation_spy.last_fields == {"review_comment": "blocked"}
```

- [ ] **Step 2: Run profile/role tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_profile_role_writes.py -q
```

Expected: writes bypass moderation; `display_name` may still be ignored.

- [ ] **Step 3: Moderate exact fields before OTP consumption, assignment, commit, and email scheduling**

```python
# Register, before consuming OTP or constructing User:
moderation_service.moderate_fields(
    {"username": data.username},
    ModerationContext(target_type="user_registration", actor_user_id=None, is_public=True),
)

# Profile update, before assigning current fields:
profile_text = {
    key: value for key, value in {
        "display_name": data.display_name, "bio": data.bio,
    }.items() if value is not None
}
```

After moderation, make `display_name` functional by trimming it, applying the same 3–30-character/allowed-character validation used by registration, checking uniqueness excluding the current user, and assigning `current.username = display_name`. Continue HTML-escaping `bio` only after moderation. Avatar and cover URLs are not text-moderated in phase 1.

For identity applications, moderate `reason`, `application_text`, `contact_info`, `extra_note`, and every link as `{f"portfolio_links[{index}]": link for index, link in enumerate(data.portfolio_links)}` before creating `RoleApplication` or scheduling `_send_role_application_email`. For role profiles, use exactly the inventory field sets; `portfolio_images` and proof images are excluded, while user-authored `social_links` is checked as text. For both role-review implementations (`roles.py` and the legacy administrator routes in `admin.py`), moderate `review_comment` before status/role mutation and commit.

- [ ] **Step 4: Run profile/role and identity regressions**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_profile_role_writes.py tests/test_identity_role_contracts.py tests/test_email_privacy_contracts.py -q
```

Expected: all tests pass, rejected applications schedule no email, and profile rejection preserves old values.

- [ ] **Step 5: Commit Task 11**

```bash
cd /d/NanTuPy
git add app/routers/auth.py app/routers/roles.py app/routers/admin.py tests/test_moderation_profile_role_writes.py
git commit -m "feat: moderate profile and identity text"
```

### - [ ] Task 12: Moderate topics and report/governance text, then enforce the inventory mechanically

**Files:**
- Modify: `D:\NanTuPy\app\routers\topics.py`
- Modify: `D:\NanTuPy\app\routers\reports.py`
- Create: `D:\NanTuPy\tests\test_moderation_governance_writes.py`
- Create: `D:\NanTuPy\tests\test_moderation_phase1_write_paths.py`

- [ ] **Step 1: Write RED topic/report tests and source guardrails**

```python
# D:\NanTuPy\tests\test_moderation_governance_writes.py
import pytest

@pytest.mark.parametrize("method,path,payload,fields", [
    ("post", "/api/topics", {"name":"blocked","description":"d"}, {"name","description"}),
    ("put", "/api/topics/{topic_id}", {"description":"blocked"}, {"description"}),
    ("post", "/api/reports", {"type":"post","target_id":1,"reason":"blocked"}, {"reason"}),
    ("post", "/api/reports/post", {"post_id":1,"reason":"blocked"}, {"reason"}),
    ("post", "/api/reports/comment", {"comment_id":1,"reason":"blocked"}, {"reason"}),
    ("post", "/api/reports/user", {"user_id":2,"reason":"blocked"}, {"reason"}),
])
def test_topic_and_governance_text_rejects_before_mutation(
    appropriate_client, seeded_targets, moderation_spy, method, path, payload, fields
):
    moderation_spy.reject = True
    response = getattr(appropriate_client, method)(
        path.format(topic_id=seeded_targets.topic_id), json=payload
    )
    assert response.status_code == 422
    assert set(moderation_spy.last_fields) == fields
    seeded_targets.assert_unchanged()
```

```python
# D:\NanTuPy\tests\test_moderation_phase1_write_paths.py
from pathlib import Path
from app.services.moderation_inventory import MODERATED_TEXT_FIELDS

ROUTERS = Path(__file__).parents[1] / "app" / "routers"
BUSINESS_ROUTER_FILES = [
    "auth.py", "posts.py", "interactions.py", "chat.py", "ws.py",
    "communities.py", "comic.py", "roles.py", "topics.py", "reports.py",
]


def test_business_routers_do_not_import_legacy_filters():
    source = "\n".join((ROUTERS / name).read_text(encoding="utf-8") for name in BUSINESS_ROUTER_FILES)
    assert "content_moderation import ContentModeration" not in source
    assert "content_filter import" not in source


def test_inventory_has_no_duplicate_or_empty_field_sets():
    assert len(MODERATED_TEXT_FIELDS) == len(set(MODERATED_TEXT_FIELDS))
    assert all(fields and len(fields) == len(set(fields)) for fields in MODERATED_TEXT_FIELDS.values())


ROUTE_TEST_FILES = [
    "test_moderation_posts_comments.py",
    "test_moderation_messages.py",
    "test_moderation_community_writes.py",
    "test_moderation_comic_writes.py",
    "test_moderation_profile_role_writes.py",
    "test_moderation_governance_writes.py",
]


def test_each_inventory_route_has_a_rejection_test_marker():
    tests = "\n".join(
        (Path(__file__).parent / name).read_text(encoding="utf-8")
        for name in ROUTE_TEST_FILES
    )
    missing = sorted(route for route in MODERATED_TEXT_FIELDS if route not in tests)
    assert missing == []
```

Pass the exact route strings as the `ids` list in parameterized tests from Tasks 7–12 so the final guard can find them. Include `WS send_message` as the ID of both direct and community WebSocket rejection cases.

- [ ] **Step 2: Run governance and inventory tests and verify RED**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_governance_writes.py tests/test_moderation_phase1_write_paths.py -q
```

Expected: topics/reports bypass moderation and/or a legacy filter import remains.

- [ ] **Step 3: Add topic and report moderation before business logic**

```python
# Topic create
fields = {"name": name, "description": description}
# Topic edit
fields = {"description": payload["description"]} if "description" in payload else {}
# Every report alias delegates to _handle_report, so moderate once there:
fields = {"reason": reason}
```

Use target types `topic`, `topic_edit`, and `report_reason`, all with the authenticated actor ID. Topic fields are public; report reason is non-public governance text. Run moderation before duplicate-topic/report lookups that return success, before creating rows, and before changing an existing topic. This phase only filters the existing report reason; it does not expand target types, create cases, or add resolution workflow.

- [ ] **Step 4: Run all backend phase-1 focused tests**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest \
  tests/test_moderation_phase1_contracts.py \
  tests/test_local_text_moderator.py \
  tests/test_moderation_snapshot.py \
  tests/test_moderation_migration.py \
  tests/test_moderation_admin_api.py \
  tests/test_moderation_log_privacy.py \
  tests/test_inactive_auth.py \
  tests/test_moderation_posts_comments.py \
  tests/test_moderation_messages.py \
  tests/test_moderation_community_writes.py \
  tests/test_moderation_comic_writes.py \
  tests/test_moderation_profile_role_writes.py \
  tests/test_moderation_governance_writes.py \
  tests/test_moderation_phase1_write_paths.py -q
```

Expected: all phase-1 tests pass with no skipped inventory route.

- [ ] **Step 5: Commit Task 12**

```bash
cd /d/NanTuPy
git add app/routers/topics.py app/routers/reports.py tests/test_moderation_governance_writes.py tests/test_moderation_phase1_write_paths.py
git commit -m "feat: close remaining text moderation paths"
```

### - [ ] Task 13: Parse structured HTTP errors and stop refresh for disabled Flutter accounts

**Files:**
- Modify: `D:\FlutterProject\nonto\lib\services\api\api_client.dart`
- Create: `D:\FlutterProject\nonto\test\moderation_api_error_test.dart`
- Create: `D:\FlutterProject\nonto\test\account_disabled_interceptor_test.dart`

- [ ] **Step 1: Write RED `ApiResponse` parsing tests**

```dart
// D:\FlutterProject\nonto\test\moderation_api_error_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:nonto/services/api/api_client.dart';

void main() {
  test('parses FastAPI structured detail', () {
    final response = ApiClient.parseErrorResponse(
      statusCode: 422,
      data: {'detail': {'code': 'CONTENT_REJECTED', 'message': '内容未通过审核', 'retryable': false}},
    );
    expect(response.success, isFalse);
    expect(response.errorCode, 'CONTENT_REJECTED');
    expect(response.message, '内容未通过审核');
    expect(response.isRetryable, isFalse);
  });

  test('moderation unavailable is retryable', () {
    final response = ApiClient.parseErrorResponse(
      statusCode: 503,
      data: {'detail': {'code': 'MODERATION_UNAVAILABLE', 'message': '内容审核服务暂不可用，请稍后重试', 'retryable': true}},
    );
    expect(response.errorCode, 'MODERATION_UNAVAILABLE');
    expect(response.isRetryable, isTrue);
  });

  test('disabled account is a terminal 403 failure', () {
    final response = ApiClient.parseErrorResponse(
      statusCode: 403,
      data: {'detail': {'code': 'ACCOUNT_DISABLED', 'message': '账号已停用', 'retryable': false}},
    );
    expect(response.errorCode, 'ACCOUNT_DISABLED');
    expect(response.isRetryable, isFalse);
  });

  test('legacy string and pydantic list details stay compatible', () {
    expect(ApiClient.parseErrorResponse(statusCode: 400, data: {'detail': 'bad'}).message, 'bad');
    expect(ApiClient.parseErrorResponse(statusCode: 422, data: {'detail': [{'msg': 'invalid'}]}).message, 'invalid');
  });
}
```

```dart
// D:\FlutterProject\nonto\test\account_disabled_interceptor_test.dart
// Inject a Dio adapter that returns 403 detail.code=ACCOUNT_DISABLED and retryable=false.
// Count refresh adapter calls and onTokenExpired calls.
test('ACCOUNT_DISABLED clears session without refresh', () async {
  final harness = AccountDisabledDioHarness();
  await harness.client.get('/auth/me');
  expect(harness.refreshCalls, 0);
  expect(harness.tokenExpiredCalls, 1);
  expect(harness.loggedOutTransitions, 1);
  expect(ApiClient.token, isNull);
  expect(harness.prefs.containsKey('access_token'), isFalse);
});
```

Implement the harness in the test file with a fake `HttpClientAdapter`, injected Dio/refresh Dio, in-memory SharedPreferences initial values, and a reset in `tearDown`; it must not make network calls.

- [ ] **Step 2: Run Flutter API tests and verify RED**

Run:

```bash
cd /d/FlutterProject/nonto
flutter test test/moderation_api_error_test.dart test/account_disabled_interceptor_test.dart
```

Expected: compile fails because `errorCode`, `isRetryable`, `parseErrorResponse`, and test injection are absent.

- [ ] **Step 3: Add typed error fields, one parser, and disabled-account interception**

```dart
class ApiErrorCodes {
  static const contentRejected = 'CONTENT_REJECTED';
  static const moderationUnavailable = 'MODERATION_UNAVAILABLE';
  static const accountDisabled = 'ACCOUNT_DISABLED';
}

class ApiResponse<T> {
  final bool success;
  final T? data;
  final String? message;
  final int? statusCode;
  final String? errorCode;
  final bool isRetryable;

  const ApiResponse({
    required this.success,
    this.data,
    this.message,
    this.statusCode,
    this.errorCode,
    this.isRetryable = false,
  });
}
```

Add `ApiClient.parseErrorResponse<T>({required int? statusCode, dynamic data, String? fallbackMessage})`. If `detail` is a map, read `code`, `message`, and boolean `retryable`; retain string/list/top-level-message behavior. For the three locked codes, enforce the contract even if malformed server input disagrees: `CONTENT_REJECTED=false`, `MODERATION_UNAVAILABLE=true`, and `ACCOUNT_DISABLED=false`. For other codes, use a boolean `detail.retryable` when present, otherwise treat an absent response or status 500–599 as retryable. Make `_handleError` delegate to this parser.

At the top of the Dio error interceptor, parse every response before status-specific handling. If the code is `ACCOUNT_DISABLED` (locked HTTP 403), call the existing session-clear path, close WebSocket via the token-clear hook, invoke `onTokenExpired` exactly once so navigation enters the logged-out flow, and `handler.next(error)` without `_doRefreshToken()`. Only ordinary 401 authentication failures enter refresh. Add constructor/test hooks for Dio, refresh Dio, preferences access, and logout transition observation while preserving singleton defaults. Redact debug logs: log path, status, code, and exception type only; never log token, request payload, response body, or raw server exception.

- [ ] **Step 4: Run API parsing and disabled-account tests**

Run:

```bash
cd /d/FlutterProject/nonto
flutter test test/moderation_api_error_test.dart test/account_disabled_interceptor_test.dart
```

Expected: all tests pass; refresh call count remains zero for `ACCOUNT_DISABLED`.

- [ ] **Step 5: Commit Task 13**

```bash
cd /d/FlutterProject/nonto
git add lib/services/api/api_client.dart test/moderation_api_error_test.dart test/account_disabled_interceptor_test.dart
git commit -m "fix: parse moderation and disabled account errors"
```

### - [ ] Task 14: Propagate structured failures and roll back Flutter HTTP mutations

**Files:**
- Modify: `D:\FlutterProject\nonto\lib\services\api\post_service.dart`
- Modify: `D:\FlutterProject\nonto\lib\services\api\comment_service.dart`
- Modify: `D:\FlutterProject\nonto\lib\services\api\chat_service.dart`
- Modify: `D:\FlutterProject\nonto\lib\services\api\community_service.dart`
- Modify: `D:\FlutterProject\nonto\lib\services\comic_service.dart`
- Modify: `D:\FlutterProject\nonto\lib\services\api\auth_service.dart`
- Modify: `D:\FlutterProject\nonto\lib\services\api\role_service.dart`
- Modify: `D:\FlutterProject\nonto\lib\services\api\topic_service.dart`
- Modify: `D:\FlutterProject\nonto\lib\services\api\report_service.dart`
- Modify: `D:\FlutterProject\nonto\lib\providers\comment_notifier.dart`
- Modify: `D:\FlutterProject\nonto\lib\providers\auth_notifier.dart`
- Modify: `D:\FlutterProject\nonto\lib\providers\community_notifier.dart`
- Modify: `D:\FlutterProject\nonto\lib\screens\post\create_post_screen.dart`
- Modify: `D:\FlutterProject\nonto\lib\widgets\comment_section.dart`
- Modify: `D:\FlutterProject\nonto\lib\screens\community\community_create_screen.dart`
- Modify: `D:\FlutterProject\nonto\lib\screens\community\community_manage_screen.dart`
- Modify: `D:\FlutterProject\nonto\lib\screens\community\community_detail_screen.dart`
- Modify: `D:\FlutterProject\nonto\lib\screens\community\community_chat_screen.dart`
- Modify: `D:\FlutterProject\nonto\lib\screens\comic\comic_upload_page.dart`
- Modify: `D:\FlutterProject\nonto\lib\screens\comic\comic_detail_page.dart`
- Modify: `D:\FlutterProject\nonto\lib\screens\profile\edit_profile_screen.dart`
- Modify: `D:\FlutterProject\nonto\lib\screens\profile\identity_application_screen.dart`
- Modify: `D:\FlutterProject\nonto\lib\screens\topics\my_topics_screen.dart`
- Modify: `D:\FlutterProject\nonto\lib\screens\post\post_detail_screen.dart`
- Modify: `D:\FlutterProject\nonto\lib\screens\profile\user_profile_screen.dart`
- Modify: `D:\FlutterProject\nonto\lib\widgets\post_share_to_chat_sheet.dart`
- Create: `D:\FlutterProject\nonto\test\moderation_http_rollback_test.dart`

- [ ] **Step 1: Write RED rollback and standardized-message tests**

```dart
// D:\FlutterProject\nonto\test\moderation_http_rollback_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:nonto/services/api/api_client.dart';

ApiResponse rejected() => const ApiResponse(
  success: false, statusCode: 422, errorCode: 'CONTENT_REJECTED',
  message: '内容未通过审核', isRetryable: false,
);
ApiResponse unavailable() => const ApiResponse(
  success: false, statusCode: 503, errorCode: 'MODERATION_UNAVAILABLE',
  message: '内容审核服务暂不可用，请稍后重试', isRetryable: true,
);

void main() {
  test('comment optimistic insert rolls back and preserves code/message', () async {
    final notifier = commentHarness(response: rejected());
    final before = notifier.state.comments;
    final message = await notifier.submitComment('fictional text');
    expect(notifier.state.comments, before);
    expect(notifier.state.isSending, isFalse);
    expect(message, '内容未通过审核');
  });

  testWidgets('post edit rejection keeps original model and draft', (tester) async {
    final harness = await pumpPostEditHarness(tester, response: rejected());
    await harness.submit('new text');
    expect(harness.visiblePostText, 'old text');
    expect(harness.draftText, 'new text');
    expect(find.text('内容未通过审核'), findsOneWidget);
  });

  testWidgets('community comic profile identity topic and report failures restore UI', (tester) async {
    for (final harness in httpMutationHarnesses(response: unavailable())) {
      await harness.pump(tester);
      final before = harness.snapshot();
      await harness.submit();
      expect(harness.snapshot(), before);
      expect(find.text('内容审核服务暂不可用，请稍后重试'), findsOneWidget);
    }
  });
}
```

Build concrete fake-service harnesses in this file for create/edit post, normal/comic comment, HTTP private-message fallback/post sharing, community create/edit/join/announcement/ban/HTTP chat, comic event, profile, identity application, topic, and report. Each harness returns an `ApiResponse` rather than throwing and captures state before submission.

- [ ] **Step 2: Run rollback tests and verify RED**

Run:

```bash
cd /d/FlutterProject/nonto
flutter test test/moderation_http_rollback_test.dart
```

Expected: one or more services throw away `errorCode`, return success despite `resp.success == false`, or leave optimistic state changed.

- [ ] **Step 3: Preserve `ApiResponse` and centralize user-facing moderation messages**

Do not catch an unsuccessful `ApiResponse` and replace it with a boolean or generic exception. Add this helper to `api_client.dart`:

```dart
String apiFailureMessage(ApiResponse response, {required String fallback}) {
  switch (response.errorCode) {
    case ApiErrorCodes.contentRejected:
      return '内容未通过审核';
    case ApiErrorCodes.moderationUnavailable:
      return '内容审核服务暂不可用，请稍后重试';
    case ApiErrorCodes.accountDisabled:
      return '账号已停用';
    default:
      return response.message?.trim().isNotEmpty == true ? response.message! : fallback;
  }
}
```

For each listed service, return the original `ApiResponse`. For each provider/screen:

```dart
final before = currentState;
applyOptimisticChange();
final response = await serviceMutation();
if (!response.success) {
  restore(before);
  showError(apiFailureMessage(response, fallback: '操作失败，请重试'));
  return;
}
applyServerResult(response.data);
```

Post/comment edit rejection preserves the original displayed server model and the user's draft. Community HTTP chat must not append a map unless `resp.success` is true. Report dialogs and identity/profile/topic/event/community forms remain populated after failure. Do not display hit terms, expressions, thresholds, or raw exceptions.

- [ ] **Step 4: Run rollback tests and existing affected UI regressions**

Run:

```bash
cd /d/FlutterProject/nonto
flutter test \
  test/moderation_http_rollback_test.dart \
  test/nonto_create_post_phase5a_regression_test.dart \
  test/nonto_edit_profile_phase4d_regression_test.dart \
  test/nonto_community_detail_apply_regression_test.dart \
  test/nonto_community_phase6b_regression_test.dart \
  test/role_identity_contract_test.dart
```

Expected: all tests pass; no failed mutation remains optimistically visible as successful.

- [ ] **Step 5: Commit Task 14**

```bash
cd /d/FlutterProject/nonto
git add lib/services/api lib/services/comic_service.dart lib/providers/comment_notifier.dart lib/providers/auth_notifier.dart lib/providers/community_notifier.dart lib/screens/post/create_post_screen.dart lib/widgets/comment_section.dart lib/widgets/post_share_to_chat_sheet.dart lib/screens/community lib/screens/comic lib/screens/profile/edit_profile_screen.dart lib/screens/profile/identity_application_screen.dart lib/screens/topics/my_topics_screen.dart lib/screens/post/post_detail_screen.dart lib/screens/profile/user_profile_screen.dart test/moderation_http_rollback_test.dart
git commit -m "fix: roll back rejected text mutations"
```

### - [ ] Task 15: Make `reliable_websocket` settle non-200 ACKs as typed terminal failures

**Files:**
- Create: `D:\FlutterProject\nonto\packages\reliable_websocket\lib\src\models\send_failure.dart`
- Modify: `D:\FlutterProject\nonto\packages\reliable_websocket\lib\reliable_websocket.dart`
- Modify: `D:\FlutterProject\nonto\packages\reliable_websocket\lib\src\protocol\message.dart`
- Modify: `D:\FlutterProject\nonto\packages\reliable_websocket\lib\src\client.dart`
- Modify: `D:\FlutterProject\nonto\packages\reliable_websocket\lib\src\sender\reliable_sender.dart`
- Modify: `D:\FlutterProject\nonto\packages\reliable_websocket\lib\src\outbox\outbox_manager.dart`
- Modify: `D:\FlutterProject\nonto\packages\reliable_websocket\lib\src\database\database.dart`
- Modify generated: `D:\FlutterProject\nonto\packages\reliable_websocket\lib\src\database\database.g.dart`
- Create: `D:\FlutterProject\nonto\packages\reliable_websocket\test\failed_ack_test.dart`

- [ ] **Step 1: Write RED package tests for flattened/payload ACKs and terminal outbox settlement**

```dart
// D:\FlutterProject\nonto\packages\reliable_websocket\test\failed_ack_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:reliable_websocket/reliable_websocket.dart';

void main() {
  for (final frame in [
    {'type':'ack','client_msg_id':'c1','clientMsgId':'c1','status':422,
     'code':'CONTENT_REJECTED','retryable':false,
     'msg':'内容未通过审核','message':'内容未通过审核'},
    {'type':'ack','payload':{'client_msg_id':'c1','status':503,
     'code':'MODERATION_UNAVAILABLE','retryable':true,
     'msg':'内容审核服务暂不可用，请稍后重试'}},
  ]) {
    test('non-200 ACK decodes typed failure without message id', () {
      final decoded = ProtocolFrame.fromJson(frame);
      final failure = decoded.ackFailure;
      expect(failure, isNotNull);
      expect(failure!.clientMsgId, 'c1');
      expect(failure.retryable, failure.code == 'MODERATION_UNAVAILABLE');
      expect(decoded.ackMessageId, isNull);
    });
  }

  test('failed ACK cancels timer, marks outbox failed, and never resends', () async {
    final harness = await SenderHarness.create();
    final id = await harness.send({'content':'fictional'});
    await harness.deliverAck({'type':'ack','client_msg_id':id,'status':422,
      'code':'CONTENT_REJECTED','msg':'内容未通过审核'});
    await harness.elapseAckTimeouts(4);
    expect(await harness.outboxStatus(id), 'failed');
    expect(harness.sendCount(id), 1);
    expect(harness.failures.single.code, 'CONTENT_REJECTED');
    expect(harness.successes, isEmpty);
  });
}
```

Implement `SenderHarness` with the package's injected in-memory Drift executor, fake connection, short timers, and public/test-visible frame dispatch; do not use a live socket.

- [ ] **Step 2: Run package tests and verify RED**

Run:

```bash
cd /d/FlutterProject/nonto/packages/reliable_websocket
flutter test test/failed_ack_test.dart
```

Expected: compile fails because `SendFailure`, `ackFailure`, and typed callbacks do not exist; current client treats every ACK as success.

- [ ] **Step 3: Define and export the typed failure**

```dart
// D:\FlutterProject\nonto\packages\reliable_websocket\lib\src\models\send_failure.dart
class SendFailure {
  final String clientMsgId;
  final int status;
  final String code;
  final String message;
  final bool retryable;

  const SendFailure({
    required this.clientMsgId,
    required this.status,
    required this.code,
    required this.message,
    required this.retryable,
  });
}
```

Export it and change `MessageFailedHandler`/`OnMessageFailed` to `void Function(SendFailure failure)` throughout package constructors/config.

Add safe numeric/string getters to `ProtocolFrame`:

```dart
String? get ackClientMsgId =>
    (payload?['client_msg_id'] ?? payload?['clientMsgId'] ??
     rawJson?['client_msg_id'] ?? rawJson?['clientMsgId'])?.toString();
int get ackStatus => int.tryParse('${payload?['status'] ?? rawJson?['status'] ?? 200}') ?? 200;
String? get ackCode => (payload?['code'] ?? rawJson?['code'])?.toString();
bool? get ackRetryable {
  final value = payload?['retryable'] ?? rawJson?['retryable'];
  return value is bool ? value : null;
}
SendFailure? get ackFailure {
  final id = ackClientMsgId;
  if (id == null || (ackStatus >= 200 && ackStatus < 300)) return null;
  final code = ackCode ?? 'SEND_FAILED';
  final message = (payload?['msg'] ?? payload?['message'] ?? rawJson?['msg'] ?? rawJson?['message'] ?? '发送失败').toString();
  final retryable = switch (code) {
    'CONTENT_REJECTED' || 'ACCOUNT_DISABLED' => false,
    'MODERATION_UNAVAILABLE' => true,
    _ => ackRetryable ?? false,
  };
  return SendFailure(
    clientMsgId: id, status: ackStatus, code: code, message: message,
    retryable: retryable,
  );
}
```

Ensure `_extractLegacyPayload` copies both `client_msg_id` and `clientMsgId`, plus `code`, `retryable`, `msg`, and `message`, into the ACK payload fallback.

- [ ] **Step 4: Settle a failed ACK without retries**

```dart
// ReliableSender
Future<void> onFailedAck(SendFailure failure) async {
  final settled = await _outbox.markPendingFailed(failure.clientMsgId);
  if (!settled) return;
  _ackTimers[failure.clientMsgId]?.cancel();
  _ackTimers.remove(failure.clientMsgId);
  _onFailed?.call(failure);
}

// ReliableWebSocketClient._onAck
final failure = frame.ackFailure;
if (failure != null) {
  await _sender.onFailedAck(failure);
  return;
}
final clientMsgId = frame.ackClientMsgId;
if (clientMsgId != null) await _sender.onAck(clientMsgId);
```

Implement `OutboxManager.markPendingFailed()` through a new `AppDatabase.markPendingFailed()` custom update: `UPDATE outbox SET status='failed' WHERE client_msg_id=? AND status='pending'`, returning `statementUpdates > 0`. This makes duplicate failed ACKs idempotent and ensures the callback fires once. Keep the failed row for diagnostics/manual cleanup; `getPendingMessages()` and reconnect resend query only `pending`, so it cannot automatically retransmit. The package schema remains version 1 because no columns change; `database.g.dart` is regenerated only to confirm generated code remains synchronized after DAO edits.

- [ ] **Step 5: Generate package Drift code and run package tests**

Run:

```bash
cd /d/FlutterProject/nonto/packages/reliable_websocket
dart run build_runner build --delete-conflicting-outputs
flutter test
```

Expected: all package tests pass; failed ACK send count remains one after timer/reconnect simulation.

- [ ] **Step 6: Commit Task 15**

```bash
cd /d/FlutterProject/nonto
git add packages/reliable_websocket/lib packages/reliable_websocket/test/failed_ack_test.dart
git commit -m "fix: settle websocket failed acknowledgements"
```

### - [ ] Task 16: Propagate typed failed ACKs through WebSocket service and `ChatSendQueue`

**Files:**
- Modify: `D:\FlutterProject\nonto\lib\services\websocket_service.dart`
- Modify: `D:\FlutterProject\nonto\lib\services\chat_send_queue.dart`
- Modify: `D:\FlutterProject\nonto\lib\providers\chat_notifiers.dart`
- Create: `D:\FlutterProject\nonto\test\moderation_chat_queue_test.dart`

- [ ] **Step 1: Write RED matching, early-NACK, retryability, and queue-progress tests**

```dart
// D:\FlutterProject\nonto\test\moderation_chat_queue_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:nonto/services/websocket_service.dart';

const rejected = ChatSendFailure(
  clientMsgId: 'c1', code: 'CONTENT_REJECTED',
  message: '内容未通过审核', retryable: false,
);
const unavailable = ChatSendFailure(
  clientMsgId: 'c1', code: 'MODERATION_UNAVAILABLE',
  message: '内容审核服务暂不可用，请稍后重试', retryable: true,
);

void main() {
  test('failed ACK settles only matching id and drains next message', () async {
    final harness = ChatQueueHarness(twoMessages: true);
    await harness.start();
    expect(harness.queue.handleSendFailure(rejected), isTrue);
    expect(harness.first.failureCode, 'CONTENT_REJECTED');
    expect(harness.first.canRetry, isFalse);
    expect(harness.second.status, 'sending');
  });

  test('moderation unavailable permits manual retry but never automatic resend', () async {
    final harness = ChatQueueHarness();
    await harness.start();
    harness.queue.handleSendFailure(unavailable);
    await harness.elapseNetworkTimers();
    expect(harness.sendCalls, 1);
    expect(harness.first.canRetry, isTrue);
  });

  test('failed ACK arriving before client id assignment is buffered', () async {
    final harness = ChatQueueHarness(delaySendResult: true);
    final start = harness.start();
    harness.deliverFailure(const ChatSendFailure(
      clientMsgId:'early', code:'CONTENT_REJECTED', message:'内容未通过审核', retryable:false));
    harness.completeSendResult('early');
    await start;
    expect(harness.first.status, 'failed');
    expect(harness.queue.pendingCount, 0);
  });
}
```

- [ ] **Step 2: Run queue tests and verify RED**

Run:

```bash
cd /d/FlutterProject/nonto
flutter test test/moderation_chat_queue_test.dart
```

Expected: compile fails because callbacks and queue accept strings/maps and there is no early-failure buffer.

- [ ] **Step 3: Add app-level typed failure and preserve every field**

```dart
class ChatSendFailure {
  final String clientMsgId;
  final String code;
  final String message;
  final bool retryable;

  const ChatSendFailure({
    required this.clientMsgId, required this.code,
    required this.message, required this.retryable,
  });
}
```

Change `WebSocketClientCallbacks.onMessageFailed` to receive package `SendFailure`. In `_ReliableWebSocketClientAdapter`, map package callback directly. Change `_sendErrorController` to `StreamController<ChatSendFailure>.broadcast()` and expose `Stream<ChatSendFailure>`. Generic `error` frames may create `code='SEND_FAILED'`, `retryable=false` only when they include a non-empty client ID; moderation failed ACKs come through `onMessageFailed`, not `onError`.

- [ ] **Step 4: Make queue settlement exact and race-safe**

Change queue callback to:

```dart
void Function(int optimisticMsgId, ChatSendFailure failure)? onFailed;
final Map<String, ChatSendFailure> _earlyFailures = {};
```

Implement `handleSendFailure(ChatSendFailure failure)` to match `_current.message.clientMsgId` or a waiting entry's exact ID, cancel only that entry, persist `status='failed'`, `failureCode`, `failureMessage`, and `retryable`, call `onFailed`, and drain the next FIFO item. If `_current` exists with null `clientMsgId`, buffer by `failure.clientMsgId`. Immediately after assigning the returned ID in `_processNext`, consume `_earlyFailures[id]` before `_earlyAcks[id]`; a terminal failure wins and removes a stale success ACK for the same ID. Neither failure code triggers automatic `sendMessage`; the persisted `retryable` value controls only whether UI offers manual retry.

Update `MessagesNotifier._onSendError` and `_onQueueFailed` to accept `ChatSendFailure`, update only the exact local message, persist its code/message/retryable fields, and set `isSending=false` without altering another queued message.

- [ ] **Step 5: Run queue and chat reliability tests**

Run:

```bash
cd /d/FlutterProject/nonto
flutter test test/moderation_chat_queue_test.dart test/chat_reliability_regression_test.dart test/nonto_chat_phase2b_regression_test.dart
```

Expected: all tests pass; a rejected first message does not block or fail the second.

- [ ] **Step 6: Commit Task 16**

```bash
cd /d/FlutterProject/nonto
git add lib/services/websocket_service.dart lib/services/chat_send_queue.dart lib/providers/chat_notifiers.dart test/moderation_chat_queue_test.dart
git commit -m "fix: propagate typed chat send failures"
```

### - [ ] Task 17: Own the sole Drift v1→v2 migration, persist all chat failure semantics, and update UI

**Files:**
- Modify: `D:\FlutterProject\nonto\lib\models\message.dart`
- Modify: `D:\FlutterProject\nonto\lib\services\database\app_database.dart`
- Modify generated: `D:\FlutterProject\nonto\lib\services\database\app_database.g.dart`
- Modify: `D:\FlutterProject\nonto\lib\services\local_db_service.dart`
- Modify: `D:\FlutterProject\nonto\lib\providers\chat_notifiers.dart`
- Modify: `D:\FlutterProject\nonto\lib\screens\chat\chat_room_screen.dart`
- Modify: `D:\FlutterProject\nonto\lib\screens\community\community_chat_screen.dart`
- Create: `D:\FlutterProject\nonto\test\moderation_drift_migration_test.dart`
- Create: `D:\FlutterProject\nonto\test\moderation_chat_failure_ui_test.dart`

- [ ] **Step 1: Write RED model round-trip and v1-to-v2 migration tests**

```dart
// D:\FlutterProject\nonto\test\moderation_drift_migration_test.dart
import 'package:drift/native.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nonto/services/database/app_database.dart';

void main() {
  test('v1 database upgrades without deleting old messages', () async {
    final executor = NativeDatabase.memory();
    await createVersionOneSchema(executor);
    await executor.runInsert(
      "INSERT INTO messages_table (id,conversation_id,sender_id,content,message_type,is_read,status,is_recalled) VALUES (1,2,3,'old','text',0,'sent',0)",
      const [],
    );
    final db = AppDatabase.forTesting(executor);
    expect(db.schemaVersion, 2);
    final rows = await db.getMessages(2);
    expect(rows.single.content, 'old');
    expect(rows.single.failureCode, isNull);
    expect(rows.single.failureMessage, isNull);
    expect(rows.single.retryable, isNull);
    await db.insertMessage(MessagesTableCompanion.insert(
      id: 4, conversationId: 2, senderId: 3,
      failureCode: const Value('CONTENT_REJECTED'),
      failureMessage: const Value('内容未通过审核'),
      retryable: const Value(false),
    ));
    final failed = (await db.getMessages(2)).first;
    expect(failed.failureCode, 'CONTENT_REJECTED');
    expect(failed.failureMessage, '内容未通过审核');
    expect(failed.retryable, isFalse);
    await db.close();
  });
}
```

Expose `AppDatabase.forTesting(QueryExecutor)` and put the exact v1 `CREATE TABLE` statements in `createVersionOneSchema`; include all v1 columns so Drift executes a real migration.

```dart
// D:\FlutterProject\nonto\test\moderation_chat_failure_ui_test.dart
testWidgets('rejected message shows reason and no retry button', (tester) async {
  await pumpFailedMessage(
    tester, code: 'CONTENT_REJECTED', message: '内容未通过审核', retryable: false,
  );
  expect(find.text('内容未通过审核'), findsOneWidget);
  expect(find.text('重试'), findsNothing);
});

testWidgets('unavailable message offers manual retry', (tester) async {
  await pumpFailedMessage(
    tester, code: 'MODERATION_UNAVAILABLE',
    message: '内容审核服务暂不可用，请稍后重试', retryable: true,
  );
  expect(find.text('内容审核服务暂不可用，请稍后重试'), findsOneWidget);
  expect(find.text('重试'), findsOneWidget);
});
```

- [ ] **Step 2: Run migration/UI tests and verify RED**

Run:

```bash
cd /d/FlutterProject/nonto
flutter test test/moderation_drift_migration_test.dart test/moderation_chat_failure_ui_test.dart
```

Expected: compile fails because failure columns/properties and `forTesting` do not exist; current UI always shows retry for failed text.

- [ ] **Step 3: Add all three nullable failure fields and the sole migration branch 2**

```dart
// Message
final String? failureCode;
final String? failureMessage;
final bool? retryable;
bool get canRetry => status == 'failed' && retryable == true;
```

Add constructor, `fromJson`/`toJson` mappings for `failure_code`, `failure_message`, and `retryable`, plus `copyWith` support for all three. A successful/sending copy clears all stale failure values via explicit `clearFailure` support. For a locked code, persist its received contract value and validate `CONTENT_REJECTED=false`, `MODERATION_UNAVAILABLE=true`, and `ACCOUNT_DISABLED=false` at the protocol/parser boundary rather than re-deriving retryability from status or score.

```dart
// MessagesTable
TextColumn get failureCode => text().nullable()();
TextColumn get failureMessage => text().nullable()();
BoolColumn get retryable => boolean().nullable()();

// AppDatabase
@override
int get schemaVersion => 2;

onUpgrade: (Migrator m, int from, int to) async {
  if (from < 2) {
    await m.addColumn(messagesTable, messagesTable.failureCode);
    await m.addColumn(messagesTable, messagesTable.failureMessage);
    await m.addColumn(messagesTable, messagesTable.retryable);
  }
  await _writeMeta(metaKeySchemaVersion, to.toString());
},
```

Phase 1 exclusively owns this v1→v2 branch. Do not alter the published v1 schema, wipe tables, remove old messages, or defer any of these three columns to Phase 2. Phase 2 starts at v2 and must not repeat this branch or add the same columns. Add `AppDatabase.forTesting(super.executor)` for the migration test. Update `_messageToCompanion()` and `_driftRowToMessage()` in `local_db_service.dart` with all three nullable fields.

- [ ] **Step 4: Render terminal versus retryable failures**

In private chat `_SendStatusIcon`, show `message.failureMessage` for failed messages. Set `onPressed` only when `message.canRetry`; render no refresh icon/button for `CONTENT_REJECTED` or `ACCOUNT_DISABLED`. `retryFailedMessage()` must return immediately when `!failed.canRetry`. In community HTTP chat, unsuccessful sends remove the optimistic map and show the structured message; if its WebSocket path uses the same reliable service, store `failure_code`/`failure_message`/`retryable` and use the persisted retry flag.

- [ ] **Step 5: Generate Drift code and run migration/UI/chat tests**

Run:

```bash
cd /d/FlutterProject/nonto
dart run build_runner build --delete-conflicting-outputs
flutter test \
  test/moderation_drift_migration_test.dart \
  test/moderation_chat_failure_ui_test.dart \
  test/moderation_chat_queue_test.dart \
  test/chat_reliability_regression_test.dart \
  test/nonto_community_chat_phase9_regression_test.dart
```

Expected: all tests pass; a v1 row remains after opening schema v2 and failed-message semantics survive a database round trip.

- [ ] **Step 6: Commit Task 17**

```bash
cd /d/FlutterProject/nonto
git add lib/models/message.dart lib/services/database/app_database.dart lib/services/database/app_database.g.dart lib/services/local_db_service.dart lib/providers/chat_notifiers.dart lib/screens/chat/chat_room_screen.dart lib/screens/community/community_chat_screen.dart test/moderation_drift_migration_test.dart test/moderation_chat_failure_ui_test.dart
git commit -m "feat: persist moderation chat failures"
```

## Final verification checklist: backend, Flutter, migration, privacy, and phase boundaries

**Files:**
- Modify if a check finds a defect: only files already listed in Tasks 1–17
- Test: all backend and Flutter tests

- [ ] **Step 1: Run backend static contract and full tests**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest -q
```

Expected: exit 0 with no failed tests. Existing environment-dependent diagnostic scripts may be excluded only if they were already excluded by project pytest configuration before this work; do not mark a new phase-1 test skipped.

- [ ] **Step 2: Verify one Alembic head and no model drift**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m alembic heads
./.venv/Scripts/python.exe -m alembic check
```

Expected: exactly one `2026_07_18_0200 (head)` and `No new upgrade operations detected.` The migration's `down_revision` must still be the actual head captured in Task 3 Step 4, not a revision copied from an investigation.

- [ ] **Step 3: Run backend privacy and phase-boundary scans**

Run:

```bash
cd /d/NanTuPy
! rg -n "text_preview|hit_words=.*original|original_text\[:|Token invalid.*str\(e\)|detail=str\(e\)" app/services app/routers
! rg -n "TencentCloud|moderation_cases|content_revisions|moderation_tasks|ModerationTaskWorker|pending_review|manual_review" app/services/moderation_*.py app/routers tests/test_moderation_*.py
! rg -n "ContentModeration|content_filter" app/routers/{auth,posts,interactions,chat,ws,communities,comic,roles,topics,reports}.py
```

Expected: all three negated searches exit 0 with no matches. Generic pre-existing `detail=str(e)` outside moderation-touched write paths must be converted to a non-sensitive stable 500 while editing that path; do not expose database or regex exceptions.

- [ ] **Step 4: Run Flutter formatting, analysis, package tests, and full root tests**

Run:

```bash
cd /d/FlutterProject/nonto
dart format --output=none --set-exit-if-changed lib test packages/reliable_websocket/lib packages/reliable_websocket/test
flutter analyze
(cd packages/reliable_websocket && flutter test)
flutter test
```

Expected: every command exits 0; no analyzer errors/warnings introduced; package and root test suites pass.

- [ ] **Step 5: Verify structured contracts and no sensitive Flutter logging**

Run:

```bash
cd /d/FlutterProject/nonto
rg -n "CONTENT_REJECTED|MODERATION_UNAVAILABLE|ACCOUNT_DISABLED" lib test packages/reliable_websocket
! rg -n "debugPrint\([^\n]*(token=|request.*data|response.*data|content=)" lib/services/api/api_client.dart lib/services/websocket_service.dart lib/services/chat_send_queue.dart
```

Expected: all three codes appear in implementation and tests; the negated logging scan finds no token/body logging.

- [ ] **Step 6: Run plan placeholder, interface, and scope self-checks**

Run:

```bash
cd /d/NanTuPy
PLAN=docs/superpowers/plans/2026-07-18-content-moderation-phase1-text.md
! rg -n 'T[B]D|T[O]DO|implement later|fill in details|后续实[现]|稍后补[充]|类似 Task' "$PLAN"
rg -n 'ModerationService|LocalTextModerator|ModerationPolicy|ModerationResult|ModerationDecision' "$PLAN"
rg -n 'CONTENT_REJECTED|MODERATION_UNAVAILABLE|ACCOUNT_DISABLED|retryable' "$PLAN"
rg -n 'ACCOUNT_DISABLED.*403|403.*ACCOUNT_DISABLED|rule_version.*int|Severity.*low.*medium.*high' "$PLAN"
rg -n 'moderate_realtime_text|moderate_public_text|v1→v2|failure_code|failure_message' "$PLAN"
rg -n 'alembic heads|down_revision|真实|actual head' "$PLAN"
```

Expected: the forbidden-placeholder search has no matches; every locked type, status, retryability value, phase boundary, and Drift v2 column is present; the migration instructions explicitly query the actual head.

- [ ] **Step 7: Verify all required entrance families have rejection and unavailable coverage**

Run:

```bash
cd /d/NanTuPy
./.venv/Scripts/python.exe -m pytest tests/test_moderation_phase1_write_paths.py -q
rg -n "posts|comments|chat|communities|comic|auth|roles|topics|reports" tests/test_moderation_*writes.py tests/test_moderation_messages.py
```

Expected: inventory tests pass and the output shows every required family. Confirm manually from the assertions that rejection occurs before assignment/creation and that message rejection also leaves dedup, sequence log, fanout, notifications, push, timestamp, and cache unchanged.

- [ ] **Step 8: Review the final diffs without committing acceptance-only changes**

Run:

```bash
git -C /d/NanTuPy status --short
git -C /d/NanTuPy diff --check
git -C /d/FlutterProject/nonto status --short
git -C /d/FlutterProject/nonto diff --check
```

Expected: no whitespace errors. Only Task 1–17 implementation/test files are changed relative to the task branch; pre-existing unrelated changes remain untouched. This verification checklist creates no commit because it changes no files.

## Requirement-to-task acceptance index

- Unified types/errors/policy/service, `rule_version: int`, three-value `Severity`, and phase-2-stable imports without predefining Phase 2 entrypoints: Tasks 1–2.
- Local normalization, baseline rules, spam/abuse/hate, fail-closed behavior: Task 2.
- Dynamic ORM and execution-time real-head Alembic migration: Task 3.
- Immutable snapshot, database version polling across API processes, safe regex static checks and runtime timeout: Task 4.
- Administrator sensitive-word API, version increments, legacy compatibility, privacy-safe logs: Task 5.
- `is_active` at login, current token, refresh, optional auth, role dependency, and WebSocket: Task 6.
- Post and ordinary-comment create/edit, with old value retained on rejection: Task 7.
- HTTP direct chat, WebSocket direct/community chat, and HTTP community chat with zero rejection side effects: Task 8.
- Community name, description, rules, announcements, join text, and ban reason: Task 9.
- Comic event fields and comic-comment create (no invented edit endpoint): Task 10.
- Registration/public profile, role profiles, identity applications, and visible governance comments: Task 11.
- Topics, reports, governance input, and complete machine-verifiable inventory: Task 12.
- Flutter structured `ApiResponse`, locked retryability, and HTTP 403 `ACCOUNT_DISABLED` logout without refresh: Task 13.
- Flutter HTTP page rollback and standard user-safe messages: Task 14.
- `reliable_websocket` non-200 ACK terminal outbox handling: Task 15.
- Typed WebSocket failures, exact `client_msg_id` matching, early failed-ACK race, and queue progress: Task 16.
- Phase-1-exclusive Drift v1→v2 migration with nullable failure code/message/retryability, plus terminal/retryable UI distinction: Task 17.
- Backend/Flutter full suites, one Alembic head, privacy, placeholder, interface, inventory, and phase-boundary checks: final verification checklist.

## Explicit phase 2–4 exclusions

Execution is out of scope if it introduces any of the following: case/revision/task/action/violation/profile governance tables; pending/manual-review states; content hashes or version application; report disposition/appeal workflow; restrictions or automatic scoring; Provider SDK/configuration; Tencent Cloud calls; COS quarantine/public promotion; media review; worker command, leases, retries, or dead letters; historical scans; retention cleanup; moderation operations dashboard. Those capabilities belong to the later phase plans and must reuse, not expand, the phase-1 synchronous contracts above.
