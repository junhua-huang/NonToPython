from dataclasses import FrozenInstanceError, asdict, is_dataclass
import re

import pytest

from app.services.moderation_errors import (
    AppContractError,
    ContentRejected,
    ErrorCode,
    ModerationUnavailable,
    content_rejected,
    to_http_exception,
)
from app.services.moderation_inventory import EXCLUDED_TEXT_INPUTS, MODERATED_TEXT_FIELDS
from app.services.moderation_policy import ModerationPolicy
from app.services.moderation_service import ModerationService
from app.services.moderation_types import (
    CompiledRegexRule,
    ModerationContext,
    ModerationDecision,
    ModerationResult,
    ModerationRule,
    ModerationSnapshot,
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


def rejected_result() -> ModerationResult:
    return ModerationResult(
        decision=ModerationDecision.REJECT,
        risk_category=RiskCategory.ABUSE,
        severity=Severity.HIGH,
        confidence=1.0,
        policy_version="phase1-local-v1",
        rule_version=1,
        matched_rule_ids=(7,),
    )


def test_phase1_enums_have_exact_locked_values():
    assert {item.value for item in ModerationDecision} == {"approve", "reject"}
    assert {item.value for item in Severity} == {"low", "medium", "high"}
    assert {item.value for item in RiskCategory} == {
        "sexual",
        "violence",
        "illegal",
        "abuse",
        "hate",
        "spam",
        "privacy",
        "other",
    }


def test_result_is_immutable_slotted_and_requires_an_actual_int_rule_version():
    result = approved_result()

    assert result.severity is None
    assert type(result.rule_version) is int
    assert type(asdict(result)["rule_version"]) is int
    assert not hasattr(result, "__dict__")
    with pytest.raises(TypeError, match="^rule_version must be int$"):
        ModerationResult(
            decision=ModerationDecision.APPROVE,
            risk_category=RiskCategory.OTHER,
            severity=None,
            confidence=1.0,
            policy_version="phase1-local-v1",
            rule_version="1",  # type: ignore[arg-type]
        )
    with pytest.raises(TypeError, match="^rule_version must be int$"):
        ModerationResult(
            decision=ModerationDecision.APPROVE,
            risk_category=RiskCategory.OTHER,
            severity=None,
            confidence=1.0,
            policy_version="phase1-local-v1",
            rule_version=True,
        )
    with pytest.raises(FrozenInstanceError):
        result.confidence = 0.1  # type: ignore[misc]


def test_all_phase1_value_objects_are_frozen_and_slotted():
    context = ModerationContext(target_type="post", actor_user_id=7, is_public=True)
    rule = ModerationRule(
        id=3,
        expression="blocked",
        match_type="literal",
        category=RiskCategory.ABUSE,
        severity=Severity.MEDIUM,
        row_version=2,
    )
    compiled_rule = CompiledRegexRule(rule=rule, pattern=re.compile("blocked"))
    snapshot = ModerationSnapshot(
        version=2,
        literal_rules=(rule,),
        regex_rules=(compiled_rule,),
        loaded_at=10.0,
        verified_at=11.0,
    )

    for value in (context, rule, compiled_rule, snapshot, approved_result()):
        assert is_dataclass(value)
        assert not hasattr(value, "__dict__")
        first_field = next(iter(value.__dataclass_fields__))
        with pytest.raises(FrozenInstanceError):
            setattr(value, first_field, getattr(value, first_field))


def test_policy_returns_local_binary_decision_without_pending_or_manual_review():
    context = ModerationContext(target_type="post", actor_user_id=7, is_public=True)
    policy = ModerationPolicy()

    assert policy.version == "phase1-local-v1"
    assert policy.decide(context, approved_result()) is ModerationDecision.APPROVE
    assert policy.decide(context, rejected_result()) is ModerationDecision.REJECT
    assert "pending" not in {item.value for item in ModerationDecision}
    assert "manual" not in {item.value for item in ModerationDecision}


def test_phase1_service_does_not_predefine_phase2_entrypoints():
    assert not hasattr(ModerationService, "moderate_realtime_text")
    assert not hasattr(ModerationService, "moderate_public_text")


class StubModerator:
    def __init__(self, result=None, error=None):
        self.result = result or approved_result()
        self.error = error
        self.seen = []

    def moderate(self, text):
        self.seen.append(text)
        if self.error is not None:
            raise self.error
        return self.result


def test_service_fails_closed_when_local_moderator_is_missing_or_raises():
    context = ModerationContext(target_type="post", actor_user_id=7, is_public=True)

    with pytest.raises(ModerationUnavailable) as missing:
        ModerationService().moderate_text("hello", context)
    assert isinstance(missing.value.cause, RuntimeError)

    internal = RuntimeError("database password secret")
    with pytest.raises(ModerationUnavailable) as failed:
        ModerationService(StubModerator(error=internal)).moderate_text("hello", context)
    assert failed.value.cause is internal


def test_service_raises_content_rejected_for_local_reject():
    context = ModerationContext(target_type="post", actor_user_id=7, is_public=True)

    with pytest.raises(ContentRejected):
        ModerationService(StubModerator(result=rejected_result())).moderate_text(
            "blocked", context
        )


def test_service_moderates_only_nonblank_fields_and_preserves_result_order():
    context = ModerationContext(target_type="profile", actor_user_id=7, is_public=True)
    moderator = StubModerator()
    service = ModerationService(moderator)

    results = service.moderate_fields(
        {"name": "NanTu", "bio": None, "city": "   ", "intro": "Hello"},
        context,
    )

    assert moderator.seen == ["NanTu", "Hello"]
    assert results == (approved_result(), approved_result())


@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (
            content_rejected(),
            422,
            {
                "code": "CONTENT_REJECTED",
                "message": "内容未通过审核",
                "retryable": False,
            },
        ),
        (
            ModerationUnavailable(RuntimeError("database password secret")),
            503,
            {
                "code": "MODERATION_UNAVAILABLE",
                "message": "内容审核服务暂不可用，请稍后重试",
                "retryable": True,
            },
        ),
        (
            AppContractError(ErrorCode.ACCOUNT_DISABLED),
            403,
            {
                "code": "ACCOUNT_DISABLED",
                "message": "账号已停用",
                "retryable": False,
            },
        ),
    ],
)
def test_http_error_contracts_are_exact_and_do_not_expose_causes(
    error, status_code, detail
):
    response_error = to_http_exception(error)

    assert response_error.status_code == status_code
    assert response_error.detail == detail
    assert set(response_error.detail) == {"code", "message", "retryable"}
    assert "secret" not in str(response_error.detail).lower()
    assert "runtimeerror" not in str(response_error.detail).lower()


def test_error_codes_have_exact_locked_values():
    assert {item.value for item in ErrorCode} == {
        "CONTENT_REJECTED",
        "MODERATION_UNAVAILABLE",
        "ACCOUNT_DISABLED",
    }


def test_inventory_has_exact_complete_endpoint_key_set():
    assert set(MODERATED_TEXT_FIELDS) == {
        "POST /api/auth/register",
        "PUT /api/auth/profile",
        "POST /api/posts",
        "PUT /api/posts/{post_id}",
        "POST /api/posts/{post_id}/comments",
        "PUT /api/comments/{comment_id}",
        "POST /api/chat/conversations/{conversation_id}/messages",
        "WS send_message",
        "POST /api/communities",
        "PATCH /api/communities/{community_id}",
        "POST /api/communities/{community_id}/join",
        "POST /api/communities/{community_id}/announcements",
        "PATCH /api/communities/{community_id}/announcements/{announcement_id}",
        "POST /api/communities/{community_id}/bans",
        "POST /api/communities/{community_id}/chat/messages",
        "POST /api/comic/events",
        "PUT /api/comic/events/{event_id}",
        "POST /api/comic/events/{event_id}/comments",
        "POST /api/roles/apply",
        "PUT /api/roles/profiles/coser",
        "PUT /api/roles/profiles/photographer",
        "PUT /api/roles/profiles/service",
        "POST /api/roles/applications/{application_id}/approve",
        "POST /api/roles/applications/{application_id}/reject",
        "POST /api/roles/applications/{application_id}/suspend",
        "POST /role-applications/{application_id}/approve",
        "POST /role-applications/{application_id}/reject",
        "POST /role-applications/{application_id}/suspend",
        "POST /api/topics",
        "PUT /api/topics/{topic_id}",
        "POST /api/reports",
        "POST /api/reports/post",
        "POST /api/reports/comment",
        "POST /api/reports/user",
    }


def test_inventory_has_no_media_fields_and_explicitly_records_exclusions():
    flattened = {field for fields in MODERATED_TEXT_FIELDS.values() for field in fields}
    media_fields = {"image", "video", "avatar_url", "banner_url", "proof_images"}

    assert not flattened.intersection(media_fields)
    assert set(EXCLUDED_TEXT_INPUTS) == {
        "POST /api/auth/register",
        "POST /api/auth/login",
        "POST /api/auth/change-password",
        "POST /api/auth/forgot-password",
        "POST /api/auth/reset-password",
        "message media metadata",
        "role media evidence",
        "read-only queries",
        "system-generated text",
    }
    assert {"email", "password", "email_code"} <= set(
        EXCLUDED_TEXT_INPUTS["POST /api/auth/register"]
    )
    assert {"media_url", "avatar_url", "banner_url", "cover_photo_url"} <= set(
        EXCLUDED_TEXT_INPUTS["message media metadata"]
    )
    assert {"proof_images", "portfolio_images"} <= set(
        EXCLUDED_TEXT_INPUTS["role media evidence"]
    )
    assert {"keyword", "search_query", "pagination", "sort"} <= set(
        EXCLUDED_TEXT_INPUTS["read-only queries"]
    )
    assert {
        "friend_acceptance_hi",
        "community_welcome_message",
        "notification_text",
        "recall_notice",
    } <= set(EXCLUDED_TEXT_INPUTS["system-generated text"])
