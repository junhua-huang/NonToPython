from __future__ import annotations

import logging

from app.core.config import Config
from app.services.media_moderation_types import (
    MediaModerationDecision,
    MediaModerationResult,
    MediaModerationTarget,
    approved_without_provider,
)
from app.services.moderation_errors import ContentRejected, ModerationUnavailable
from app.services.tencent_cos_image_moderator import (
    TencentCosImageModerationError,
    TencentCosImageModerator,
)

logger = logging.getLogger(__name__)
_DEFAULT_PROVIDER = object()
_ALLOWED_LOG_PROVIDER_NAMES = {"disabled", "tencent_cos_ci"}


def _safe_provider_name(provider_name: str) -> str:
    if provider_name in _ALLOWED_LOG_PROVIDER_NAMES:
        return provider_name
    return "tencent_cos_ci"


class MediaModerationService:
    def __init__(self, provider=_DEFAULT_PROVIDER, config=Config):
        self._provider = provider
        self._config = config

    def moderate(self, target: MediaModerationTarget) -> MediaModerationResult:
        if not getattr(self._config, "COS_CI_IMAGE_AUDIT_ENABLED", False):
            result = approved_without_provider()
            logger.info(
                "media_moderation_skipped target_type=%s actor_user_id=%s upload_type=%s decision=%s provider=%s",
                target.target_type,
                target.actor_user_id,
                target.upload_type,
                result.decision.value,
                _safe_provider_name(result.provider),
            )
            return result

        try:
            provider = (
                TencentCosImageModerator(config=self._config)
                if self._provider is _DEFAULT_PROVIDER
                else self._provider
            )
            result = provider.moderate(target)
        except TencentCosImageModerationError as exc:
            logger.warning(
                "media_moderation_unavailable target_type=%s actor_user_id=%s upload_type=%s error_type=%s",
                target.target_type,
                target.actor_user_id,
                target.upload_type,
                type(exc).__name__,
            )
            raise ModerationUnavailable(exc) from None
        except Exception as exc:
            logger.warning(
                "media_moderation_unavailable target_type=%s actor_user_id=%s upload_type=%s error_type=%s",
                target.target_type,
                target.actor_user_id,
                target.upload_type,
                type(exc).__name__,
            )
            raise ModerationUnavailable() from None

        if result.decision is MediaModerationDecision.REJECT:
            logger.warning(
                "media_moderation_rejected target_type=%s actor_user_id=%s upload_type=%s decision=%s provider=%s",
                target.target_type,
                target.actor_user_id,
                target.upload_type,
                result.decision.value,
                _safe_provider_name(result.provider),
            )
            raise ContentRejected()

        logger.info(
            "media_moderation_approved target_type=%s actor_user_id=%s upload_type=%s decision=%s provider=%s",
            target.target_type,
            target.actor_user_id,
            target.upload_type,
            result.decision.value,
            _safe_provider_name(result.provider),
        )
        return result
