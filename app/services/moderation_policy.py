from app.services.moderation_types import (
    ModerationContext,
    ModerationDecision,
    ModerationResult,
)


class ModerationPolicy:
    version = "phase1-local-v1"

    def decide(
        self,
        context: ModerationContext,
        local_result: ModerationResult,
    ) -> ModerationDecision:
        del context
        return local_result.decision
