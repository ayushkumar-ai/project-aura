from enum import Enum
from typing import Any

from core.models import AURARequest


class PolicyDecision(str, Enum):
    """Possible policy outcomes for an AURA request."""

    ALLOW = "allow"
    DENY = "deny"


class Policy:
    """Policy and authorization boundary for AURA requests and tools."""

    def __init__(
        self,
        authorized_tools: set[str] | None = None,
        privileged_tools: set[str] | None = None,
    ):
        self.authorized_tools = (
            authorized_tools
            if authorized_tools is not None
            else {
                "calculator",
                "echo",
                "text_transform",
                "json_query",
                "system_info",
                "http_mock",
                "analysis_tool",
                "execution_tool",
                "verification_tool",
            }
        )
        self.privileged_tools = (
            privileged_tools
            if privileged_tools is not None
            else {
                "system_info",
                "execution_tool",
                "device_action",
            }
        )

    def evaluate(self, request: AURARequest) -> PolicyDecision:
        """Evaluate whether a request is allowed to proceed."""
        if not request.user_input.strip():
            return PolicyDecision.DENY

        identity = getattr(request, "identity", None)
        if identity is not None:
            # Reject expired credentials
            if hasattr(identity, "is_expired") and identity.is_expired():
                return PolicyDecision.DENY

        return PolicyDecision.ALLOW

    def authorize_tool(self, tool_name: str, identity: Any | None = None) -> PolicyDecision:
        """Authorize execution of a registered tool, with optional principal RBAC verification."""
        if tool_name not in self.authorized_tools:
            return PolicyDecision.DENY

        if identity is not None and tool_name in self.privileged_tools:
            # Check if principal has administrative/operator role or device execution scope
            is_auth = getattr(identity, "is_authenticated", False)
            if not is_auth:
                return PolicyDecision.DENY

            has_admin_role = (
                (hasattr(identity, "has_role") and (identity.has_role("admin") or identity.has_role("operator")))
                or getattr(identity, "role", "") in ("admin", "operator")
            )
            has_exec_scope = (
                hasattr(identity, "has_scope")
                and (identity.has_scope("aura:device:exec") or identity.has_scope("aura:admin"))
            )

            if not (has_admin_role or has_exec_scope):
                return PolicyDecision.DENY

        return PolicyDecision.ALLOW
