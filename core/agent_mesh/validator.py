"""M59 — Plan & Policy Validator.

Enforces schema validation, capability checks, fail-closed policy gating,
and M48 human approval token verification prior to action dispatch.
"""

from __future__ import annotations

import logging
from typing import Any

from core.agent_mesh.types import ExecutionPlan, PlanStep
from core.platform.security import PlatformSecurityManager
from core.platform.types import CapabilityRiskLevel

logger = logging.getLogger("aura.agent_mesh.validator")


class PlanValidator:
    """Validates execution plans against security, policy, and human approval constraints."""

    def __init__(
        self,
        policy_engine: Any | None = None,
        approval_engine: Any | None = None,
        security_manager: PlatformSecurityManager | None = None,
    ):
        self.policy_engine = policy_engine
        self.approval_engine = approval_engine
        self.security = security_manager or PlatformSecurityManager()

    def validate_plan(self, tenant_id: str, plan: ExecutionPlan) -> None:
        """Validate entire execution plan structure and steps (Invariant M59-F12)."""
        if not plan or not plan.steps:
            raise ValueError("Execution plan must contain at least one step.")

        if len(plan.steps) > 50:
            raise ValueError(f"Plan exceeds maximum step limit (found {len(plan.steps)} steps, max 50).")

        for step in plan.steps:
            self.validate_step_pre_execution(tenant_id, step)

    def validate_step_pre_execution(
        self,
        tenant_id: str,
        step: PlanStep,
        approval_token: str | None = None,
    ) -> None:
        """Validate a single step before dispatch (Invariants M59-F08, M59-F09, M59-F21)."""
        if not step.action_name:
            raise ValueError(f"Step {step.step_number} is missing an action name.")

        # 1. Policy Engine Evaluation (Invariant M59-F08)
        if self.policy_engine:
            action_key = f"{step.action_type.value}:{step.action_name}"
            allowed = self.policy_engine.evaluate(
                action=action_key,
                tenant_id=tenant_id,
                context={"risk_level": step.risk_level.value, "step_number": step.step_number},
            )
            if not allowed:
                raise PermissionError(f"Policy denied execution of step {step.step_number} ({action_key}).")

        # 2. Human Approval Check for High/Critical Risk (Invariant M59-F09)
        if step.risk_level in (CapabilityRiskLevel.HIGH, CapabilityRiskLevel.CRITICAL) or step.requires_approval:
            if not approval_token:
                raise PermissionError(
                    f"Step {step.step_number} ({step.action_name}) is classified as {step.risk_level.value.upper()} risk "
                    f"and requires a valid M48 human approval token."
                )
            if self.approval_engine:
                is_valid = self.approval_engine.validate_token(
                    token=approval_token,
                    tenant_id=tenant_id,
                    action_type=f"{step.action_type.value}:{step.action_name}",
                )
                if not is_valid:
                    raise PermissionError(f"Invalid or expired approval token for step {step.step_number}.")

        # 3. Path & Command Safety Check
        if step.action_type.value == "device":
            self.security.validate_command_safety(step.action_name)
            if "path" in step.parameters:
                # Assert sandboxed path validity
                self.security.sanitize_path(tenant_id, step.parameters["path"])
