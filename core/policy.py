from enum import Enum

from core.models import AURARequest


class PolicyDecision(str, Enum):
    """Possible policy outcomes for an AURA request."""

    ALLOW = "allow"
    DENY = "deny"


class Policy:
    """Basic policy boundary for AURA requests."""

    def __init__(self, authorized_tools: set[str] | None = None):
        self.authorized_tools = (
            authorized_tools
            if authorized_tools is not None
            else {"calculator", "echo"}
        )

    def evaluate(self, request: AURARequest) -> PolicyDecision:
        """Evaluate whether a request is allowed to proceed."""
        if not request.user_input.strip():
            return PolicyDecision.DENY

        return PolicyDecision.ALLOW

    def authorize_tool(self, tool_name: str) -> PolicyDecision:
        """Authorize execution of a registered tool."""
        if tool_name in self.authorized_tools:
            return PolicyDecision.ALLOW

        return PolicyDecision.DENY
