import pytest
import regex

from app.services.local_text_moderator import LocalTextModerator, normalize_text
from app.services.moderation_errors import ContentRejected, ModerationUnavailable
from app.services.moderation_service import ModerationService
from app.services.moderation_types import (
    CompiledRegexRule,
    ModerationContext,
    ModerationDecision,
    ModerationRule,
    ModerationSnapshot,
    RiskCategory,
    Severity,
)


CONTEXT = ModerationContext(
    target_type="chat_message", actor_user_id=3, is_public=False
)


def empty_snapshot(version: int = 7) -> ModerationSnapshot:
    return ModerationSnapshot(
        version=version,
        literal_rules=(),
        regex_rules=(),
        loaded_at=1.0,
        verified_at=1.0,
    )


def rule(
    *,
    rule_id: int | None,
    expression: str,
    match_type: str,
    category: RiskCategory = RiskCategory.SPAM,
    severity: Severity = Severity.HIGH,
) -> ModerationRule:
    return ModerationRule(
        id=rule_id,
        expression=expression,
        match_type=match_type,
        category=category,
        severity=severity,
        row_version=1,
    )


def test_unicode_zero_width_case_and_whitespace_are_normalized():
    assert normalize_text("Ａ\u200bＢ  C") == "ab c"
    assert normalize_text("\tＦＯＯ\u200c\u200d\u2060\ufeff\n BAR  ") == "foo bar"
    assert normalize_text("") == ""


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("黄色网站", RiskCategory.SEXUAL),
        ("百家乐", RiskCategory.ILLEGAL),
        ("海洛因", RiskCategory.ILLEGAL),
        ("恐怖袭击", RiskCategory.VIOLENCE),
        ("种族歧视", RiskCategory.HATE),
        ("kill yourself", RiskCategory.ABUSE),
    ],
)
def test_baseline_categories_are_preserved(text, category):
    result = LocalTextModerator(empty_snapshot).moderate(text)

    assert result.decision is ModerationDecision.REJECT
    assert result.risk_category is category
    assert result.severity is Severity.HIGH
    assert result.confidence == 1.0
    assert result.matched_rule_ids == ()


@pytest.mark.parametrize("text", ["normal fictional text", "", "普通的 Unicode 文本"])
def test_ordinary_and_empty_text_are_approved(text):
    result = LocalTextModerator(empty_snapshot).moderate(text)

    assert result.decision is ModerationDecision.APPROVE
    assert result.risk_category is RiskCategory.OTHER
    assert result.severity is None
    assert result.confidence == 1.0
    assert result.policy_version == "phase1-local-v1"
    assert result.rule_version == 7
    assert result.matched_rule_ids == ()


def test_obfuscated_wechat_advertising_and_general_hate_are_rejected():
    moderator = LocalTextModerator(empty_snapshot)

    spam = moderator.moderate("加  微  信 wx123456")
    hate = moderator.moderate("他们都 滚")

    assert spam.decision is ModerationDecision.REJECT
    assert spam.risk_category is RiskCategory.SPAM
    assert spam.severity is Severity.MEDIUM
    assert hate.decision is ModerationDecision.REJECT
    assert hate.risk_category is RiskCategory.HATE
    assert hate.severity is Severity.HIGH


def test_service_raises_stable_rejection_without_exposing_text():
    body = "加微信 wx123456"

    with pytest.raises(ContentRejected) as caught:
        ModerationService(LocalTextModerator(empty_snapshot)).moderate_text(
            body, CONTEXT
        )

    assert caught.value.public_message == "内容未通过审核"
    assert body not in caught.value.public_message


def test_snapshot_provider_is_read_for_every_moderation():
    snapshots = iter((empty_snapshot(10), empty_snapshot(11)))
    moderator = LocalTextModerator(lambda: next(snapshots))

    assert moderator.moderate("hello").rule_version == 10
    assert moderator.moderate("hello").rule_version == 11


def test_broken_snapshot_is_fail_closed_without_exposing_internal_details():
    def broken_snapshot():
        raise RuntimeError("database unavailable with private body")

    with pytest.raises(ModerationUnavailable) as caught:
        ModerationService(LocalTextModerator(broken_snapshot)).moderate_text(
            "hello", CONTEXT
        )

    assert caught.value.error_code.value == "MODERATION_UNAVAILABLE"
    assert "private body" not in caught.value.public_message
    assert "hello" not in caught.value.public_message


def test_regex_runtime_timeout_is_fail_closed_and_uses_locked_timeout():
    class TimingOutPattern:
        def __init__(self):
            self.timeout = None

        def search(self, text: str, timeout: float):
            self.timeout = timeout
            raise TimeoutError("private regex expression")

    pattern = TimingOutPattern()
    dynamic_rule = rule(rule_id=8, expression="redacted", match_type="regex")
    snapshot = ModerationSnapshot(
        version=8,
        literal_rules=(),
        regex_rules=(CompiledRegexRule(rule=dynamic_rule, pattern=pattern),),
        loaded_at=1.0,
        verified_at=1.0,
    )

    with pytest.raises(ModerationUnavailable) as caught:
        ModerationService(LocalTextModerator(lambda: snapshot)).moderate_text(
            "hello", CONTEXT
        )

    assert pattern.timeout == 0.02
    assert caught.value.error_code.value == "MODERATION_UNAVAILABLE"
    assert "private regex" not in caught.value.public_message
    assert "redacted" not in caught.value.public_message


def test_dynamic_literal_uses_current_snapshot_and_records_actual_integer_id():
    dynamic_rule = rule(
        rule_id=12,
        expression="Ｎｅｗ\u200bＢｌｏｃｋ",
        match_type="literal",
        category=RiskCategory.PRIVACY,
        severity=Severity.MEDIUM,
    )
    snapshot = ModerationSnapshot(
        version=21,
        literal_rules=(dynamic_rule,),
        regex_rules=(),
        loaded_at=1.0,
        verified_at=1.0,
    )

    result = LocalTextModerator(lambda: snapshot).moderate("prefix newblock suffix")

    assert result.decision is ModerationDecision.REJECT
    assert result.risk_category is RiskCategory.PRIVACY
    assert result.severity is Severity.MEDIUM
    assert result.rule_version == 21
    assert result.matched_rule_ids == (12,)


def test_dynamic_regex_uses_third_party_regex_and_records_no_id_for_none():
    dynamic_rule = rule(
        rule_id=None,
        expression=r"ticket[- ]?[0-9]{4}",
        match_type="regex",
        category=RiskCategory.ILLEGAL,
    )
    compiled = regex.compile(dynamic_rule.expression, regex.IGNORECASE)
    snapshot = ModerationSnapshot(
        version=22,
        literal_rules=(),
        regex_rules=(CompiledRegexRule(rule=dynamic_rule, pattern=compiled),),
        loaded_at=1.0,
        verified_at=1.0,
    )

    result = LocalTextModerator(lambda: snapshot).moderate("ＴＩＣＫＥＴ-1234")

    assert compiled.__class__.__module__.startswith("_regex")
    assert result.decision is ModerationDecision.REJECT
    assert result.risk_category is RiskCategory.ILLEGAL
    assert result.severity is Severity.HIGH
    assert result.confidence == 0.99
    assert result.rule_version == 22
    assert result.matched_rule_ids == ()


def test_malformed_dynamic_rule_is_fail_closed():
    malformed_rule = rule(rule_id=True, expression="blocked", match_type="literal")
    snapshot = ModerationSnapshot(
        version=23,
        literal_rules=(malformed_rule,),
        regex_rules=(),
        loaded_at=1.0,
        verified_at=1.0,
    )

    with pytest.raises(ModerationUnavailable) as caught:
        ModerationService(LocalTextModerator(lambda: snapshot)).moderate_text(
            "blocked", CONTEXT
        )

    assert caught.value.error_code.value == "MODERATION_UNAVAILABLE"
    assert "blocked" not in caught.value.public_message
