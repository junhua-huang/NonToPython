import threading
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import regex

from app.services.moderation_types import (
    ModerationDecision,
    ModerationResult,
    ModerationSnapshot,
    RiskCategory,
    Severity,
)


_REGEX_TIMEOUT = 0.02
_DYNAMIC_EVALUATION_BUDGET = 0.1
_DYNAMIC_LITERAL_CACHE_SIZE = 8
_DEFAULT_IGNORABLE = regex.compile(r"\p{Default_Ignorable_Code_Point}+")
_HAS_EAST_ASIAN = regex.compile(
    r"[\p{Script_Extensions=Han}\p{Script_Extensions=Hiragana}"
    r"\p{Script_Extensions=Katakana}\p{Script_Extensions=Hangul}]"
)
_WORD_CHARACTER = r"\p{L}\p{M}\p{N}\p{Connector_Punctuation}"
_BASELINE_LITERAL_GROUPS = (
    (
        frozenset(
            {
                "色情",
                "淫秽",
                "裸体",
                "裸聊",
                "嫖娼",
                "卖淫",
                "黄色网站",
                "约炮",
                "porn",
                "xxx",
                "escort",
                "naked",
                "nude",
                "hentai",
            }
        ),
        RiskCategory.SEXUAL,
        Severity.HIGH,
    ),
    (
        frozenset(
            {
                "赌博",
                "赌场",
                "赌球",
                "六合彩",
                "百家乐",
                "博彩",
                "毒品",
                "吸毒",
                "大麻",
                "海洛因",
                "冰毒",
                "可卡因",
                "代开发票",
                "casino",
                "gambling",
                "cocaine",
                "heroin",
                "marijuana",
                "meth",
                "hacking",
                "fake id",
                "counterfeit",
            }
        ),
        RiskCategory.ILLEGAL,
        Severity.HIGH,
    ),
    (
        frozenset(
            {
                "杀人",
                "绑架",
                "恐怖袭击",
                "爆炸",
                "枪支",
                "kill you",
                "murder",
                "massacre",
                "terrorist",
            }
        ),
        RiskCategory.VIOLENCE,
        Severity.HIGH,
    ),
    (
        frozenset(
            {
                "种族歧视",
                "性别歧视",
                "地域歧视",
                "宗教歧视",
                "hate speech",
                "nazi",
                "法西斯",
                "种族主义",
            }
        ),
        RiskCategory.HATE,
        Severity.HIGH,
    ),
    (
        frozenset({"kill yourself", "去死", "弄死你", "人肉", "人身攻击", "dox"}),
        RiskCategory.ABUSE,
        Severity.HIGH,
    ),
)
_SPAM = regex.compile(
    r"(?:加\s*(?:微\s*信|v)|wx)\s*[:：-]?\s*[a-z0-9_-]{5,}",
    regex.IGNORECASE,
)
_HATE = regex.compile(r"(?:全部|都)\s*(?:滚|去死)", regex.IGNORECASE)


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = _DEFAULT_IGNORABLE.sub("", normalized, timeout=_REGEX_TIMEOUT)
    return " ".join(normalized.casefold().split())


@dataclass(frozen=True, slots=True)
class _LiteralMatcher:
    literal: str
    pattern: Any | None = None
    ignores_whitespace: bool = False

    def matches(self, text: str, text_without_whitespace: str | None = None) -> bool:
        if self.pattern is not None:
            return bool(self.pattern.search(text, timeout=_REGEX_TIMEOUT))
        if self.ignores_whitespace:
            compact_text = (
                text.replace(" ", "")
                if text_without_whitespace is None
                else text_without_whitespace
            )
            return self.literal in compact_text
        return self.literal in text


def _prepare_literal_matcher(expression: str, *, baseline: bool) -> _LiteralMatcher:
    literal = normalize_text(expression)
    if not literal:
        raise ValueError("literal rule must not normalize to empty")
    if _HAS_EAST_ASIAN.search(literal, timeout=_REGEX_TIMEOUT):
        return _LiteralMatcher(literal.replace(" ", ""), ignores_whitespace=True)
    if baseline and literal.isascii() and all(
        character.isalnum() or character.isspace() for character in literal
    ):
        pattern = regex.compile(
            rf"(?<![{_WORD_CHARACTER}]){regex.escape(literal)}"
            rf"(?![{_WORD_CHARACTER}])"
        )
        return _LiteralMatcher(literal, pattern)
    return _LiteralMatcher(literal)


def _literal_matches(text: str, expression: str, *, baseline: bool) -> bool:
    return _prepare_literal_matcher(expression, baseline=baseline).matches(text)


_PREPARED_BASELINE_LITERAL_GROUPS = tuple(
    (
        tuple(
            _prepare_literal_matcher(literal, baseline=True) for literal in literals
        ),
        category,
        severity,
    )
    for literals, category, severity in _BASELINE_LITERAL_GROUPS
)


class _CacheLock(Protocol):
    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool: ...

    def release(self) -> None: ...


class LocalTextModerator:
    def __init__(
        self,
        snapshot_provider: Callable[[], ModerationSnapshot],
        *,
        monotonic: Callable[[], float] = time.monotonic,
        _cache_lock: _CacheLock | None = None,
    ):
        self._snapshot_provider = snapshot_provider
        self._monotonic = monotonic
        self._dynamic_literal_cache: OrderedDict[
            int, tuple[ModerationSnapshot, tuple[_LiteralMatcher, ...]]
        ] = OrderedDict()
        self._dynamic_literal_cache_lock = (
            threading.RLock() if _cache_lock is None else _cache_lock
        )

    def _dynamic_literal_matchers(
        self, snapshot: ModerationSnapshot, deadline: float
    ) -> tuple[_LiteralMatcher, ...]:
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise TimeoutError("dynamic moderation rule evaluation timed out")
        if not self._dynamic_literal_cache_lock.acquire(timeout=remaining):
            raise TimeoutError("dynamic moderation rule evaluation timed out")

        try:
            self._check_dynamic_deadline(deadline)
            cache_key = id(snapshot)
            cached = self._dynamic_literal_cache.get(cache_key)
            if cached is not None and cached[0] is snapshot:
                self._dynamic_literal_cache.move_to_end(cache_key)
                return cached[1]

            candidate = []
            for rule in snapshot.literal_rules:
                candidate.append(
                    _prepare_literal_matcher(rule.expression, baseline=False)
                )
                self._check_dynamic_deadline(deadline)
            matchers = tuple(candidate)
            self._check_dynamic_deadline(deadline)
            self._dynamic_literal_cache[cache_key] = (snapshot, matchers)
            self._dynamic_literal_cache.move_to_end(cache_key)
            while len(self._dynamic_literal_cache) > _DYNAMIC_LITERAL_CACHE_SIZE:
                self._dynamic_literal_cache.popitem(last=False)
            return matchers
        finally:
            self._dynamic_literal_cache_lock.release()

    def _check_dynamic_deadline(self, deadline: float) -> None:
        if self._monotonic() >= deadline:
            raise TimeoutError("dynamic moderation rule evaluation timed out")

    def _check_dynamic_budget(self, started_at: float) -> None:
        if self._monotonic() - started_at > _DYNAMIC_EVALUATION_BUDGET:
            raise TimeoutError("dynamic moderation rule evaluation timed out")

    def moderate(self, text: str) -> ModerationResult:
        normalized = normalize_text(text)
        normalized_without_whitespace = normalized.replace(" ", "")
        snapshot = self._snapshot_provider()
        if type(snapshot) is not ModerationSnapshot:
            raise TypeError("snapshot provider must return ModerationSnapshot")

        category = RiskCategory.OTHER
        severity = None
        confidence = 1.0
        matched_rule_ids: list[int] = []
        started_at = self._monotonic()
        deadline = started_at + _DYNAMIC_EVALUATION_BUDGET
        literal_matchers = self._dynamic_literal_matchers(snapshot, deadline)

        for matchers, literal_category, literal_severity in (
            _PREPARED_BASELINE_LITERAL_GROUPS
        ):
            if any(
                matcher.matches(normalized, normalized_without_whitespace)
                for matcher in matchers
            ):
                category = literal_category
                severity = literal_severity
                break
        else:
            if _SPAM.search(normalized, timeout=_REGEX_TIMEOUT):
                category = RiskCategory.SPAM
                severity = Severity.MEDIUM
                confidence = 0.98
            elif _HATE.search(normalized, timeout=_REGEX_TIMEOUT):
                category = RiskCategory.HATE
                severity = Severity.HIGH
                confidence = 0.98
            else:
                for rule, matcher in zip(snapshot.literal_rules, literal_matchers):
                    self._check_dynamic_budget(started_at)
                    matched = matcher.matches(
                        normalized, normalized_without_whitespace
                    )
                    self._check_dynamic_budget(started_at)
                    if matched:
                        category = rule.category
                        severity = rule.severity
                        confidence = 1.0
                        if rule.id is not None:
                            if type(rule.id) is not int:
                                raise TypeError("rule id must be int or None")
                            matched_rule_ids.append(rule.id)
                        break
                else:
                    for compiled_rule in snapshot.regex_rules:
                        remaining = deadline - self._monotonic()
                        if remaining <= 0:
                            raise TimeoutError(
                                "dynamic moderation rule evaluation timed out"
                            )
                        rule = compiled_rule.rule
                        matched = compiled_rule.pattern.search(
                            normalized, timeout=min(_REGEX_TIMEOUT, remaining)
                        )
                        self._check_dynamic_budget(started_at)
                        if matched:
                            category = rule.category
                            severity = rule.severity
                            confidence = 0.99
                            if rule.id is not None:
                                if type(rule.id) is not int:
                                    raise TypeError("rule id must be int or None")
                                matched_rule_ids.append(rule.id)
                            break

        rejected = severity is not None
        return ModerationResult(
            decision=(
                ModerationDecision.REJECT
                if rejected
                else ModerationDecision.APPROVE
            ),
            risk_category=category,
            severity=severity,
            confidence=confidence,
            policy_version="phase1-local-v1",
            rule_version=snapshot.version,
            matched_rule_ids=tuple(matched_rule_ids),
        )
