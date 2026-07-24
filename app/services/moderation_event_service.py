from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import ModerationEvent

logger = logging.getLogger(__name__)

_ALLOWED_DECISIONS = {"approve", "reject", "unavailable"}
_FORBIDDEN_VALUE_FRAGMENTS = {
    "token",
    "secret",
    "signed_url",
    "cos_key",
    "raw_response",
    "exception",
    "stacktrace",
    "hit_words",
}


def _safe_str(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower()
    if any(fragment in lowered for fragment in _FORBIDDEN_VALUE_FRAGMENTS):
        return None
    return text[:limit]


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is int:
        return value
    return None


def _safe_score(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = float(value)
    if not 0 <= score <= 100:
        return None
    return score


def record_moderation_event(
    db: Session | None,
    *,
    provider: str,
    content_type: str,
    decision: str,
    route_key: str | None = None,
    target_type: str | None = None,
    target_id: str | int | None = None,
    actor_user_id: int | None = None,
    error_code: str | None = None,
    label: str | None = None,
    category: str | None = None,
    score: float | None = None,
) -> None:
    """Persist moderation metadata only; never blocks the moderation decision."""
    if db is None:
        return
    try:
        safe_decision = decision if decision in _ALLOWED_DECISIONS else "unavailable"
        with db.begin_nested():
            db.add(
                ModerationEvent(
                    provider=_safe_str(provider, 50) or "unknown",
                    content_type=_safe_str(content_type, 20) or "unknown",
                    route_key=_safe_str(route_key, 120),
                    target_type=_safe_str(target_type, 80),
                    target_id=_safe_str(target_id, 80),
                    actor_user_id=_safe_int(actor_user_id),
                    decision=safe_decision,
                    error_code=_safe_str(error_code, 50),
                    label=_safe_str(label, 80),
                    category=_safe_str(category, 80),
                    score=_safe_score(score),
                )
            )
            db.flush()
    except Exception:
        logger.warning(
            "Moderation event write failed provider_type=%s content_type=%s decision=%s",
            type(provider).__name__,
            _safe_str(content_type, 20) or "unknown",
            type(decision).__name__,
        )
