from collections.abc import Mapping
from dataclasses import FrozenInstanceError, asdict, is_dataclass
import math
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


def make_result(**overrides) -> ModerationResult:
    values = {
        "decision": ModerationDecision.APPROVE,
        "risk_category": RiskCategory.OTHER,
        "severity": None,
        "confidence": 1.0,
        "policy_version": "phase1-local-v1",
        "rule_version": 0,
        "matched_rule_ids": (),
    }
    values.update(overrides)
    return ModerationResult(**values)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("decision", "approve", "decision must be ModerationDecision"),
        ("risk_category", "other", "risk_category must be RiskCategory"),
        ("severity", "high", "severity must be Severity or None"),
        ("confidence", True, "confidence must be int or float"),
        ("confidence", "1", "confidence must be int or float"),
        ("policy_version", 1, "policy_version must be str"),
        ("rule_version", "1", "rule_version must be int"),
        ("rule_version", True, "rule_version must be int"),
        ("matched_rule_ids", [1], "matched_rule_ids must be tuple"),
        ("matched_rule_ids", (True,), "matched_rule_ids items must be int"),
        ("matched_rule_ids", ("1",), "matched_rule_ids items must be int"),
    ],
)
def test_result_rejects_wrong_runtime_types(field, value, message):
    with pytest.raises(TypeError, match=f"^{message}$"):
        make_result(**{field: value})


@pytest.mark.parametrize("confidence", [math.nan, math.inf, -math.inf])
def test_result_requires_finite_confidence(confidence):
    with pytest.raises(ValueError, match="^confidence must be finite$"):
        make_result(confidence=confidence)


@pytest.mark.parametrize("confidence", [-0.01, 1.01, 10**1000])
def test_result_requires_confidence_in_closed_unit_interval(confidence):
    with pytest.raises(ValueError, match="^confidence must be between 0 and 1$"):
        make_result(confidence=confidence)


def test_result_requires_rejected_result_to_have_severity():
    with pytest.raises(ValueError, match="^rejected result must have severity$"):
        ModerationResult(
            decision=ModerationDecision.REJECT,
            risk_category=RiskCategory.SPAM,
            severity=None,
            confidence=1.0,
            policy_version="phase1-local-v1",
            rule_version=1,
        )


def test_result_requires_approve_severity_to_be_none_and_nonempty_policy_version():
    with pytest.raises(
        ValueError, match="^severity must be None when decision is APPROVE$"
    ):
        make_result(severity=Severity.LOW)
    with pytest.raises(ValueError, match="^policy_version must not be empty$"):
        make_result(policy_version="")


class Confidence(int):
    pass


class RuleIds(tuple):
    pass


def test_result_accepts_numeric_and_tuple_subclasses():
    result = make_result(confidence=Confidence(1), matched_rule_ids=RuleIds((1,)))

    assert result.confidence == 1
    assert result.matched_rule_ids == (1,)


def test_result_accepts_integer_confidence_and_is_immutable_and_slotted():
    result = make_result(confidence=1, matched_rule_ids=(1, 2))

    assert result.severity is None
    assert type(result.rule_version) is int
    assert type(asdict(result)["rule_version"]) is int
    assert result.confidence == 1
    assert not hasattr(result, "__dict__")
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


def test_phase1_service_has_only_locked_public_entrypoints():
    public_callables = {
        name
        for name, member in ModerationService.__dict__.items()
        if not name.startswith("_") and callable(member)
    }

    assert public_callables == {"moderate_text", "moderate_fields"}
    assert not hasattr(ModerationService, "moderate_realtime_text")
    assert not hasattr(ModerationService, "moderate_public_text")


_DEFAULT_RESULT = object()


class StubModerator:
    def __init__(self, result=_DEFAULT_RESULT, error=None):
        self.result = approved_result() if result is _DEFAULT_RESULT else result
        self.error = error
        self.seen = []

    def moderate(self, text):
        self.seen.append(text)
        if self.error is not None:
            raise self.error
        return self.result


class StubPolicy:
    def __init__(self, decision=ModerationDecision.APPROVE, error=None):
        self.decision = decision
        self.error = error
        self.seen = []

    def __bool__(self):
        return False

    def decide(self, context, result):
        self.seen.append((context, result))
        if self.error is not None:
            raise self.error
        return self.decision


def test_service_fails_closed_when_local_moderator_is_missing_or_raises():
    context = ModerationContext(target_type="post", actor_user_id=7, is_public=True)

    with pytest.raises(ModerationUnavailable) as missing:
        ModerationService().moderate_text("hello", context)
    assert isinstance(missing.value.cause, RuntimeError)

    internal = RuntimeError("database password secret")
    with pytest.raises(ModerationUnavailable) as failed:
        ModerationService(StubModerator(error=internal)).moderate_text("hello", context)
    assert failed.value.cause is internal


def test_service_preserves_existing_contract_error_identity():
    context = ModerationContext(target_type="post", actor_user_id=7, is_public=True)
    unavailable = ModerationUnavailable(RuntimeError("moderator offline"))

    with pytest.raises(ModerationUnavailable) as raised:
        ModerationService(StubModerator(error=unavailable)).moderate_text(
            "hello", context
        )

    assert raised.value is unavailable


def test_service_uses_explicit_falsey_policy_and_propagates_its_contract_error():
    context = ModerationContext(target_type="post", actor_user_id=7, is_public=True)
    rejected = ContentRejected()
    policy = StubPolicy(error=rejected)

    with pytest.raises(ContentRejected) as raised:
        ModerationService(StubModerator(), policy=policy).moderate_text("hello", context)

    assert raised.value is rejected
    assert len(policy.seen) == 1


def test_service_raises_content_rejected_for_local_reject():
    context = ModerationContext(target_type="post", actor_user_id=7, is_public=True)

    with pytest.raises(ContentRejected):
        ModerationService(StubModerator(result=rejected_result())).moderate_text(
            "blocked", context
        )


@pytest.mark.parametrize("result", [None, "approve", object()])
def test_service_fails_closed_on_malformed_local_result(result):
    context = ModerationContext(target_type="post", actor_user_id=7, is_public=True)

    with pytest.raises(ModerationUnavailable) as raised:
        ModerationService(StubModerator(result=result)).moderate_text("hello", context)

    assert isinstance(raised.value.cause, TypeError)
    assert str(raised.value.cause) == "local moderator must return ModerationResult"


@pytest.mark.parametrize("decision", ["approve", None, object()])
def test_service_fails_closed_on_malformed_policy_decision(decision):
    context = ModerationContext(target_type="post", actor_user_id=7, is_public=True)
    policy = StubPolicy(decision=decision)

    with pytest.raises(ModerationUnavailable) as raised:
        ModerationService(StubModerator(), policy=policy).moderate_text("hello", context)

    assert isinstance(raised.value.cause, ValueError)
    assert str(raised.value.cause) == "policy must return APPROVE or REJECT"


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


class BrokenFields(Mapping):
    def __init__(self, error, fail_during_iteration=False):
        self.error = error
        self.fail_during_iteration = fail_during_iteration

    def __getitem__(self, key):
        raise KeyError(key)

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def values(self):
        if not self.fail_during_iteration:
            raise self.error

        error = self.error

        def broken_values():
            yield "first"
            raise error

        return broken_values()


@pytest.mark.parametrize("fail_during_iteration", [False, True])
def test_moderate_fields_wraps_values_access_and_iteration_errors(
    fail_during_iteration,
):
    context = ModerationContext(target_type="profile", actor_user_id=7, is_public=True)
    internal = RuntimeError("broken mapping")
    moderator = StubModerator()

    with pytest.raises(ModerationUnavailable) as raised:
        ModerationService(moderator).moderate_fields(
            BrokenFields(internal, fail_during_iteration), context
        )

    assert raised.value.cause is internal
    assert moderator.seen == (["first"] if fail_during_iteration else [])


@pytest.mark.parametrize("value", [1, False, object()])
def test_moderate_fields_fails_closed_on_non_string_values(value):
    context = ModerationContext(target_type="profile", actor_user_id=7, is_public=True)

    with pytest.raises(ModerationUnavailable) as raised:
        ModerationService(StubModerator()).moderate_fields({"field": value}, context)

    assert isinstance(raised.value.cause, TypeError)
    assert str(raised.value.cause) == "moderation field values must be str or None"


class BrokenString(str):
    def __new__(cls, value, error):
        instance = super().__new__(cls, value)
        instance.error = error
        return instance

    def strip(self, *args, **kwargs):
        raise self.error


def test_moderate_fields_wraps_strip_errors():
    context = ModerationContext(target_type="profile", actor_user_id=7, is_public=True)
    internal = RuntimeError("broken strip")

    with pytest.raises(ModerationUnavailable) as raised:
        ModerationService(StubModerator()).moderate_fields(
            {"field": BrokenString("hello", internal)}, context
        )

    assert raised.value.cause is internal


def test_moderate_fields_propagates_contract_errors_without_rewrapping():
    context = ModerationContext(target_type="profile", actor_user_id=7, is_public=True)
    unavailable = ModerationUnavailable(RuntimeError("moderator offline"))

    with pytest.raises(ModerationUnavailable) as raised:
        ModerationService(StubModerator(error=unavailable)).moderate_fields(
            {"name": None, "bio": "Hello"}, context
        )

    assert raised.value is unavailable


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


def test_inventory_has_exact_34_endpoint_to_field_contract():
    expected = {
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
        "PATCH /api/communities/{community_id}/announcements/{announcement_id}": (
            "title",
            "content",
        ),
        "POST /api/communities/{community_id}/bans": ("reason",),
        "POST /api/communities/{community_id}/chat/messages": ("content",),
        "POST /api/comic/events": (
            "name",
            "venue",
            "ticket_info",
            "website",
            "intro",
        ),
        "PUT /api/comic/events/{event_id}": (
            "name",
            "venue",
            "ticket_info",
            "website",
            "intro",
        ),
        "POST /api/comic/events/{event_id}/comments": ("content",),
        "POST /api/roles/apply": (
            "reason",
            "application_text",
            "contact_info",
            "extra_note",
            "portfolio_links[]",
        ),
        "PUT /api/roles/profiles/coser": (
            "cosname",
            "bio",
            "styles",
            "city",
            "social_links",
        ),
        "PUT /api/roles/profiles/photographer": (
            "equipment",
            "styles",
            "city",
            "social_links",
        ),
        "PUT /api/roles/profiles/service": (
            "service_type",
            "description",
            "city",
            "price_info",
        ),
        "POST /api/roles/applications/{application_id}/approve": (
            "review_comment",
        ),
        "POST /api/roles/applications/{application_id}/reject": (
            "review_comment",
        ),
        "POST /api/roles/applications/{application_id}/suspend": (
            "review_comment",
        ),
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

    assert len(MODERATED_TEXT_FIELDS) == 34
    assert MODERATED_TEXT_FIELDS == expected


def test_inventory_has_no_media_fields_and_explicitly_records_exclusions():
    flattened = {field for fields in MODERATED_TEXT_FIELDS.values() for field in fields}
    media_fields = {
        "image",
        "video",
        "avatar_url",
        "banner_url",
        "proof_images",
        "media_url",
        "video_url",
        "image_urls",
        "cover_photo_url",
        "portfolio_images",
    }

    assert flattened.isdisjoint(media_fields)
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
    assert {
        endpoint: EXCLUDED_TEXT_INPUTS[endpoint]
        for endpoint in (
            "POST /api/auth/register",
            "POST /api/auth/login",
            "POST /api/auth/change-password",
            "POST /api/auth/forgot-password",
            "POST /api/auth/reset-password",
        )
    } == {
        "POST /api/auth/register": ("email", "password", "email_code"),
        "POST /api/auth/login": ("login", "email", "password", "email_code"),
        "POST /api/auth/change-password": ("old_password", "new_password"),
        "POST /api/auth/forgot-password": ("email",),
        "POST /api/auth/reset-password": ("email", "code", "new_password"),
    }
    assert EXCLUDED_TEXT_INPUTS["message media metadata"] == (
        "media_url",
        "avatar_url",
        "banner_url",
        "cover_photo_url",
    )
    assert EXCLUDED_TEXT_INPUTS["role media evidence"] == (
        "proof_images",
        "portfolio_images",
    )
    assert EXCLUDED_TEXT_INPUTS["read-only queries"] == (
        "keyword",
        "search_query",
        "pagination",
        "sort",
    )
    assert EXCLUDED_TEXT_INPUTS["system-generated text"] == (
        "friend_acceptance_hi",
        "community_welcome_message",
        "notification_text",
        "recall_notice",
    )
