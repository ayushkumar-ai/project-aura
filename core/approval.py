import hashlib
import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from core.policy import Policy, PolicyDecision
from core.provenance import TaintedValue
from core.skill_registry import SkillRegistry
from core.task_planner import ExecutionPlan, PlanStep

logger = logging.getLogger("aura.approval")


def _canonical_value(val: Any) -> Any:
    """Convert values into deterministic, JSON-serializable primitives."""
    if isinstance(val, TaintedValue):
        return {
            "__tainted__": True,
            "raw_value": _canonical_value(val.raw_value),
            "is_untrusted": val.is_untrusted,
            "source_type": val.source_type,
            "originating_step_id": val.originating_step_id,
            "source_urls": sorted(val.source_urls),
            "metadata": {str(k): _canonical_value(v) for k, v in sorted(val.metadata.items())},
        }
    elif isinstance(val, (str, int, float, bool)) or val is None:
        return val
    elif isinstance(val, (list, tuple, set, frozenset)):
        return [_canonical_value(x) for x in val]
    elif isinstance(val, dict):
        return {str(k): _canonical_value(v) for k, v in sorted(val.items())}
    elif callable(val):
        return f"callable:{getattr(val, '__qualname__', str(val))}"
    else:
        return repr(val)


class ApprovalStatus(str, Enum):
    """Lifecycle status of an approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ApprovalDecisionType(str, Enum):
    """Outcome classification of an approval evaluation."""

    ALLOWED = "allowed"
    REQUIRES_APPROVAL = "requires_approval"
    DENIED = "denied"


@dataclass
class ApprovalRequest:
    """Structured representation of an approval request for a sensitive planned action."""

    approval_id: str
    task_id: str
    plan_id: str
    step_id: str
    skill_name: str
    reason: str
    declared_tools: tuple[str, ...] = field(default_factory=tuple)
    status: ApprovalStatus = ApprovalStatus.PENDING
    plan_fingerprint: str = ""
    created_at: float = field(default_factory=time.time)
    resolved_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.approval_id, str) or not self.approval_id.strip():
            raise ValueError("approval_id must be a non-empty string.")
        self.approval_id = self.approval_id.strip()

        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("task_id must be a non-empty string.")
        self.task_id = self.task_id.strip()

        if not isinstance(self.plan_id, str) or not self.plan_id.strip():
            raise ValueError("plan_id must be a non-empty string.")
        self.plan_id = self.plan_id.strip()

        if not isinstance(self.step_id, str) or not self.step_id.strip():
            raise ValueError("step_id must be a non-empty string.")
        self.step_id = self.step_id.strip()

        if not isinstance(self.skill_name, str) or not self.skill_name.strip():
            raise ValueError("skill_name must be a non-empty string.")
        self.skill_name = self.skill_name.strip()

        if isinstance(self.status, str):
            self.status = ApprovalStatus(self.status)
        elif not isinstance(self.status, ApprovalStatus):
            raise TypeError("status must be an instance of ApprovalStatus.")

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")

    def is_pending(self) -> bool:
        """Check if request is awaiting human approval."""
        return self.status == ApprovalStatus.PENDING

    def is_approved(self) -> bool:
        """Check if request was approved."""
        return self.status == ApprovalStatus.APPROVED

    def is_rejected(self) -> bool:
        """Check if request was rejected."""
        return self.status == ApprovalStatus.REJECTED


@dataclass(frozen=True)
class ApprovalDecision:
    """Decision returned by the ApprovalGateway evaluation."""

    decision: ApprovalDecisionType
    reason: str = ""
    approval_request: ApprovalRequest | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_allowed(self) -> bool:
        """Check if action is allowed to proceed automatically or with approval."""
        return self.decision == ApprovalDecisionType.ALLOWED

    @property
    def requires_approval(self) -> bool:
        """Check if action requires explicit approval before execution."""
        return self.decision == ApprovalDecisionType.REQUIRES_APPROVAL

    @property
    def is_denied(self) -> bool:
        """Check if action is explicitly denied."""
        return self.decision == ApprovalDecisionType.DENIED


class ApprovalGateway:
    """Safety and approval boundary determining whether planned actions require explicit approval."""

    def __init__(
        self,
        policy: Policy | None = None,
        skill_registry: SkillRegistry | None = None,
        sensitive_tools: set[str] | Sequence[str] | None = None,
        sensitive_skills: set[str] | Sequence[str] | None = None,
        auto_approve: bool = False,
    ):
        if policy is not None and not isinstance(policy, Policy):
            raise TypeError("policy must be an instance of Policy or None.")
        if skill_registry is not None and not isinstance(skill_registry, SkillRegistry):
            raise TypeError("skill_registry must be an instance of SkillRegistry or None.")

        self.policy = policy
        self.skill_registry = skill_registry
        self.sensitive_tools = (
            {t.strip().lower() for t in sensitive_tools if isinstance(t, str) and t.strip()}
            if sensitive_tools is not None
            else set()
        )
        self.sensitive_skills = (
            {s.strip().lower() for s in sensitive_skills if isinstance(s, str) and s.strip()}
            if sensitive_skills is not None
            else set()
        )
        self.auto_approve = bool(auto_approve)

        # In-memory approval storage: approval_id -> ApprovalRequest
        self._requests: dict[str, ApprovalRequest] = {}
        # Keyed index: (task_id, plan_id, step_id) -> approval_id
        self._step_index: dict[tuple[str, str, str], str] = {}

    @staticmethod
    def compute_plan_fingerprint(plan: ExecutionPlan) -> str:
        """Compute a deterministic SHA-256 fingerprint covering all approval-relevant plan content."""
        if not isinstance(plan, ExecutionPlan):
            raise TypeError("plan must be an instance of ExecutionPlan.")

        steps_data = []
        for step in plan.steps:
            step_dict = {
                "step_id": step.step_id,
                "skill_name": step.skill_name,
                "input_data": _canonical_value(step.input_data),
                "dependencies": sorted(step.dependencies),
                "task_requirements": (
                    {
                        "caps": sorted(list(step.task_requirements.required_capabilities)),
                        "pref_model": step.task_requirements.preferred_model,
                        "pref_provider": step.task_requirements.preferred_provider,
                    }
                    if step.task_requirements is not None
                    else None
                ),
                "metadata": _canonical_value(step.metadata),
            }
            steps_data.append(step_dict)

        plan_data = {
            "plan_id": plan.plan_id,
            "plan_metadata": _canonical_value(plan.metadata),
            "steps": steps_data,
        }

        canonical_str = json.dumps(plan_data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()

    def _get_declared_tools(self, step: PlanStep) -> tuple[str, ...]:
        """Resolve declared tools from the step and skill registry."""
        tools: set[str] = set()
        if self.skill_registry is not None and self.skill_registry.has(step.skill_name):
            skill = self.skill_registry.get(step.skill_name)
            tools.update(skill.tools)

        if "tools" in step.metadata and isinstance(step.metadata["tools"], (list, tuple, set)):
            for t in step.metadata["tools"]:
                if isinstance(t, str) and t.strip():
                    tools.add(t.strip().lower())

        return tuple(sorted(tools))

    def evaluate_step(
        self,
        step: PlanStep,
        plan: ExecutionPlan,
        task_id: str,
    ) -> ApprovalDecision:
        """Evaluate whether a planned step can execute automatically, requires approval, or is denied."""
        if not isinstance(step, PlanStep):
            raise TypeError("step must be an instance of PlanStep.")
        if not isinstance(plan, ExecutionPlan):
            raise TypeError("plan must be an instance of ExecutionPlan.")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string.")

        task_id_norm = task_id.strip()
        declared_tools = self._get_declared_tools(step)

        # 1. Policy Boundary Check: If any declared tool is denied by Policy, action is DENIED
        if self.policy is not None:
            for tool_name in declared_tools:
                decision = self.policy.authorize_tool(tool_name)
                if decision != PolicyDecision.ALLOW:
                    logger.warning(
                        "Tool '%s' required by step '%s' is denied by Policy",
                        tool_name,
                        step.step_id,
                    )
                    return ApprovalDecision(
                        decision=ApprovalDecisionType.DENIED,
                        reason=f"Tool '{tool_name}' required by step '{step.step_id}' is denied by Policy.",
                    )

        # 2. Classify Sensitivity / Risk (Sensitivity check MUST take precedence over auto-approve)
        reasons: list[str] = []
        is_sensitive = False

        if step.skill_name.strip().lower() in self.sensitive_skills:
            is_sensitive = True
            reasons.append(f"Skill '{step.skill_name}' is classified as sensitive.")

        for tool_name in declared_tools:
            if tool_name in self.sensitive_tools:
                is_sensitive = True
                reasons.append(f"Tool '{tool_name}' requires explicit approval.")

        if step.metadata.get("requires_approval", False):
            is_sensitive = True
            reasons.append(f"Step '{step.step_id}' metadata explicitly requires approval.")

        # 3. If action is sensitive, it REQUIRES_APPROVAL unless a valid trusted approval exists
        if is_sensitive:
            fingerprint = self.compute_plan_fingerprint(plan)
            index_key = (task_id_norm, plan.plan_id, step.step_id)

            if index_key in self._step_index:
                existing_id = self._step_index[index_key]
                existing_req = self._requests[existing_id]

                # Replay / Stale Protection: verify fingerprint matches
                if existing_req.plan_fingerprint != fingerprint:
                    logger.warning(
                        "Plan fingerprint changed for step '%s' in task '%s'. Stale approval invalid.",
                        step.step_id,
                        task_id_norm,
                    )
                    # Create fresh approval request for modified plan
                    new_req = ApprovalRequest(
                        approval_id=str(uuid4()),
                        task_id=task_id_norm,
                        plan_id=plan.plan_id,
                        step_id=step.step_id,
                        skill_name=step.skill_name,
                        reason="; ".join(reasons) or "Plan modified, re-approval required.",
                        declared_tools=declared_tools,
                        status=ApprovalStatus.PENDING,
                        plan_fingerprint=fingerprint,
                    )
                    self._requests[new_req.approval_id] = new_req
                    self._step_index[index_key] = new_req.approval_id
                    return ApprovalDecision(
                        decision=ApprovalDecisionType.REQUIRES_APPROVAL,
                        reason=new_req.reason,
                        approval_request=new_req,
                    )

                if existing_req.status == ApprovalStatus.APPROVED:
                    return ApprovalDecision(
                        decision=ApprovalDecisionType.ALLOWED,
                        reason=f"Action approved under approval ID '{existing_req.approval_id}'.",
                        approval_request=existing_req,
                    )
                elif existing_req.status == ApprovalStatus.REJECTED:
                    rejection_reason = existing_req.metadata.get("rejection_reason", "Explicitly rejected.")
                    return ApprovalDecision(
                        decision=ApprovalDecisionType.DENIED,
                        reason=f"Approval rejected for step '{step.step_id}': {rejection_reason}",
                        approval_request=existing_req,
                    )
                elif existing_req.status == ApprovalStatus.PENDING:
                    return ApprovalDecision(
                        decision=ApprovalDecisionType.REQUIRES_APPROVAL,
                        reason=existing_req.reason,
                        approval_request=existing_req,
                    )
                else:
                    return ApprovalDecision(
                        decision=ApprovalDecisionType.DENIED,
                        reason=f"Approval request is {existing_req.status.value}.",
                        approval_request=existing_req,
                    )

            # No existing approval request: create new pending request
            reason_text = "; ".join(reasons) if reasons else "Action requires explicit approval."
            req = ApprovalRequest(
                approval_id=str(uuid4()),
                task_id=task_id_norm,
                plan_id=plan.plan_id,
                step_id=step.step_id,
                skill_name=step.skill_name,
                reason=reason_text,
                declared_tools=declared_tools,
                status=ApprovalStatus.PENDING,
                plan_fingerprint=fingerprint,
            )
            self._requests[req.approval_id] = req
            self._step_index[index_key] = req.approval_id

            return ApprovalDecision(
                decision=ApprovalDecisionType.REQUIRES_APPROVAL,
                reason=reason_text,
                approval_request=req,
            )

        # 4. If NOT sensitive, safe action is auto-approved / allowed
        return ApprovalDecision(
            decision=ApprovalDecisionType.ALLOWED,
            reason="Action is safe and auto-approved.",
        )

    def evaluate_plan(self, plan: ExecutionPlan, task_id: str) -> ApprovalDecision:
        """Evaluate all steps in an ExecutionPlan."""
        if not isinstance(plan, ExecutionPlan):
            raise TypeError("plan must be an instance of ExecutionPlan.")

        for step in plan.steps:
            dec = self.evaluate_step(step, plan, task_id)
            if dec.is_denied:
                return dec
            if dec.requires_approval:
                return dec

        return ApprovalDecision(
            decision=ApprovalDecisionType.ALLOWED,
            reason="All steps in plan are auto-approved.",
        )

    def approve(self, approval_id: str) -> ApprovalRequest:
        """Explicitly approve a pending approval request."""
        if not isinstance(approval_id, str) or not approval_id.strip():
            raise ValueError("approval_id must be a non-empty string.")

        norm_id = approval_id.strip()
        if norm_id not in self._requests:
            raise KeyError(f"Approval request not found: {norm_id}")

        req = self._requests[norm_id]
        if req.status == ApprovalStatus.APPROVED:
            return req

        if req.status != ApprovalStatus.PENDING:
            raise ValueError(f"Cannot approve request in '{req.status.value}' state.")

        req.status = ApprovalStatus.APPROVED
        req.resolved_at = time.time()
        logger.info("Approval request '%s' approved for task '%s'", norm_id, req.task_id)
        return req

    def reject(self, approval_id: str, reason: str = "") -> ApprovalRequest:
        """Explicitly reject a pending approval request."""
        if not isinstance(approval_id, str) or not approval_id.strip():
            raise ValueError("approval_id must be a non-empty string.")

        norm_id = approval_id.strip()
        if norm_id not in self._requests:
            raise KeyError(f"Approval request not found: {norm_id}")

        req = self._requests[norm_id]
        if req.status == ApprovalStatus.REJECTED:
            return req

        if req.status != ApprovalStatus.PENDING:
            raise ValueError(f"Cannot reject request in '{req.status.value}' state.")

        req.status = ApprovalStatus.REJECTED
        req.resolved_at = time.time()
        if reason:
            req.metadata["rejection_reason"] = reason
        logger.info("Approval request '%s' rejected for task '%s'", norm_id, req.task_id)
        return req

    def cancel(self, approval_id: str) -> ApprovalRequest:
        """Cancel a pending approval request."""
        if not isinstance(approval_id, str) or not approval_id.strip():
            raise ValueError("approval_id must be a non-empty string.")

        norm_id = approval_id.strip()
        if norm_id not in self._requests:
            raise KeyError(f"Approval request not found: {norm_id}")

        req = self._requests[norm_id]
        if req.status != ApprovalStatus.PENDING:
            raise ValueError(f"Cannot cancel request in '{req.status.value}' state.")

        req.status = ApprovalStatus.CANCELLED
        req.resolved_at = time.time()
        return req

    def get_request(self, approval_id: str) -> ApprovalRequest:
        """Retrieve an approval request by ID."""
        if not isinstance(approval_id, str) or not approval_id.strip():
            raise KeyError(f"Invalid approval_id: {approval_id}")

        norm_id = approval_id.strip()
        if norm_id not in self._requests:
            raise KeyError(f"Approval request not found: {norm_id}")

        return self._requests[norm_id]

    def list_requests(
        self,
        task_id: str | None = None,
        status: ApprovalStatus | None = None,
    ) -> list[ApprovalRequest]:
        """List approval requests matching optional task_id and status filters."""
        results = list(self._requests.values())
        if task_id is not None:
            norm_task = task_id.strip()
            results = [r for r in results if r.task_id == norm_task]
        if status is not None:
            results = [r for r in results if r.status == status]
        return results
