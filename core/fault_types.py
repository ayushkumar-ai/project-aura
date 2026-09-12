"""Milestone 27: Causal Fault Diagnosis & Self-Healing Type Contracts.

Defines strongly typed, immutable data structures for fault classification,
evidence collection, remediation planning, healing budgets, and recovery results.
All structures are frozen to ensure audit-integrity across the healing pipeline.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

# ---------------------------------------------------------------------------
# Security key blocklist — reused from the established pattern across AURA
# ---------------------------------------------------------------------------
FORBIDDEN_FAULT_METADATA_KEYS = frozenset({
    "approved",
    "approval_status",
    "is_approved",
    "auto_approve",
    "permission",
    "authorized",
    "is_admin",
    "is_authorized",
    "bypass_policy",
    "sudo",
    "override",
    "system_override",
})

MAX_FAULT_METADATA_ENTRIES = 32
MAX_FAULT_TRACEBACK_CHARS = 8192
MAX_FAULT_EVIDENCE_ITEMS = 20


def _sanitize_fault_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Sanitize metadata dictionary, stripping privilege-escalation keys."""
    if not isinstance(meta, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for k, v in list(meta.items())[:MAX_FAULT_METADATA_ENTRIES]:
        k_str = str(k).strip()
        if not k_str or k_str.lower() in FORBIDDEN_FAULT_METADATA_KEYS or callable(v):
            continue
        if isinstance(v, (str, int, float, bool)) or v is None:
            cleaned[k_str] = str(v)[:2048] if isinstance(v, str) else v
        else:
            cleaned[k_str] = repr(v)[:512]
    return cleaned


# ---------------------------------------------------------------------------
# Fault Classification Taxonomy
# ---------------------------------------------------------------------------

class FaultCategory(str, Enum):
    """Classification of a detected execution fault."""

    TRANSIENT_INFRASTRUCTURE = "transient_infrastructure"
    """Temporary failures: timeout, network, resource unavailability — retry-safe."""

    DYNAMIC_SKILL_DEFECT = "dynamic_skill_defect"
    """Exception raised by a synthesized dynamic skill — may be patchable."""

    ARTIFACT_SCHEMA_MISMATCH = "artifact_schema_mismatch"
    """Artifact type, MIME, required-key, or quality contract violation — adapter may help."""

    RESOURCE_STARVATION = "resource_starvation"
    """Budget exhaustion or lock contention — escalate or reschedule."""

    SEMANTIC_CRITERIA_UNMET = "semantic_criteria_unmet"
    """Milestone evaluation gate score below threshold — replanning or goal reconfiguration."""

    POLICY_SECURITY_BLOCK = "policy_security_block"
    """Action denied by Policy or ApprovalGateway — requires operator clarification."""

    SAGA_COMPENSATION_FAILURE = "saga_compensation_failure"
    """Compensating action itself failed during rollback."""

    UNKNOWN = "unknown"
    """Insufficient evidence to classify — prefer controlled escalation."""

    def is_retryable(self) -> bool:
        """True if this category is safe to retry without intervention."""
        return self in (FaultCategory.TRANSIENT_INFRASTRUCTURE,)

    def is_operator_escalation_required(self) -> bool:
        """True if this category should always be escalated to an operator."""
        return self in (
            FaultCategory.POLICY_SECURITY_BLOCK,
            FaultCategory.UNKNOWN,
        )

    def is_auto_remediable(self) -> bool:
        """True if autonomous remediation strategies exist for this category."""
        return self in (
            FaultCategory.TRANSIENT_INFRASTRUCTURE,
            FaultCategory.DYNAMIC_SKILL_DEFECT,
            FaultCategory.ARTIFACT_SCHEMA_MISMATCH,
            FaultCategory.SEMANTIC_CRITERIA_UNMET,
        )


class RemediationActionType(str, Enum):
    """Type of remediation action to apply."""

    RETRY_PHASE = "retry_phase"
    """Re-execute the failed phase goal without any structural change (tier 1)."""

    REPLAN_GOAL = "replan_goal"
    """Invoke GoalAdapter to synthesize an alternative execution plan (tier 2)."""

    PATCH_DYNAMIC_SKILL = "patch_dynamic_skill"
    """Generate a corrected revision of a dynamic skill via SkillSynthesizer (tier 2)."""

    INJECT_DATAFLOW_TRANSFORMER = "inject_dataflow_transformer"
    """Insert a schema adapter into the artifact pipeline between two goals (tier 2)."""

    LOWER_MILESTONE_THRESHOLD = "lower_milestone_threshold"
    """Temporarily reduce milestone evaluation gate threshold and re-evaluate (tier 2)."""

    SELECTIVE_SAGA_ROLLBACK = "selective_saga_rollback"
    """Roll back only the causally affected phase steps, not the whole campaign (tier 3)."""

    REQUEST_OPERATOR_CLARIFICATION = "request_operator_clarification"
    """Escalate to human operator via ClarificationGateway (tier 4)."""

    ABORT_CAMPAIGN = "abort_campaign"
    """Controlled abort — fall back to full saga compensation (terminal)."""


class HealingStatus(str, Enum):
    """Lifecycle state of a self-healing attempt."""

    DIAGNOSING = "diagnosing"
    PLANNING = "planning"
    EXECUTING = "executing"
    RECOVERED = "recovered"
    HEALING_FAILED = "healing_failed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    OPERATOR_ESCALATED = "operator_escalated"

    def is_terminal(self) -> bool:
        return self in (
            HealingStatus.RECOVERED,
            HealingStatus.HEALING_FAILED,
            HealingStatus.BUDGET_EXHAUSTED,
            HealingStatus.OPERATOR_ESCALATED,
        )


class ConfidenceLevel(str, Enum):
    """Evidence confidence of a fault diagnosis."""

    HIGH = "high"       # ≥ 0.75 — act autonomously if safe
    MEDIUM = "medium"   # 0.50–0.74 — act cautiously / use tier 1-2 only
    LOW = "low"         # 0.25–0.49 — prefer escalation
    INSUFFICIENT = "insufficient"  # < 0.25 — do not remediate autonomously

    @classmethod
    def from_score(cls, score: float) -> "ConfidenceLevel":
        if score >= 0.75:
            return cls.HIGH
        elif score >= 0.50:
            return cls.MEDIUM
        elif score >= 0.25:
            return cls.LOW
        return cls.INSUFFICIENT


# ---------------------------------------------------------------------------
# Core Data Structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HealingBudget:
    """Resource constraints for a single self-healing cycle."""

    max_attempts: int = 2
    """Maximum number of full heal-attempt cycles per phase."""

    max_total_seconds: float = 60.0
    """Wall-clock time limit for the entire healing cycle."""

    max_actions_per_attempt: int = 4
    """Maximum remediation actions executed within one attempt."""

    max_retries: int = 1
    """Maximum bare retry attempts (tier 1 only) before escalating."""

    def __post_init__(self) -> None:
        if not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer.")
        if not isinstance(self.max_total_seconds, (int, float)) or self.max_total_seconds <= 0:
            raise ValueError("max_total_seconds must be a positive number.")
        if not isinstance(self.max_actions_per_attempt, int) or self.max_actions_per_attempt < 1:
            raise ValueError("max_actions_per_attempt must be a positive integer.")
        if not isinstance(self.max_retries, int) or self.max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "max_total_seconds": self.max_total_seconds,
            "max_actions_per_attempt": self.max_actions_per_attempt,
            "max_retries": self.max_retries,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HealingBudget":
        return cls(
            max_attempts=int(data.get("max_attempts", 2)),
            max_total_seconds=float(data.get("max_total_seconds", 60.0)),
            max_actions_per_attempt=int(data.get("max_actions_per_attempt", 4)),
            max_retries=int(data.get("max_retries", 1)),
        )


@dataclass(frozen=True)
class FaultDiagnosticReport:
    """Immutable evidence-based fault diagnosis record."""

    report_id: str
    campaign_id: str
    phase_id: str
    goal_id: str
    fault_category: FaultCategory
    confidence_level: ConfidenceLevel
    culpability_score: float              # 0.0 – 1.0; higher = more confident root cause
    error_message: str
    error_traceback: str
    root_cause_span_id: str | None        # span_id of root-cause span if trace available
    affected_artifact_ids: tuple[str, ...]
    failing_input: str                    # truncated input context at fault point
    evidence_items: tuple[str, ...]       # human-readable evidence strings
    timestamp: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "report_id", str(self.report_id).strip() or f"fdr_{uuid4().hex[:8]}")
        object.__setattr__(self, "campaign_id", str(self.campaign_id).strip())
        object.__setattr__(self, "phase_id", str(self.phase_id).strip())
        object.__setattr__(self, "goal_id", str(self.goal_id).strip())

        if isinstance(self.fault_category, str):
            object.__setattr__(self, "fault_category", FaultCategory(self.fault_category))
        if isinstance(self.confidence_level, str):
            object.__setattr__(self, "confidence_level", ConfidenceLevel(self.confidence_level))

        score = max(0.0, min(1.0, float(self.culpability_score)))
        object.__setattr__(self, "culpability_score", score)
        object.__setattr__(self, "error_message", str(self.error_message or "")[:2048])
        object.__setattr__(self, "error_traceback", str(self.error_traceback or "")[:MAX_FAULT_TRACEBACK_CHARS])
        object.__setattr__(self, "root_cause_span_id", str(self.root_cause_span_id).strip() if self.root_cause_span_id else None)

        # Coerce sequences
        aids = tuple(str(a) for a in (self.affected_artifact_ids or ()))
        object.__setattr__(self, "affected_artifact_ids", aids)

        evs = tuple(str(e)[:512] for e in list(self.evidence_items or ())[:MAX_FAULT_EVIDENCE_ITEMS])
        object.__setattr__(self, "evidence_items", evs)

        object.__setattr__(self, "failing_input", str(self.failing_input or "")[:1024])
        object.__setattr__(self, "timestamp", float(self.timestamp))
        object.__setattr__(self, "metadata", _sanitize_fault_metadata(self.metadata or {}))

    def is_autonomously_remediable(self) -> bool:
        """True when confidence is sufficient and the fault category supports auto-repair."""
        return (
            self.fault_category.is_auto_remediable()
            and self.confidence_level not in (ConfidenceLevel.INSUFFICIENT,)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "campaign_id": self.campaign_id,
            "phase_id": self.phase_id,
            "goal_id": self.goal_id,
            "fault_category": self.fault_category.value,
            "confidence_level": self.confidence_level.value,
            "culpability_score": self.culpability_score,
            "error_message": self.error_message,
            "error_traceback": self.error_traceback,
            "root_cause_span_id": self.root_cause_span_id,
            "affected_artifact_ids": list(self.affected_artifact_ids),
            "failing_input": self.failing_input,
            "evidence_items": list(self.evidence_items),
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FaultDiagnosticReport":
        return cls(
            report_id=data.get("report_id", f"fdr_{uuid4().hex[:8]}"),
            campaign_id=data.get("campaign_id", ""),
            phase_id=data.get("phase_id", ""),
            goal_id=data.get("goal_id", ""),
            fault_category=FaultCategory(data.get("fault_category", FaultCategory.UNKNOWN.value)),
            confidence_level=ConfidenceLevel(data.get("confidence_level", ConfidenceLevel.INSUFFICIENT.value)),
            culpability_score=float(data.get("culpability_score", 0.0)),
            error_message=data.get("error_message", ""),
            error_traceback=data.get("error_traceback", ""),
            root_cause_span_id=data.get("root_cause_span_id"),
            affected_artifact_ids=tuple(data.get("affected_artifact_ids", [])),
            failing_input=data.get("failing_input", ""),
            evidence_items=tuple(data.get("evidence_items", [])),
            timestamp=float(data.get("timestamp", time.time())),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True)
class RemediationAction:
    """Single bounded, auditable remediation step."""

    action_id: str
    action_type: RemediationActionType
    target_id: str                        # skill name, artifact ID, phase ID, etc.
    tier: int                             # 1=retry, 2=replan/patch, 3=saga, 4=escalate
    requires_approval: bool
    parameters: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    estimated_duration_seconds: float = 5.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "action_id", str(self.action_id).strip() or f"ra_{uuid4().hex[:8]}")
        if isinstance(self.action_type, str):
            object.__setattr__(self, "action_type", RemediationActionType(self.action_type))
        object.__setattr__(self, "target_id", str(self.target_id).strip())
        object.__setattr__(self, "tier", max(1, min(4, int(self.tier))))
        object.__setattr__(self, "requires_approval", bool(self.requires_approval))
        object.__setattr__(self, "parameters", _sanitize_fault_metadata(self.parameters or {}))
        object.__setattr__(self, "rationale", str(self.rationale or "")[:512])
        object.__setattr__(self, "estimated_duration_seconds", max(0.0, float(self.estimated_duration_seconds)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type.value,
            "target_id": self.target_id,
            "tier": self.tier,
            "requires_approval": self.requires_approval,
            "parameters": dict(self.parameters),
            "rationale": self.rationale,
            "estimated_duration_seconds": self.estimated_duration_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RemediationAction":
        return cls(
            action_id=data.get("action_id", f"ra_{uuid4().hex[:8]}"),
            action_type=RemediationActionType(data.get("action_type", RemediationActionType.ABORT_CAMPAIGN.value)),
            target_id=data.get("target_id", ""),
            tier=int(data.get("tier", 4)),
            requires_approval=bool(data.get("requires_approval", True)),
            parameters=data.get("parameters", {}),
            rationale=data.get("rationale", ""),
            estimated_duration_seconds=float(data.get("estimated_duration_seconds", 5.0)),
        )


@dataclass(frozen=True)
class RemediationPlan:
    """Ordered, bounded sequence of remediation actions for a specific fault."""

    plan_id: str
    fault_report_id: str
    campaign_id: str
    phase_id: str
    actions: tuple[RemediationAction, ...]  # ordered by tier (safest first)
    budget: HealingBudget
    created_at: float
    rationale: str = ""
    is_operator_escalation: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", str(self.plan_id).strip() or f"rp_{uuid4().hex[:8]}")
        object.__setattr__(self, "fault_report_id", str(self.fault_report_id).strip())
        object.__setattr__(self, "campaign_id", str(self.campaign_id).strip())
        object.__setattr__(self, "phase_id", str(self.phase_id).strip())
        if not isinstance(self.actions, tuple):
            object.__setattr__(self, "actions", tuple(self.actions))
        if not isinstance(self.budget, HealingBudget):
            raise TypeError("budget must be a HealingBudget instance.")
        object.__setattr__(self, "created_at", float(self.created_at))
        object.__setattr__(self, "rationale", str(self.rationale or "")[:2048])
        object.__setattr__(self, "is_operator_escalation", bool(self.is_operator_escalation))

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "fault_report_id": self.fault_report_id,
            "campaign_id": self.campaign_id,
            "phase_id": self.phase_id,
            "actions": [a.to_dict() for a in self.actions],
            "budget": self.budget.to_dict(),
            "created_at": self.created_at,
            "rationale": self.rationale,
            "is_operator_escalation": self.is_operator_escalation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RemediationPlan":
        return cls(
            plan_id=data.get("plan_id", f"rp_{uuid4().hex[:8]}"),
            fault_report_id=data.get("fault_report_id", ""),
            campaign_id=data.get("campaign_id", ""),
            phase_id=data.get("phase_id", ""),
            actions=tuple(RemediationAction.from_dict(a) for a in data.get("actions", [])),
            budget=HealingBudget.from_dict(data.get("budget", {})),
            created_at=float(data.get("created_at", time.time())),
            rationale=data.get("rationale", ""),
            is_operator_escalation=bool(data.get("is_operator_escalation", False)),
        )


@dataclass(frozen=True)
class SelfHealingResult:
    """Outcome record of a completed self-healing attempt."""

    result_id: str
    plan_id: str
    campaign_id: str
    phase_id: str
    status: HealingStatus
    fault_category: FaultCategory
    actions_attempted: int
    actions_succeeded: int
    campaign_resumed: bool
    duration_seconds: float
    error: str | None
    attempt_number: int
    timestamp: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "result_id", str(self.result_id).strip() or f"shr_{uuid4().hex[:8]}")
        object.__setattr__(self, "plan_id", str(self.plan_id).strip())
        object.__setattr__(self, "campaign_id", str(self.campaign_id).strip())
        object.__setattr__(self, "phase_id", str(self.phase_id).strip())
        if isinstance(self.status, str):
            object.__setattr__(self, "status", HealingStatus(self.status))
        if isinstance(self.fault_category, str):
            object.__setattr__(self, "fault_category", FaultCategory(self.fault_category))
        object.__setattr__(self, "actions_attempted", max(0, int(self.actions_attempted)))
        object.__setattr__(self, "actions_succeeded", max(0, int(self.actions_succeeded)))
        object.__setattr__(self, "campaign_resumed", bool(self.campaign_resumed))
        object.__setattr__(self, "duration_seconds", max(0.0, float(self.duration_seconds)))
        object.__setattr__(self, "error", str(self.error)[:2048] if self.error else None)
        object.__setattr__(self, "attempt_number", max(1, int(self.attempt_number)))
        object.__setattr__(self, "timestamp", float(self.timestamp))
        object.__setattr__(self, "metadata", _sanitize_fault_metadata(self.metadata or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "plan_id": self.plan_id,
            "campaign_id": self.campaign_id,
            "phase_id": self.phase_id,
            "status": self.status.value,
            "fault_category": self.fault_category.value,
            "actions_attempted": self.actions_attempted,
            "actions_succeeded": self.actions_succeeded,
            "campaign_resumed": self.campaign_resumed,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
            "attempt_number": self.attempt_number,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SelfHealingResult":
        return cls(
            result_id=data.get("result_id", f"shr_{uuid4().hex[:8]}"),
            plan_id=data.get("plan_id", ""),
            campaign_id=data.get("campaign_id", ""),
            phase_id=data.get("phase_id", ""),
            status=HealingStatus(data.get("status", HealingStatus.HEALING_FAILED.value)),
            fault_category=FaultCategory(data.get("fault_category", FaultCategory.UNKNOWN.value)),
            actions_attempted=int(data.get("actions_attempted", 0)),
            actions_succeeded=int(data.get("actions_succeeded", 0)),
            campaign_resumed=bool(data.get("campaign_resumed", False)),
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            error=data.get("error"),
            attempt_number=int(data.get("attempt_number", 1)),
            timestamp=float(data.get("timestamp", time.time())),
            metadata=data.get("metadata", {}),
        )
