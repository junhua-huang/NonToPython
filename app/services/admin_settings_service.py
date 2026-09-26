from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import Config
from app.models.models import AdminSetting

_IMAGE_MODERATION_KEY = "image_moderation_enabled"
_ALLOWED_BOOLEAN_TEXT = {"true": True, "false": False}


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _parse_bool_text(value: str | None) -> bool | None:
    if not isinstance(value, str):
        return None
    return _ALLOWED_BOOLEAN_TEXT.get(value.strip().lower())


def env_image_moderation_enabled(config=Config) -> bool:
    return bool(getattr(config, "COS_CI_IMAGE_AUDIT_ENABLED", False))


def image_moderation_configured(config=Config) -> bool:
    return bool(getattr(config, "COS_BUCKET_NAME", "") or getattr(config, "COS_BUCKET", ""))


def get_image_moderation_setting_row(db: Session | None) -> AdminSetting | None:
    if db is None:
        return None
    try:
        return db.query(AdminSetting).filter(AdminSetting.key == _IMAGE_MODERATION_KEY).first()
    except Exception:
        return None


def effective_image_moderation_enabled(db: Session | None = None, *, config=Config) -> tuple[bool, str]:
    row = get_image_moderation_setting_row(db)
    parsed = _parse_bool_text(row.value if row else None)
    if parsed is not None:
        return parsed, "database"
    return env_image_moderation_enabled(config), "environment"


def serialize_moderation_settings(db: Session | None = None, *, config=Config) -> dict:
    enabled, source = effective_image_moderation_enabled(db, config=config)
    return {
        "image_moderation_enabled": enabled,
        "image_moderation_provider": "tencent_cos_ci",
        "image_moderation_configured": image_moderation_configured(config),
        "source": source,
    }


def set_image_moderation_enabled(db: Session, *, enabled: bool, admin_user_id: int) -> tuple[bool | None, bool]:
    row = db.query(AdminSetting).filter(AdminSetting.key == _IMAGE_MODERATION_KEY).first()
    previous = _parse_bool_text(row.value if row else None)
    if row is None:
        row = AdminSetting(key=_IMAGE_MODERATION_KEY, value=_bool_text(enabled), updated_by=admin_user_id)
        db.add(row)
    else:
        row.value = _bool_text(enabled)
        row.updated_by = admin_user_id
    db.flush()
    return previous, enabled
