from __future__ import annotations

from fastapi import HTTPException

from app.services.media_moderation_service import MediaModerationService
from app.services.media_moderation_types import MediaModerationTarget
from app.services.moderation_errors import AppContractError, ContentRejected, to_http_exception
from app.services.moderation_event_service import record_moderation_event

_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff", "tif", "heic", "heif", "avif", "apng", "jfif"}
_MEDIA_MODERATION_SKIP_PREFIXES = ("chat/",)

media_moderation_service = MediaModerationService()


def is_moderated_image_key(cos_key: str | None, *, content_type: str | None = None) -> bool:
    if not cos_key or type(cos_key) is not str:
        return False
    normalized = cos_key.strip().lstrip("/")
    if not normalized or ".." in normalized:
        return False
    if normalized.startswith(_MEDIA_MODERATION_SKIP_PREFIXES):
        return False
    if content_type and content_type.lower().startswith("image/"):
        return True
    suffix = normalized.rsplit(".", 1)[-1].lower() if "." in normalized else ""
    return suffix in _IMAGE_EXTENSIONS


def moderate_cos_image_or_raise(
    cos_key: str,
    *,
    target_type: str,
    actor_user_id: int | None,
    upload_type: str,
    is_public: bool,
    content_type: str | None = None,
    data_id: str | None = None,
    service: MediaModerationService | None = None,
    db=None,
    route_key: str | None = None,
):
    if not is_moderated_image_key(cos_key, content_type=content_type):
        return None
    active_service = service or media_moderation_service
    try:
        return active_service.moderate(
            MediaModerationTarget(
                cos_key=cos_key,
                target_type=target_type,
                actor_user_id=actor_user_id,
                upload_type=upload_type,
                is_public=is_public,
                content_type=content_type,
                data_id=data_id,
            )
        )
    except ContentRejected as exc:
        record_moderation_event(
            db,
            provider="tencent_cos_ci",
            content_type="image",
            route_key=route_key,
            target_type=target_type,
            target_id=data_id,
            actor_user_id=actor_user_id,
            decision="reject",
            error_code=exc.error_code.value,
        )
        raise to_http_exception(exc) from None
    except AppContractError as exc:
        record_moderation_event(
            db,
            provider="tencent_cos_ci",
            content_type="image",
            route_key=route_key,
            target_type=target_type,
            target_id=data_id,
            actor_user_id=actor_user_id,
            decision="unavailable",
            error_code=exc.error_code.value,
        )
        raise to_http_exception(exc) from None


def cleanup_and_raise_moderation_http(error: HTTPException, cleanup) -> None:
    try:
        cleanup()
    except Exception:
        pass
    raise error
