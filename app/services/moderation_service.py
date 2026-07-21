from collections.abc import Mapping
from typing import Protocol

from app.services.moderation_errors import (
    AppContractError,
    ContentRejected,
    ModerationUnavailable,
)
from app.services.moderation_policy import ModerationPolicy
from app.services.moderation_types import (
    ModerationContext,
    ModerationDecision,
    ModerationResult,
)


class _LocalModerator(Protocol):
    def moderate(self, text: str) -> ModerationResult: ...


class ModerationService:
    def __init__(
        self,
        local_moderator: _LocalModerator | None = None,
        policy: ModerationPolicy | None = None,
    ):
        self.local_moderator = local_moderator
        self.policy = ModerationPolicy() if policy is None else policy

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
            result = self.local_moderator.moderate("" if text is None else text)
            if type(result) is not ModerationResult:
                raise TypeError("local moderator must return ModerationResult")
            decision = self.policy.decide(context, result)
            if decision is ModerationDecision.REJECT:
                raise ContentRejected()
            if decision is not ModerationDecision.APPROVE:
                raise ValueError("policy must return APPROVE or REJECT")
            return result
        except AppContractError:
            raise
        except Exception as exc:
            raise ModerationUnavailable(exc) from exc

    def moderate_fields(
        self,
        fields: Mapping[str, str | None],
        context: ModerationContext,
    ) -> tuple[ModerationResult, ...]:
        try:
            return self._moderate_fields(fields, context)
        except AppContractError:
            raise
        except Exception as exc:
            raise ModerationUnavailable(exc) from exc

    def _moderate_fields(
        self,
        fields: Mapping[str, str | None],
        context: ModerationContext,
    ) -> tuple[ModerationResult, ...]:
        results = []
        for value in fields.values():
            if value is None:
                continue
            if not isinstance(value, str):
                raise TypeError("moderation field values must be str or None")
            if not value.strip():
                continue
            results.append(self.moderate_text(value, context))
        return tuple(results)


moderation_service = ModerationService()
