import unicodedata
from collections.abc import Callable

import regex

from app.services.moderation_types import (
    ModerationDecision,
    ModerationResult,
    ModerationSnapshot,
    RiskCategory,
    Severity,
)


_REGEX_TIMEOUT = 0.02
_DEFAULT_IGNORABLE = regex.compile(r"\p{Default_Ignorable_Code_Point}+")
_HAS_CJK = regex.compile(r"\p{Script=Han}")
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


def _literal_matches(text: str, expression: str, *, baseline: bool) -> bool:
    literal = normalize_text(expression)
    if _HAS_CJK.search(literal, timeout=_REGEX_TIMEOUT):
        characters = [character for character in literal if not character.isspace()]
        pattern = r"\s*".join(regex.escape(character) for character in characters)
        return bool(regex.search(pattern, text, timeout=_REGEX_TIMEOUT))
    if baseline and literal.isascii() and all(
        character.isalnum() or character.isspace() for character in literal
    ):
        pattern = rf"(?<![\p{{L}}\p{{N}}]){regex.escape(literal)}(?![\p{{L}}\p{{N}}])"
        return bool(regex.search(pattern, text, timeout=_REGEX_TIMEOUT))
    return literal in text


class LocalTextModerator:
    def __init__(self, snapshot_provider: Callable[[], ModerationSnapshot]):
        self._snapshot_provider = snapshot_provider

    def moderate(self, text: str) -> ModerationResult:
        normalized = normalize_text(text)
        snapshot = self._snapshot_provider()
        if type(snapshot) is not ModerationSnapshot:
            raise TypeError("snapshot provider must return ModerationSnapshot")

        category = RiskCategory.OTHER
        severity = None
        confidence = 1.0
        matched_rule_ids: list[int] = []

        for literals, literal_category, literal_severity in _BASELINE_LITERAL_GROUPS:
            if any(
                _literal_matches(normalized, literal, baseline=True)
                for literal in literals
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
                for rule in snapshot.literal_rules:
                    if _literal_matches(normalized, rule.expression, baseline=False):
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
                        rule = compiled_rule.rule
                        if compiled_rule.pattern.search(
                            normalized, timeout=_REGEX_TIMEOUT
                        ):
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
