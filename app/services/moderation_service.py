from collections.abc import Mapping

from app.services.moderation_errors import ContentRejected, ModerationUnavailable
from app.services.moderation_policy import ModerationPolicy
from app.services.moderation_types import (
    ModerationContext,
    ModerationDecision,
    ModerationResult,
)


class ModerationService:
    def __init__(self, local_moderator=None, policy: ModerationPolicy | None = None):
        self.local_moderator = local_moderator
        self.policy = policy or ModerationPolicy()

    def moderate_text(
        self,
        text: str | None,
        context: ModerationContext,
    ) -> ModerationResult:
        if self.local_moderator is None:
            raise ModerationUnavailable(
                RuntimeError("local moderator not initialized")
            )
        try:
            result = self.local_moderator.moderate(text or "")
            decision = self.policy.decide(context, result)
        except ContentRejected:
            raise
        except Exception as exc:
            raise ModerationUnavailable(exc) from exc
        if decision is ModerationDecision.REJECT:
            raise ContentRejected()
        return result

    def moderate_fields(
        self,
        fields: Mapping[str, str | None],
        context: ModerationContext,
    ) -> tuple[ModerationResult, ...]:
        return tuple(
            self.moderate_text(value, context)
            for value in fields.values()
            if value is not None and value.strip()
        )


moderation_service = ModerationService()
