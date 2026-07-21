import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest
import regex

import app.services.local_text_moderator as local_text_moderator
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


def test_all_default_ignorable_code_points_are_removed_without_dropping_spaces():
    assert normalize_text("Ａ\u2063Ｂ\u034fＣ") == "abc"
    assert normalize_text("foo bar") == "foo bar"


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


@pytest.mark.parametrize("text", ["黄\u2063色网站", "黄 色 网 站"])
def test_obfuscated_cjk_baseline_literals_are_rejected(text):
    result = LocalTextModerator(empty_snapshot).moderate(text)

    assert result.decision is ModerationDecision.REJECT
    assert result.risk_category is RiskCategory.SEXUAL


@pytest.mark.parametrize(
    "text",
    [
        "something",
        "paradox",
        "escorted",
        "前meth后",
        "xxx_file",
        "nude_palette",
        "fake id_number",
        "xxx\u20dd",
        "λxxx",
        "xxxλ",
        "xxx١",
    ],
)
def test_ascii_baseline_literals_do_not_match_inside_unicode_words(text):
    result = LocalTextModerator(empty_snapshot).moderate(text)

    assert result.decision is ModerationDecision.APPROVE
    assert result.risk_category is RiskCategory.OTHER


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("meth", RiskCategory.ILLEGAL),
        ("dox", RiskCategory.ABUSE),
        ("escort", RiskCategory.SEXUAL),
        ("kill yourself", RiskCategory.ABUSE),
    ],
)
def test_standalone_ascii_baseline_literals_are_rejected(text, category):
    result = LocalTextModerator(empty_snapshot).moderate(text)

    assert result.decision is ModerationDecision.REJECT
    assert result.risk_category is category


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("去死，恐怖袭击", RiskCategory.VIOLENCE),
        ("种族歧视，去死", RiskCategory.HATE),
    ],
)
def test_baseline_group_priority_is_strict(text, category):
    result = LocalTextModerator(empty_snapshot).moderate(text)

    assert result.decision is ModerationDecision.REJECT
    assert result.risk_category is category


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


@pytest.mark.parametrize(
    "expression", ["", " \t\n ", "\u2063\u034f\u200b"]
)
@pytest.mark.parametrize("body", ["harmless private body", "porn"])
def test_dynamic_literal_normalizing_to_empty_is_unavailable_without_leaking_rule(
    expression, body
):
    private_rule_text = expression + "private-rule-marker"
    dynamic_rule = rule(rule_id=11, expression=expression, match_type="literal")
    snapshot = ModerationSnapshot(
        version=20,
        literal_rules=(dynamic_rule,),
        regex_rules=(),
        loaded_at=1.0,
        verified_at=1.0,
    )

    with pytest.raises(ModerationUnavailable) as caught:
        ModerationService(LocalTextModerator(lambda: snapshot)).moderate_text(
            body, CONTEXT
        )

    assert caught.value.error_code.value == "MODERATION_UNAVAILABLE"
    assert caught.value.public_message == "内容审核服务暂不可用，请稍后重试"
    assert body not in caught.value.public_message
    assert private_rule_text not in caught.value.public_message


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


@pytest.mark.parametrize(
    ("expression", "text"),
    [
        ("禁止词", "这是禁\u3000止\t词内容"),
        ("カジノ", "オンラインカ ジ ノ広告"),
        ("도박", "불법 도 박 광고"),
        ("赌カ박", "混合赌 カ 박广告"),
    ],
)
def test_dynamic_east_asian_literal_matches_unicode_whitespace_between_characters(
    expression, text
):
    dynamic_rule = rule(
        rule_id=13,
        expression=expression,
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

    result = LocalTextModerator(lambda: snapshot).moderate(text)

    assert result.decision is ModerationDecision.REJECT
    assert result.risk_category is RiskCategory.PRIVACY
    assert result.severity is Severity.MEDIUM
    assert result.matched_rule_ids == (13,)


def test_dynamic_english_literal_keeps_substring_semantics():
    dynamic_rule = rule(
        rule_id=14,
        expression="custom",
        match_type="literal",
        category=RiskCategory.PRIVACY,
    )
    snapshot = ModerationSnapshot(
        version=21,
        literal_rules=(dynamic_rule,),
        regex_rules=(),
        loaded_at=1.0,
        verified_at=1.0,
    )

    result = LocalTextModerator(lambda: snapshot).moderate("customized")

    assert result.decision is ModerationDecision.REJECT
    assert result.matched_rule_ids == (14,)


def test_dynamic_literal_matchers_are_prepared_once_for_large_snapshot(
    monkeypatch,
):
    prepare_calls = Counter()
    original_prepare = local_text_moderator._prepare_literal_matcher

    def counting_prepare(expression, *, baseline):
        prepare_calls[expression] += 1
        return original_prepare(expression, baseline=baseline)

    monkeypatch.setattr(
        local_text_moderator, "_prepare_literal_matcher", counting_prepare
    )
    dynamic_rules = tuple(
        rule(
            rule_id=index,
            expression=f"动态禁词{index}",
            match_type="literal",
            category=RiskCategory.PRIVACY,
        )
        for index in range(600)
    )
    snapshot = ModerationSnapshot(
        version=30,
        literal_rules=dynamic_rules,
        regex_rules=(),
        loaded_at=1.0,
        verified_at=1.0,
    )
    moderator = LocalTextModerator(lambda: snapshot)

    started = time.perf_counter()
    first = moderator.moderate("ordinary harmless text")
    first_elapsed = time.perf_counter() - started
    started = time.perf_counter()
    second = moderator.moderate("ordinary harmless text")
    second_elapsed = time.perf_counter() - started

    assert first.decision is ModerationDecision.APPROVE
    assert second.decision is ModerationDecision.APPROVE
    assert sum(prepare_calls.values()) == 600
    assert max(prepare_calls.values()) == 1
    assert first_elapsed < 1.5
    assert second_elapsed < 0.5


def test_dynamic_literal_cache_keys_by_snapshot_identity_not_version():
    first_rule = rule(rule_id=31, expression="first-private-rule", match_type="literal")
    second_rule = rule(rule_id=32, expression="second-private-rule", match_type="literal")
    first_snapshot = ModerationSnapshot(
        version=44,
        literal_rules=(first_rule,),
        regex_rules=(),
        loaded_at=1.0,
        verified_at=1.0,
    )
    second_snapshot = ModerationSnapshot(
        version=44,
        literal_rules=(second_rule,),
        regex_rules=(),
        loaded_at=2.0,
        verified_at=2.0,
    )
    snapshots = iter((first_snapshot, second_snapshot))
    moderator = LocalTextModerator(lambda: next(snapshots))

    assert moderator.moderate("first-private-rule").matched_rule_ids == (31,)
    assert moderator.moderate("second-private-rule").matched_rule_ids == (32,)


def test_dynamic_literal_cache_is_thread_safe_and_bounded(monkeypatch):
    prepare_count = 0
    prepare_count_lock = threading.Lock()
    original_prepare = local_text_moderator._prepare_literal_matcher

    def counting_prepare(expression, *, baseline):
        nonlocal prepare_count
        with prepare_count_lock:
            prepare_count += 1
        return original_prepare(expression, baseline=baseline)

    monkeypatch.setattr(
        local_text_moderator, "_prepare_literal_matcher", counting_prepare
    )
    shared_snapshot = ModerationSnapshot(
        version=50,
        literal_rules=tuple(
            rule(rule_id=index, expression=f"shared-{index}", match_type="literal")
            for index in range(25)
        ),
        regex_rules=(),
        loaded_at=1.0,
        verified_at=1.0,
    )
    moderator = LocalTextModerator(lambda: shared_snapshot)
    barrier = threading.Barrier(8)

    def moderate_once():
        barrier.wait()
        return moderator.moderate("ordinary harmless text").decision

    with ThreadPoolExecutor(max_workers=8) as executor:
        decisions = tuple(executor.map(lambda _: moderate_once(), range(8)))

    assert decisions == (ModerationDecision.APPROVE,) * 8
    assert prepare_count == 25

    for index in range(local_text_moderator._DYNAMIC_LITERAL_CACHE_SIZE + 5):
        current = ModerationSnapshot(
            version=index,
            literal_rules=(
                rule(
                    rule_id=index,
                    expression=f"bounded-{index}",
                    match_type="literal",
                ),
            ),
            regex_rules=(),
            loaded_at=float(index),
            verified_at=float(index),
        )
        moderator._snapshot_provider = lambda current=current: current
        moderator.moderate("ordinary harmless text")

    assert len(moderator._dynamic_literal_cache) <= (
        local_text_moderator._DYNAMIC_LITERAL_CACHE_SIZE
    )


def test_whole_dynamic_evaluation_budget_fails_closed_without_sleep():
    class AdvancingClock:
        def __init__(self):
            self.now = -0.03

        def __call__(self):
            self.now += 0.03
            return self.now

    class FastNonMatchingPattern:
        def __init__(self):
            self.searches = 0
            self.timeouts = []

        def search(self, text, timeout):
            self.searches += 1
            self.timeouts.append(timeout)
            return None

    patterns = tuple(FastNonMatchingPattern() for _ in range(20))
    regex_rules = tuple(
        CompiledRegexRule(
            rule=rule(rule_id=index, expression=f"private-regex-{index}", match_type="regex"),
            pattern=pattern,
        )
        for index, pattern in enumerate(patterns)
    )
    snapshot = ModerationSnapshot(
        version=60,
        literal_rules=(),
        regex_rules=regex_rules,
        loaded_at=1.0,
        verified_at=1.0,
    )

    with pytest.raises(ModerationUnavailable) as caught:
        ModerationService(
            LocalTextModerator(lambda: snapshot, monotonic=AdvancingClock())
        ).moderate_text("harmless private body", CONTEXT)

    searched = [pattern for pattern in patterns if pattern.searches]
    assert 0 < len(searched) < len(patterns)
    assert {timeout for pattern in searched for timeout in pattern.timeouts} == {0.02}
    assert caught.value.error_code.value == "MODERATION_UNAVAILABLE"
    assert caught.value.public_message == "内容审核服务暂不可用，请稍后重试"
    assert "private-regex" not in caught.value.public_message
    assert "harmless private body" not in caught.value.public_message


def test_dynamic_budget_is_checked_after_last_rule_evaluation():
    class ControlledClock:
        now = 0.0

        def __call__(self):
            return self.now

    class BudgetConsumingPattern:
        def __init__(self, clock):
            self.clock = clock

        def search(self, text, timeout):
            self.clock.now = local_text_moderator._DYNAMIC_EVALUATION_BUDGET + 0.01
            return None

    clock = ControlledClock()
    private_expression = "last-private-regex"
    dynamic_rule = rule(
        rule_id=61, expression=private_expression, match_type="regex"
    )
    snapshot = ModerationSnapshot(
        version=61,
        literal_rules=(),
        regex_rules=(
            CompiledRegexRule(
                rule=dynamic_rule,
                pattern=BudgetConsumingPattern(clock),
            ),
        ),
        loaded_at=1.0,
        verified_at=1.0,
    )

    with pytest.raises(ModerationUnavailable) as caught:
        ModerationService(
            LocalTextModerator(lambda: snapshot, monotonic=clock)
        ).moderate_text("harmless private body", CONTEXT)

    assert caught.value.error_code.value == "MODERATION_UNAVAILABLE"
    assert private_expression not in caught.value.public_message
    assert "harmless private body" not in caught.value.public_message


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
