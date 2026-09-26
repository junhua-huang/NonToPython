from dataclasses import dataclass
from enum import Enum
import math


class MediaModerationDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


def _require_non_empty_str(value: str, field_name: str) -> None:
    if type(value) is not str:
        raise TypeError(f"{field_name} must be str")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_optional_non_empty_str(value: str | None, field_name: str) -> None:
    if value is None:
        return
    if type(value) is not str:
        raise TypeError(f"{field_name} must be str or None")
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


@dataclass(frozen=True, slots=True)
class MediaModerationTarget:
    cos_key: str
    target_type: str
    actor_user_id: int | None
    upload_type: str
    is_public: bool
    content_type: str | None = None
    data_id: str | None = None

    def __post_init__(self) -> None:
        _require_non_empty_str(self.cos_key, "cos_key")
        _require_non_empty_str(self.target_type, "target_type")
        _require_non_empty_str(self.upload_type, "upload_type")
        if self.actor_user_id is not None and type(self.actor_user_id) is not int:
            raise TypeError("actor_user_id must be int or None")
        if type(self.is_public) is not bool:
            raise TypeError("is_public must be bool")
        _require_optional_non_empty_str(self.content_type, "content_type")
        _require_optional_non_empty_str(self.data_id, "data_id")


@dataclass(frozen=True, slots=True)
class MediaModerationResult:
    decision: MediaModerationDecision
    provider: str
    label: str | None = None
    category: str | None = None
    sub_label: str | None = None
    score: float | None = None
    job_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.decision) is not MediaModerationDecision:
            raise TypeError("decision must be MediaModerationDecision")
        _require_non_empty_str(self.provider, "provider")
        _require_optional_non_empty_str(self.label, "label")
        _require_optional_non_empty_str(self.category, "category")
        _require_optional_non_empty_str(self.sub_label, "sub_label")
        _require_optional_non_empty_str(self.job_id, "job_id")
        if self.score is not None:
            if isinstance(self.score, bool) or not isinstance(self.score, (int, float)):
                raise TypeError("score must be int, float, or None")
            if not math.isfinite(float(self.score)):
                raise ValueError("score must be finite")
            if not 0 <= float(self.score) <= 100:
                raise ValueError("score must be between 0 and 100")


_APPROVED_DISABLED = MediaModerationResult(
    decision=MediaModerationDecision.APPROVE,
    provider="disabled",
)


def approved_without_provider() -> MediaModerationResult:
    return _APPROVED_DISABLED
