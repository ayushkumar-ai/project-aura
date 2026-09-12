"""Milestone 27: Remediation Planner.

Maps a FaultDiagnosticReport to a bounded, ordered RemediationPlan.
Applies the multi-tier remediation strategy:
  Tier 1 — retry / re-execute (safest)
  Tier 2 — replan / patch / inject adapter
  Tier 3 — selective saga rollback
  Tier 4 — operator escalation (highest gate)

The planner NEVER:
- Modifies existing runtime state directly.
- Bypasses Policy or ApprovalGateway.
- Claims to succeed if evidence confidence is insufficient.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import uuid4

from core.fault_types import (
    ConfidenceLevel,
    FaultCategory,
    FaultDiagnosticReport,
    HealingBudget,
    RemediationAction,
    RemediationActionType,
    RemediationPlan,
)

logger = logging.getLogger("aura.remediation_planner")

# Maximum number of actions in a single plan
MAX_PLAN_ACTIONS = 4


class RemediationPlanner:
    """Derives a bounded, ordered RemediationPlan from a FaultDiagnosticReport.

    The planner is stateless — it reads a diagnosis and returns a plan. It does
    not execute anything. Security authorities (Policy, ApprovalGateway) are
    referenced by name in action metadata so the orchestrator can gate them.
    """

    def plan(
        self,
        fault_report: FaultDiagnosticReport,
        budget: HealingBudget | None = None,
        context: dict[str, Any] | None = None,
    ) -> RemediationPlan:
        """Derive a RemediationPlan for the given fault.

        Args:
            fault_report: Diagnosed fault from CausalFaultAnalyzer.
            budget: Healing budget; defaults to HealingBudget().
            context: Additional planning hints (e.g. skill_name, artifact_id).

        Returns:
            An immutable RemediationPlan with ordered, bounded actions.
        """
        if not isinstance(fault_report, FaultDiagnosticReport):
            raise TypeError("fault_report must be a FaultDiagnosticReport instance.")

        budget = budget if budget is not None else HealingBudget()
        ctx = context or {}
        plan_id = f"rp_{uuid4().hex[:12]}"

        actions: list[RemediationAction] = []
        rationale_parts: list[str] = []
        is_operator_escalation = False

        category = fault_report.fault_category
        confidence = fault_report.confidence_level

        # Insufficient confidence → do not attempt autonomous repair
        if confidence == ConfidenceLevel.INSUFFICIENT:
            logger.warning(
                "RemediationPlanner: insufficient evidence for campaign=%s phase=%s — escalating.",
                fault_report.campaign_id,
                fault_report.phase_id,
            )
            actions.append(self._make_escalation_action(
                fault_report,
                rationale="Confidence insufficient for autonomous repair.",
            ))
            is_operator_escalation = True
            rationale_parts.append("Confidence INSUFFICIENT → operator escalation only.")

        elif category == FaultCategory.POLICY_SECURITY_BLOCK:
            # Policy blocks must always escalate — never attempt autonomous override
            logger.info(
                "RemediationPlanner: policy block for campaign=%s phase=%s — escalating to operator.",
                fault_report.campaign_id, fault_report.phase_id,
            )
            actions.append(self._make_escalation_action(
                fault_report,
                rationale="Policy or ApprovalGateway denied action. Operator clarification required.",
            ))
            is_operator_escalation = True
            rationale_parts.append("POLICY_SECURITY_BLOCK → operator escalation only.")

        elif category == FaultCategory.TRANSIENT_INFRASTRUCTURE:
            # Tier 1: simple retry
            if budget.max_retries > 0:
                actions.append(RemediationAction(
                    action_id=f"ra_{uuid4().hex[:8]}",
                    action_type=RemediationActionType.RETRY_PHASE,
                    target_id=fault_report.phase_id,
                    tier=1,
                    requires_approval=False,
                    parameters={
                        "goal_id": fault_report.goal_id,
                        "retry_delay_seconds": "2.0",
                    },
                    rationale="Transient infrastructure failure — retry is safe.",
                    estimated_duration_seconds=5.0,
                ))
                rationale_parts.append("TRANSIENT_INFRASTRUCTURE → tier-1 retry.")
            else:
                # Budget has no retries — escalate
                actions.append(self._make_escalation_action(
                    fault_report,
                    rationale="Transient failure but retry budget exhausted.",
                ))
                rationale_parts.append("Retry budget = 0 → escalate.")

        elif category == FaultCategory.DYNAMIC_SKILL_DEFECT:
            # Tier 2: patch skill, then retry
            skill_name = (
                ctx.get("skill_name")
                or self._extract_skill_name_from_evidence(fault_report)
                or fault_report.root_cause_span_id
                or "unknown_skill"
            )
            if confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM):
                actions.append(RemediationAction(
                    action_id=f"ra_{uuid4().hex[:8]}",
                    action_type=RemediationActionType.PATCH_DYNAMIC_SKILL,
                    target_id=str(skill_name),
                    tier=2,
                    requires_approval=False,
                    parameters={
                        "goal_id": fault_report.goal_id,
                        "error_message": fault_report.error_message[:256],
                        "failing_input": fault_report.failing_input[:256],
                    },
                    rationale=f"Dynamic skill '{skill_name}' raised an exception — attempt patch revision.",
                    estimated_duration_seconds=10.0,
                ))
                # Follow with a retry
                actions.append(RemediationAction(
                    action_id=f"ra_{uuid4().hex[:8]}",
                    action_type=RemediationActionType.RETRY_PHASE,
                    target_id=fault_report.phase_id,
                    tier=1,
                    requires_approval=False,
                    parameters={"goal_id": fault_report.goal_id, "retry_delay_seconds": "1.0"},
                    rationale="Retry after skill patch.",
                    estimated_duration_seconds=5.0,
                ))
                rationale_parts.append(f"DYNAMIC_SKILL_DEFECT → tier-2 patch '{skill_name}' then retry.")
            else:
                # Low confidence — safer to just retry once before escalating
                if budget.max_retries > 0:
                    actions.append(RemediationAction(
                        action_id=f"ra_{uuid4().hex[:8]}",
                        action_type=RemediationActionType.RETRY_PHASE,
                        target_id=fault_report.phase_id,
                        tier=1,
                        requires_approval=False,
                        parameters={"goal_id": fault_report.goal_id},
                        rationale="Low confidence skill defect — safe retry first.",
                        estimated_duration_seconds=5.0,
                    ))
                    rationale_parts.append("DYNAMIC_SKILL_DEFECT (low confidence) → tier-1 retry.")
                else:
                    actions.append(self._make_escalation_action(fault_report, rationale="Low confidence skill defect, no retries left."))
                    rationale_parts.append("Escalate low-confidence skill defect.")

        elif category == FaultCategory.ARTIFACT_SCHEMA_MISMATCH:
            # Tier 2: inject adapter transformer
            artifact_id = ctx.get("artifact_id") or (
                fault_report.affected_artifact_ids[0] if fault_report.affected_artifact_ids else ""
            )
            if confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM):
                actions.append(RemediationAction(
                    action_id=f"ra_{uuid4().hex[:8]}",
                    action_type=RemediationActionType.INJECT_DATAFLOW_TRANSFORMER,
                    target_id=fault_report.phase_id,
                    tier=2,
                    requires_approval=False,
                    parameters={
                        "source_artifact_id": str(artifact_id),
                        "goal_id": fault_report.goal_id,
                        "error_message": fault_report.error_message[:256],
                    },
                    rationale="Artifact schema mismatch — inject transformer to bridge formats.",
                    estimated_duration_seconds=8.0,
                ))
                actions.append(RemediationAction(
                    action_id=f"ra_{uuid4().hex[:8]}",
                    action_type=RemediationActionType.RETRY_PHASE,
                    target_id=fault_report.phase_id,
                    tier=1,
                    requires_approval=False,
                    parameters={"goal_id": fault_report.goal_id},
                    rationale="Retry after schema transformer injection.",
                    estimated_duration_seconds=5.0,
                ))
                rationale_parts.append("ARTIFACT_SCHEMA_MISMATCH → inject transformer then retry.")
            else:
                actions.append(self._make_escalation_action(fault_report, rationale="Schema mismatch, insufficient confidence for auto-fix."))
                rationale_parts.append("Schema mismatch (low confidence) → escalate.")

        elif category == FaultCategory.SEMANTIC_CRITERIA_UNMET:
            # Tier 2: replan the goal; if confidence is low, escalate
            if confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM):
                actions.append(RemediationAction(
                    action_id=f"ra_{uuid4().hex[:8]}",
                    action_type=RemediationActionType.REPLAN_GOAL,
                    target_id=fault_report.goal_id,
                    tier=2,
                    requires_approval=False,
                    parameters={
                        "phase_id": fault_report.phase_id,
                        "milestone_score": str(ctx.get("milestone_score", "")),
                    },
                    rationale="Milestone gate not met — attempt replanning the goal execution strategy.",
                    estimated_duration_seconds=10.0,
                ))
                rationale_parts.append("SEMANTIC_CRITERIA_UNMET → tier-2 replan.")
            else:
                actions.append(self._make_escalation_action(fault_report, rationale="Semantic gate failure, low confidence."))
                rationale_parts.append("Semantic failure (low confidence) → escalate.")

        elif category == FaultCategory.RESOURCE_STARVATION:
            # Tier 3: selective rollback to release resources, then escalate
            actions.append(RemediationAction(
                action_id=f"ra_{uuid4().hex[:8]}",
                action_type=RemediationActionType.SELECTIVE_SAGA_ROLLBACK,
                target_id=fault_report.phase_id,
                tier=3,
                requires_approval=False,
                parameters={
                    "goal_id": fault_report.goal_id,
                    "reason": "Resource starvation — release acquired locks and artifacts.",
                },
                rationale="Resource starvation — selective rollback to release contested resources.",
                estimated_duration_seconds=5.0,
            ))
            actions.append(self._make_escalation_action(
                fault_report,
                rationale="After resource release, operator should reschedule the campaign.",
            ))
            rationale_parts.append("RESOURCE_STARVATION → tier-3 rollback then escalate.")

        elif category == FaultCategory.SAGA_COMPENSATION_FAILURE:
            # Saga itself failed — only operator can sort this out
            actions.append(self._make_escalation_action(
                fault_report,
                rationale="Compensation mechanism failed — requires operator inspection.",
            ))
            is_operator_escalation = True
            rationale_parts.append("SAGA_COMPENSATION_FAILURE → operator escalation.")

        else:  # UNKNOWN or any unhandled category
            actions.append(self._make_escalation_action(
                fault_report,
                rationale="Unknown fault category — conservative escalation.",
            ))
            is_operator_escalation = True
            rationale_parts.append(f"Category={category.value} → conservative escalation.")

        # Cap actions to budget
        actions = actions[:min(len(actions), MAX_PLAN_ACTIONS, budget.max_actions_per_attempt)]

        rationale = " ".join(rationale_parts) or f"Remediation plan for {category.value}."
        logger.info(
            "RemediationPlanner: plan=%s category=%s actions=%d escalation=%s",
            plan_id,
            category.value,
            len(actions),
            is_operator_escalation,
        )

        return RemediationPlan(
            plan_id=plan_id,
            fault_report_id=fault_report.report_id,
            campaign_id=fault_report.campaign_id,
            phase_id=fault_report.phase_id,
            actions=tuple(actions),
            budget=budget,
            created_at=time.time(),
            rationale=rationale,
            is_operator_escalation=is_operator_escalation,
        )

    @staticmethod
    def _make_escalation_action(
        fault_report: FaultDiagnosticReport,
        rationale: str = "",
    ) -> RemediationAction:
        return RemediationAction(
            action_id=f"ra_{uuid4().hex[:8]}",
            action_type=RemediationActionType.REQUEST_OPERATOR_CLARIFICATION,
            target_id=fault_report.phase_id,
            tier=4,
            requires_approval=True,
            parameters={
                "campaign_id": fault_report.campaign_id,
                "phase_id": fault_report.phase_id,
                "goal_id": fault_report.goal_id,
                "fault_category": fault_report.fault_category.value,
                "error_message": fault_report.error_message[:256],
            },
            rationale=rationale or "Escalate to operator.",
            estimated_duration_seconds=0.0,
        )

    @staticmethod
    def _extract_skill_name_from_evidence(report: FaultDiagnosticReport) -> str | None:
        """Try to extract the failing skill name from evidence items or span ID."""
        for ev in report.evidence_items:
            for prefix in ("dynamic_registry.register.", "dynamic_skill.", "execute_skill."):
                if prefix in ev.lower():
                    parts = ev.lower().split(prefix, 1)
                    if len(parts) > 1:
                        return parts[1].split()[0].strip("':\"")
        return None
