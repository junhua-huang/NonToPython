from dataclasses import dataclass
from enum import Enum
import math
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
    pattern: Any


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
        if type(self.decision) is not ModerationDecision:
            raise TypeError("decision must be ModerationDecision")
        if type(self.risk_category) is not RiskCategory:
            raise TypeError("risk_category must be RiskCategory")
        if self.severity is not None and type(self.severity) is not Severity:
            raise TypeError("severity must be Severity or None")
        if isinstance(self.confidence, bool) or not isinstance(
            self.confidence, (int, float)
        ):
            raise TypeError("confidence must be int or float")
        if isinstance(self.confidence, float) and not math.isfinite(self.confidence):
            raise ValueError("confidence must be finite")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.decision is ModerationDecision.APPROVE and self.severity is not None:
            raise ValueError("severity must be None when decision is APPROVE")
        if self.decision is ModerationDecision.REJECT and self.severity is None:
            raise ValueError("rejected result must have severity")
        if type(self.policy_version) is not str:
            raise TypeError("policy_version must be str")
        if not self.policy_version:
            raise ValueError("policy_version must not be empty")
        if type(self.rule_version) is not int:
            raise TypeError("rule_version must be int")
        if not isinstance(self.matched_rule_ids, tuple):
            raise TypeError("matched_rule_ids must be tuple")
        if any(type(rule_id) is not int for rule_id in self.matched_rule_ids):
            raise TypeError("matched_rule_ids items must be int")
