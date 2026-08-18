from enum import Enum

from core.models import AURARequest


class PolicyDecision(str, Enum):
    """Possible policy outcomes for an AURA request."""

    ALLOW = "allow"
    DENY = "deny"


class Policy:
    """Basic policy boundary for AURA requests."""

    def evaluate(self, request: AURARequest) -> PolicyDecision:
        """Evaluate whether a request is allowed to proceed."""
        if not request.user_input.strip():
            return PolicyDecision.DENY

        return PolicyDecision.ALLOW