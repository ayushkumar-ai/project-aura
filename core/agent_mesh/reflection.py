"""M59 — Bounded Reflection & Self-Correction Engine.

Enables bounded replanning and retry decisions for transient failures.
Strictly prohibits privilege escalation, security policy alterations, or approval bypasses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from core.agent_mesh.types import AgentRunBudget, PlanStep, VerificationStatus

logger = logging.getLogger("aura.agent_mesh.reflection")


@dataclass
class ReflectionDecision:
    """Outcome of a reflection iteration."""
    should_retry: bool
    retry_reason: str
    revised_parameters: dict[str, Any] | None = None
    abort_execution: bool = False
    escalate_to_human: bool = False


class BoundedReflectionEngine:
    """Evaluates failed or inconclusive step executions within hard bounded budgets."""

    def evaluate_failure(
        self,
        step: PlanStep,
        verification_status: VerificationStatus,
        verification_detail: str,
        current_retry_count: int,
        budget: AgentRunBudget,
    ) -> ReflectionDecision:
        """Evaluate whether to retry, replan, or abort (Invariants M59-F38, M59-F39)."""
        # If max retries exceeded, abort immediately
        if current_retry_count >= budget.max_retries:
            return ReflectionDecision(
                should_retry=False,
                retry_reason=f"Max retries ({budget.max_retries}) exceeded.",
                abort_execution=True,
            )

        # Fatal errors cannot be retried (e.g. policy denial, permission error)
        if "policy denied" in verification_detail.lower() or "permission" in verification_detail.lower():
            return ReflectionDecision(
                should_retry=False,
                retry_reason="Fatal security or policy error; retries prohibited.",
                abort_execution=True,
            )

        if "approval" in verification_detail.lower():
            return ReflectionDecision(
                should_retry=False,
                retry_reason="Human approval required.",
                escalate_to_human=True,
            )

        # Transient / format error -> bounded retry with same permissions
        return ReflectionDecision(
            should_retry=True,
            retry_reason=f"Transient failure: {verification_detail}",
            revised_parameters=dict(step.parameters),
        )
