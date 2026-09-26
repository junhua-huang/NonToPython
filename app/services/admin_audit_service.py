from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.models import AdminAuditLog

logger = logging.getLogger(__name__)

_ALLOWED_METADATA_KEYS = {
    "status_before",
    "status_after",
    "report_status",
    "content_type",
    "error_code",
    "counts",
    "target_label",
}

_FORBIDDEN_KEY_FRAGMENTS = {
    "content",
    "body",
    "text",
    "description",
    "token",
    "secret",
    "signed_url",
    "cos_key",
    "raw_response",
    "exception",
    "stacktrace",
    "password",
}


def _truncate(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text[:limit]


def _request_ip(request) -> str | None:
    client = getattr(request, "client", None)
    host = getattr(client, "host", None)
    return _truncate(host, 64)


def _request_user_agent(request) -> str | None:
    headers = getattr(request, "headers", None)
    if not headers:
        return None
    try:
        return _truncate(headers.get("user-agent"), 255)
    except Exception:
        return None


def sanitize_audit_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    sanitized: dict[str, Any] = {}
    for key, value in metadata.items():
        normalized_key = str(key)
        lowered = normalized_key.lower()
        if normalized_key not in _ALLOWED_METADATA_KEYS:
            continue
        if any(fragment in lowered for fragment in _FORBIDDEN_KEY_FRAGMENTS):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            sanitized[normalized_key] = value
        elif isinstance(value, dict):
            sanitized[normalized_key] = {
                str(child_key): child_value
                for child_key, child_value in value.items()
                if not any(fragment in str(child_key).lower() for fragment in _FORBIDDEN_KEY_FRAGMENTS)
                and (isinstance(child_value, (str, int, float, bool)) or child_value is None)
            }
        elif isinstance(value, (list, tuple)):
            sanitized[normalized_key] = [
                item for item in value if isinstance(item, (str, int, float, bool)) or item is None
            ][:20]
    return sanitized


def _build_audit_log(
    *,
    admin_user_id: int,
    action: str,
    target_type: str,
    target_id: str | int | None = None,
    result: str = "success",
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    request=None,
) -> AdminAuditLog:
    sanitized_metadata = sanitize_audit_metadata(metadata)
    return AdminAuditLog(
        admin_user_id=admin_user_id,
        action=_truncate(action, 80) or "unknown",
        target_type=_truncate(target_type, 50) or "unknown",
        target_id=_truncate(target_id, 80),
        result=_truncate(result, 20) or "success",
        reason=_truncate(reason, 500),
        metadata_json=json.dumps(sanitized_metadata, ensure_ascii=False) if sanitized_metadata else None,
        ip_address=_request_ip(request),
        user_agent=_request_user_agent(request),
    )


def record_required_admin_audit(db: Session, **kwargs) -> None:
    """Attach a mandatory admin audit event to the caller's transaction."""
    db.add(_build_audit_log(**kwargs))


def record_admin_audit(
    db: Session,
    *,
    admin_user_id: int,
    action: str,
    target_type: str,
    target_id: str | int | None = None,
    result: str = "success",
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    request=None,
) -> None:
    """Best-effort audit logging for non-critical compatibility paths."""
    try:
        with db.begin_nested():
            db.add(
                _build_audit_log(
                    admin_user_id=admin_user_id,
                    action=action,
                    target_type=target_type,
                    target_id=target_id,
                    result=result,
                    reason=reason,
                    metadata=metadata,
                    request=request,
                )
            )
            db.flush()
    except Exception:
        logger.warning(
            "Admin audit write failed admin_user_id=%s action=%s target_type=%s",
            admin_user_id,
            type(action).__name__,
            type(target_type).__name__,
        )
