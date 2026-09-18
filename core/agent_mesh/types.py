"""M59 — Unified Autonomous Agent Runtime & Enterprise Intelligence Mesh Types.

Defines domain models for agent runs, lifecycle states, execution phases, specialized roles,
action types, budgets, verification outcomes, failure categories, events, and audits.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from core.cognitive_memory.types import scrub_sensitive_content
from core.platform.types import CapabilityRiskLevel


class AgentRunStatus(str, Enum):
    """Authoritative lifecycle status for an AgentRun."""
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_EXTERNAL = "waiting_external"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


# Valid state transitions matrix (Invariant M59-F03)
VALID_RUN_TRANSITIONS: dict[AgentRunStatus, set[AgentRunStatus]] = {
    AgentRunStatus.PENDING: {
        AgentRunStatus.RUNNING,
        AgentRunStatus.PAUSED,
        AgentRunStatus.CANCELLED,
        AgentRunStatus.FAILED,
    },
    AgentRunStatus.RUNNING: {
        AgentRunStatus.WAITING_APPROVAL,
        AgentRunStatus.WAITING_EXTERNAL,
        AgentRunStatus.PAUSED,
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.TIMED_OUT,
        AgentRunStatus.CANCELLED,
        AgentRunStatus.UNKNOWN,
    },
    AgentRunStatus.WAITING_APPROVAL: {
        AgentRunStatus.RUNNING,
        AgentRunStatus.CANCELLED,
        AgentRunStatus.FAILED,
        AgentRunStatus.TIMED_OUT,
    },
    AgentRunStatus.WAITING_EXTERNAL: {
        AgentRunStatus.RUNNING,
        AgentRunStatus.CANCELLED,
        AgentRunStatus.FAILED,
        AgentRunStatus.TIMED_OUT,
        AgentRunStatus.UNKNOWN,
    },
    AgentRunStatus.PAUSED: {
        AgentRunStatus.RUNNING,
        AgentRunStatus.CANCELLED,
    },
    # Terminal states cannot transition anywhere (Invariant M59-F04)
    AgentRunStatus.COMPLETED: set(),
    AgentRunStatus.FAILED: set(),
    AgentRunStatus.TIMED_OUT: set(),
    AgentRunStatus.CANCELLED: set(),
    AgentRunStatus.UNKNOWN: set(),
}


class AgentPhase(str, Enum):
    """Granular execution phases of the 16-step Unified Agent Loop."""
    RECEIVE = "receive"
    AUTHENTICATE = "authenticate"
    AUTHORIZE = "authorize"
    INTENT = "intent"
    CONTEXT = "context"
    PLAN = "plan"
    VALIDATE_PLAN = "validate_plan"
    POLICY_GATE = "policy_gate"
    APPROVAL_GATE = "approval_gate"
    DISPATCH = "dispatch"
    EXECUTE = "execute"
    OBSERVE = "observe"
    VERIFY = "verify"
    REFLECT = "reflect"
    LEARN = "learn"
    FINALIZE = "finalize"


class AgentRole(str, Enum):
    """Specialized intelligence mesh agent roles."""
    GENERALIST = "generalist"
    RESEARCH = "research"
    PLANNING = "planning"
    MEMORY = "memory"
    ANALYSIS = "analysis"
    EXECUTION = "execution"
    VERIFICATION = "verification"
    DEVICE = "device"
    MULTIMODAL = "multimodal"


class ActionType(str, Enum):
    """Class of action to be executed."""
    TOOL = "tool"
    DEVICE = "device"
    TASK = "task"
    DELEGATION = "delegation"
    MULTIMODAL = "multimodal"
    MEMORY = "memory"
    RESPONSE = "response"


class VerificationStatus(str, Enum):
    """Outcome verification status."""
    UNVERIFIED = "unverified"
    VERIFIED_SUCCESS = "verified_success"
    VERIFIED_FAILURE = "verified_failure"
    INCONCLUSIVE = "inconclusive"


class FailureCategory(str, Enum):
    """Deterministic failure classification."""
    AUTHENTICATION_FAILURE = "authentication_failure"
    AUTHORIZATION_FAILURE = "authorization_failure"
    POLICY_DENIED = "policy_denied"
    APPROVAL_DENIED = "approval_denied"
    APPROVAL_EXPIRED = "approval_expired"
    TENANT_ISOLATION_FAILURE = "tenant_isolation_failure"
    VALIDATION_FAILURE = "validation_failure"
    PROVIDER_FAILURE = "provider_failure"
    TOOL_FAILURE = "tool_failure"
    DEVICE_FAILURE = "device_failure"
    MULTIMODAL_FAILURE = "multimodal_failure"
    MEMORY_FAILURE = "memory_failure"
    TIMEOUT = "timeout"
    QUOTA_EXCEEDED = "quota_exceeded"
    CIRCUIT_OPEN = "circuit_open"
    CONCURRENCY_CONFLICT = "concurrency_conflict"
    UNKNOWN_EXTERNAL_OUTCOME = "unknown_external_outcome"
    VERIFICATION_FAILURE = "verification_failure"
    INTERNAL_ERROR = "internal_error"


@dataclass
class AgentRunBudget:
    """Enforced resource and execution envelope for an AgentRun."""
    max_iterations: int = 25
    max_tool_calls: int = 50
    max_model_calls: int = 30
    max_retries: int = 3
    max_child_depth: int = 3
    max_child_runs: int = 5
    timeout_seconds: float = 300.0
    max_tokens: int = 100_000

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> AgentRunBudget:
        if not data or not isinstance(data, dict):
            return cls()
        return cls(
            max_iterations=int(data.get("max_iterations", 25)),
            max_tool_calls=int(data.get("max_tool_calls", 50)),
            max_model_calls=int(data.get("max_model_calls", 30)),
            max_retries=int(data.get("max_retries", 3)),
            max_child_depth=int(data.get("max_child_depth", 3)),
            max_child_runs=int(data.get("max_child_runs", 5)),
            timeout_seconds=float(data.get("timeout_seconds", 300.0)),
            max_tokens=int(data.get("max_tokens", 100_000)),
        )


@dataclass
class PlanStep:
    """A discrete, typed step inside an execution plan."""
    step_number: int
    action_type: ActionType
    action_name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    risk_level: CapabilityRiskLevel = CapabilityRiskLevel.LOW
    requires_approval: bool = False
    description: str = ""

    def __post_init__(self):
        if isinstance(self.action_type, str):
            self.action_type = ActionType(self.action_type)
        if isinstance(self.risk_level, str):
            self.risk_level = CapabilityRiskLevel(self.risk_level)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_number": self.step_number,
            "action_type": self.action_type.value,
            "action_name": self.action_name,
            "parameters": dict(self.parameters),
            "risk_level": self.risk_level.value,
            "requires_approval": self.requires_approval,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanStep:
        return cls(
            step_number=int(data["step_number"]),
            action_type=ActionType(data["action_type"]),
            action_name=data["action_name"],
            parameters=dict(data.get("parameters", {})),
            risk_level=CapabilityRiskLevel(data.get("risk_level", "low")),
            requires_approval=bool(data.get("requires_approval", False)),
            description=data.get("description", ""),
        )


@dataclass
class ExecutionPlan:
    """Authoritative structured plan generated by StructuredPlanner."""
    plan_id: str = field(default_factory=lambda: f"plan_{uuid4().hex[:16]}")
    goal: str = ""
    steps: list[PlanStep] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExecutionPlan:
        return cls(
            plan_id=data.get("plan_id", f"plan_{uuid4().hex[:16]}"),
            goal=data.get("goal", ""),
            steps=[PlanStep.from_dict(s) for s in data.get("steps", [])],
            created_at=float(data.get("created_at", time.time())),
        )


@dataclass
class AgentRunStep:
    """Record of a single executed step within an AgentRun."""
    step_id: str = field(default_factory=lambda: f"step_{uuid4().hex[:16]}")
    run_id: str = ""
    tenant_id: str = "default"
    step_number: int = 1
    phase: AgentPhase = AgentPhase.EXECUTE
    plan_action: str = ""
    action_type: ActionType = ActionType.TOOL
    parameters: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    status: str = "requested"
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    approval_token: str | None = None
    duration_ms: float = 0.0
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None

    def __post_init__(self):
        if isinstance(self.phase, str):
            self.phase = AgentPhase(self.phase)
        if isinstance(self.action_type, str):
            self.action_type = ActionType(self.action_type)
        if isinstance(self.verification_status, str):
            self.verification_status = VerificationStatus(self.verification_status)
        if isinstance(self.parameters, dict):
            cleaned = {}
            for k, v in self.parameters.items():
                cleaned[k] = scrub_sensitive_content(v) if isinstance(v, str) else v
            self.parameters = cleaned

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "run_id": self.run_id,
            "tenant_id": self.tenant_id,
            "step_number": self.step_number,
            "phase": self.phase.value,
            "plan_action": self.plan_action,
            "action_type": self.action_type.value,
            "parameters": dict(self.parameters),
            "result": dict(self.result),
            "status": self.status,
            "verification_status": self.verification_status.value,
            "approval_token": self.approval_token,
            "duration_ms": self.duration_ms,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentRunStep:
        return cls(
            step_id=data.get("step_id", f"step_{uuid4().hex[:16]}"),
            run_id=data.get("run_id", ""),
            tenant_id=data.get("tenant_id", "default"),
            step_number=int(data.get("step_number", 1)),
            phase=AgentPhase(data.get("phase", "execute")),
            plan_action=data.get("plan_action", ""),
            action_type=ActionType(data.get("action_type", "tool")),
            parameters=dict(data.get("parameters", {})),
            result=dict(data.get("result", {})),
            status=data.get("status", "requested"),
            verification_status=VerificationStatus(data.get("verification_status", "unverified")),
            approval_token=data.get("approval_token"),
            duration_ms=float(data.get("duration_ms", 0.0)),
            created_at=float(data.get("created_at", time.time())),
            completed_at=float(data["completed_at"]) if data.get("completed_at") is not None else None,
        )


@dataclass
class AgentDelegation:
    """Parent-child delegation record in the Enterprise Intelligence Mesh."""
    delegation_id: str = field(default_factory=lambda: f"del_{uuid4().hex[:16]}")
    parent_run_id: str = ""
    child_run_id: str = ""
    tenant_id: str = "default"
    role: AgentRole = AgentRole.RESEARCH
    capabilities: list[str] = field(default_factory=list)
    budget_allocated: AgentRunBudget = field(default_factory=AgentRunBudget)
    status: str = "active"
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None

    def __post_init__(self):
        if isinstance(self.role, str):
            self.role = AgentRole(self.role)
        if isinstance(self.budget_allocated, dict):
            self.budget_allocated = AgentRunBudget.from_dict(self.budget_allocated)

    def to_dict(self) -> dict[str, Any]:
        return {
            "delegation_id": self.delegation_id,
            "parent_run_id": self.parent_run_id,
            "child_run_id": self.child_run_id,
            "tenant_id": self.tenant_id,
            "role": self.role.value,
            "capabilities": list(self.capabilities),
            "budget_allocated": self.budget_allocated.to_dict(),
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentDelegation:
        return cls(
            delegation_id=data.get("delegation_id", f"del_{uuid4().hex[:16]}"),
            parent_run_id=data.get("parent_run_id", ""),
            child_run_id=data.get("child_run_id", ""),
            tenant_id=data.get("tenant_id", "default"),
            role=AgentRole(data.get("role", "research")),
            capabilities=list(data.get("capabilities", [])),
            budget_allocated=AgentRunBudget.from_dict(data.get("budget_allocated")),
            status=data.get("status", "active"),
            created_at=float(data.get("created_at", time.time())),
            completed_at=float(data["completed_at"]) if data.get("completed_at") is not None else None,
        )


@dataclass
class AgentMeshEvent:
    """Granular observable lifecycle event emitted during an AgentRun."""
    event_id: str = field(default_factory=lambda: f"evt_{uuid4().hex[:16]}")
    run_id: str = ""
    tenant_id: str = "default"
    event_type: str = "lifecycle"
    phase: AgentPhase = AgentPhase.RECEIVE
    data: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def __post_init__(self):
        if isinstance(self.phase, str):
            self.phase = AgentPhase(self.phase)
        if isinstance(self.data, dict):
            self.data = {k: (scrub_sensitive_content(v) if isinstance(v, str) else v) for k, v in self.data.items()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "tenant_id": self.tenant_id,
            "event_type": self.event_type,
            "phase": self.phase.value,
            "data": dict(self.data),
            "created_at": self.created_at,
        }


@dataclass
class AgentMeshAudit:
    """Immutable security and authority audit record."""
    audit_id: str = field(default_factory=lambda: f"aud_{uuid4().hex[:16]}")
    run_id: str = ""
    tenant_id: str = "default"
    action: str = ""
    principal_id: str = ""
    risk_level: CapabilityRiskLevel = CapabilityRiskLevel.LOW
    details: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def __post_init__(self):
        if isinstance(self.risk_level, str):
            self.risk_level = CapabilityRiskLevel(self.risk_level)
        if isinstance(self.details, dict):
            self.details = {k: (scrub_sensitive_content(v) if isinstance(v, str) else v) for k, v in self.details.items()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "run_id": self.run_id,
            "tenant_id": self.tenant_id,
            "action": self.action,
            "principal_id": self.principal_id,
            "risk_level": self.risk_level.value,
            "details": dict(self.details),
            "created_at": self.created_at,
        }


@dataclass
class AgentRun:
    """Authoritative domain entity representing an execution run of an autonomous agent."""
    run_id: str = field(default_factory=lambda: f"run_{uuid4().hex[:16]}")
    tenant_id: str = "default"
    user_id: str = "default"
    parent_run_id: str | None = None
    correlation_id: str = field(default_factory=lambda: f"corr_{uuid4().hex[:16]}")
    causation_id: str | None = None
    intent: str = ""
    status: AgentRunStatus = AgentRunStatus.PENDING
    current_phase: AgentPhase = AgentPhase.RECEIVE
    depth: int = 0
    budget: AgentRunBudget = field(default_factory=AgentRunBudget)
    iteration_count: int = 0
    tool_call_count: int = 0
    provider_call_count: int = 0
    token_usage: dict[str, int] = field(default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    cost_estimate: float = 0.0
    final_outcome: dict[str, Any] = field(default_factory=dict)
    error_detail: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None

    def __post_init__(self):
        if isinstance(self.status, str):
            self.status = AgentRunStatus(self.status)
        if isinstance(self.current_phase, str):
            self.current_phase = AgentPhase(self.current_phase)
        if isinstance(self.budget, dict):
            self.budget = AgentRunBudget.from_dict(self.budget)
        if isinstance(self.intent, str):
            self.intent = scrub_sensitive_content(self.intent)

    def transition_to(self, new_status: AgentRunStatus | str, error_detail: str | None = None) -> None:
        """Enforce strict valid transitions and immutability of terminal states (Invariants M59-F03, M59-F04)."""
        target = AgentRunStatus(new_status) if isinstance(new_status, str) else new_status
        if self.status == target:
            return

        valid_targets = VALID_RUN_TRANSITIONS.get(self.status, set())
        if target not in valid_targets:
            raise ValueError(f"Illegal AgentRun state transition from '{self.status.value}' to '{target.value}'.")

        self.status = target
        if error_detail:
            self.error_detail = error_detail

        now = time.time()
        if target == AgentRunStatus.RUNNING and self.started_at is None:
            self.started_at = now
        elif target in (AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.TIMED_OUT, AgentRunStatus.CANCELLED, AgentRunStatus.UNKNOWN):
            self.completed_at = now

    def is_terminal(self) -> bool:
        return self.status in (AgentRunStatus.COMPLETED, AgentRunStatus.FAILED, AgentRunStatus.TIMED_OUT, AgentRunStatus.CANCELLED, AgentRunStatus.UNKNOWN)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "parent_run_id": self.parent_run_id,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "intent": self.intent,
            "status": self.status.value,
            "current_phase": self.current_phase.value,
            "depth": self.depth,
            "budget": self.budget.to_dict(),
            "iteration_count": self.iteration_count,
            "tool_call_count": self.tool_call_count,
            "provider_call_count": self.provider_call_count,
            "token_usage": dict(self.token_usage),
            "cost_estimate": self.cost_estimate,
            "final_outcome": dict(self.final_outcome),
            "error_detail": self.error_detail,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentRun:
        return cls(
            run_id=data.get("run_id", f"run_{uuid4().hex[:16]}"),
            tenant_id=data.get("tenant_id", "default"),
            user_id=data.get("user_id", "default"),
            parent_run_id=data.get("parent_run_id"),
            correlation_id=data.get("correlation_id", f"corr_{uuid4().hex[:16]}"),
            causation_id=data.get("causation_id"),
            intent=data.get("intent", ""),
            status=AgentRunStatus(data.get("status", "pending")),
            current_phase=AgentPhase(data.get("phase", data.get("current_phase", "receive"))),
            depth=int(data.get("depth", 0)),
            budget=AgentRunBudget.from_dict(data.get("budget")),
            iteration_count=int(data.get("iteration_count", 0)),
            tool_call_count=int(data.get("tool_call_count", 0)),
            provider_call_count=int(data.get("provider_call_count", 0)),
            token_usage=dict(data.get("token_usage", {})),
            cost_estimate=float(data.get("cost_estimate", 0.0)),
            final_outcome=dict(data.get("final_outcome", {})),
            error_detail=data.get("error_detail"),
            created_at=float(data.get("created_at", time.time())),
            started_at=float(data["started_at"]) if data.get("started_at") is not None else None,
            completed_at=float(data["completed_at"]) if data.get("completed_at") is not None else None,
        )
