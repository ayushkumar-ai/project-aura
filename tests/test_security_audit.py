"""Tests for Structured Security Audit Stream & RBAC Observability (M44)."""

import pytest

from core.auth import TokenAuthenticator, create_token_authenticator
from core.identity import UserIdentity, UserRole, UserScope
from core.metrics import get_metrics_registry
from core.policy import Policy, PolicyDecision
from core.security_audit import (
    SecurityAuditEvent,
    SecurityEventType,
    get_security_audit_logger,
)


def test_auth_success_and_failure_audit_events():
    metrics = get_metrics_registry()
    metrics.reset_all()

    audit = get_security_audit_logger()
    audit.clear()

    auth = create_token_authenticator(master_key="admin_secret_token_123")

    # 1. Test missing credentials -> AUTH_FAILURE
    res1 = auth.authenticate(None)
    assert res1.success is False

    events = audit.get_events(event_type=SecurityEventType.AUTH_FAILURE)
    assert len(events) >= 1
    assert events[-1].outcome == "deny"
    assert "Missing" in events[-1].reason

    # 2. Test invalid credentials -> AUTH_FAILURE
    res2 = auth.authenticate("invalid_random_token")
    assert res2.success is False

    events = audit.get_events(event_type=SecurityEventType.AUTH_FAILURE)
    assert len(events) >= 2
    assert "Invalid" in events[-1].reason

    # 3. Test valid master token -> AUTH_SUCCESS
    res3 = auth.authenticate("admin_secret_token_123")
    assert res3.success is True
    assert res3.identity is not None

    success_events = audit.get_events(event_type=SecurityEventType.AUTH_SUCCESS)
    assert len(success_events) >= 1
    assert success_events[-1].outcome == "allow"
    assert success_events[-1].user_id == "aura_admin"


def test_policy_authorization_denial_audit_events():
    audit = get_security_audit_logger()
    audit.clear()

    pol = Policy(
        authorized_tools={"calculator", "system_info"},
        privileged_tools={"system_info"},
    )

    # 1. Unlisted tool -> TOOL_POLICY_VIOLATION
    decision1 = pol.authorize_tool("unauthorized_shell_tool")
    assert decision1 == PolicyDecision.DENY

    tool_violations = audit.get_events(event_type=SecurityEventType.TOOL_POLICY_VIOLATION)
    assert len(tool_violations) >= 1
    assert tool_violations[-1].outcome == "deny"

    # 2. Privileged tool without admin role -> AUTHORIZATION_DENIED
    standard_user = UserIdentity(
        user_id="usr_normal",
        username="NormalUser",
        roles=frozenset({UserRole.USER}),
        scopes=frozenset({UserScope.RUN.value}),
        is_authenticated=True,
    )
    decision2 = pol.authorize_tool("system_info", identity=standard_user)
    assert decision2 == PolicyDecision.DENY

    denials = audit.get_events(event_type=SecurityEventType.AUTHORIZATION_DENIED)
    assert len(denials) >= 1
    assert denials[-1].outcome == "deny"
    assert denials[-1].user_id == "usr_normal"
