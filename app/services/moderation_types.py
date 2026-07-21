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
        if type(self.rule_version) is not int:
            raise TypeError("rule_version must be int")
