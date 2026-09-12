"""Durable Campaign Contracts & Schemas (M25).

Defines strongly typed immutable data contracts for multi-phase mission campaigns,
cross-goal artifact dataflows, contracts, milestone gates, and distributed sagas.
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence
from uuid import uuid4

from core.artifact_types import ArtifactType, MAX_ARTIFACT_SIZE_BYTES
from core.provenance import TaintedValue, is_tainted, unwrap_tainted, wrap_tainted

# Resource & Scale bounds
MAX_CAMPAIGN_PHASES = 32
MAX_CAMPAIGN_MILESTONES = 64
MAX_DATAFLOW_BINDINGS = 128
MAX_SAGA_STEPS = 256
MAX_COMPENSATING_ACTIONS_PER_STEP = 16
MAX_CAMPAIGN_METADATA_ENTRIES = 64
MAX_CAMPAIGN_TIMEOUT_SECONDS = 604800.0  # 7 days

FORBIDDEN_CAMPAIGN_METADATA_KEYS = frozenset({
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
})


def _sanitize_campaign_value(val: Any) -> Any:
    """Sanitize metadata values preserving TaintedValue envelopes."""
    if isinstance(val, TaintedValue):
        return {
            "__tainted__": True,
            "raw_value": _sanitize_campaign_value(val.raw_value),
            "is_untrusted": val.is_untrusted,
            "source_type": val.source_type,
            "originating_step_id": val.originating_step_id,
            "source_urls": list(val.source_urls),
        }
    elif isinstance(val, (str, int, float, bool)) or val is None:
        if isinstance(val, str) and len(val) > 8192:
            return val[:8192] + "...[truncated]"
        return val
    elif isinstance(val, (list, tuple, set, frozenset)):
        return [_sanitize_campaign_value(x) for x in list(val)[:50]]
    elif isinstance(val, dict):
        cleaned: dict[str, Any] = {}
        for k, v in list(val.items())[:MAX_CAMPAIGN_METADATA_ENTRIES]:
            k_str = str(k).strip()
            if k_str.lower() in FORBIDDEN_CAMPAIGN_METADATA_KEYS or callable(v):
                continue
            cleaned[k_str] = _sanitize_campaign_value(v)
        return cleaned
    elif callable(val):
        return "<callable>"
    else:
        return repr(val)[:8192]


def _sanitize_campaign_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Sanitize campaign metadata dictionary."""
    if not isinstance(meta, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for k, v in list(meta.items())[:MAX_CAMPAIGN_METADATA_ENTRIES]:
        k_str = str(k).strip()
        if not k_str or k_str.lower() in FORBIDDEN_CAMPAIGN_METADATA_KEYS or callable(v):
            continue
        cleaned[k_str] = _sanitize_campaign_value(v)
    return cleaned


class CampaignStatus(str, Enum):
    """Lifecycle status of a multi-goal mission campaign."""

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    COMPENSATING = "compensating"
    RECOVERING = "recovering"  # M27: actively in self-healing cycle
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    def is_terminal(self) -> bool:
        return self in (
            CampaignStatus.COMPLETED,
            CampaignStatus.FAILED,
            CampaignStatus.CANCELLED,
        )


class PhaseStatus(str, Enum):
    """Execution status of a single campaign phase."""

    PENDING = "pending"
    RUNNING = "running"
    VERIFYING = "verifying"
    HEALING = "healing"    # M27: self-healing in progress for this phase
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    COMPENSATED = "compensated"

    def is_terminal(self) -> bool:
        return self in (
            PhaseStatus.COMPLETED,
            PhaseStatus.FAILED,
            PhaseStatus.SKIPPED,
            PhaseStatus.COMPENSATED,
        )


class DataflowChannelType(str, Enum):
    """Routing semantic for cross-goal artifact delivery."""

    DIRECT = "direct"
    BUFFERED = "buffered"
    BROADCAST = "broadcast"
    ACCUMULATOR = "accumulator"


class CompensatingActionType(str, Enum):
    """Classification of compensating action upon failure."""

    TOMBSTONE_ARTIFACT = "tombstone_artifact"
    RELEASE_LOCKS = "release_locks"
    EXECUTE_GOAL = "execute_goal"
    CUSTOM_CALLBACK = "custom_callback"
    CHECKPOINT_ROLLBACK = "checkpoint_rollback"


class SagaStepStatus(str, Enum):
    """Status of an individual saga forward or backward step."""

    FORWARD_EXECUTED = "forward_executed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"
    COMPENSATION_FAILED = "compensation_failed"
    COMPENSATION_SKIPPED = "compensation_skipped"


@dataclass(frozen=True)
class ArtifactContract:
    """Precondition contract expected by a downstream consumer goal for an artifact."""

    contract_id: str
    expected_artifact_type: ArtifactType
    required_mime_types: tuple[str, ...] = field(default_factory=tuple)
    min_quality_score: float = 0.0
    allow_tainted: bool = False
    schema_definition: dict[str, Any] | None = None
    max_size_bytes: int = MAX_ARTIFACT_SIZE_BYTES
    allowed_versions: tuple[int, ...] = field(default_factory=tuple)
    producer_role_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        cid = str(self.contract_id).strip()
        if not cid:
            raise ValueError("contract_id must be a non-empty string.")
        object.__setattr__(self, "contract_id", cid)

        if isinstance(self.expected_artifact_type, str):
            object.__setattr__(self, "expected_artifact_type", ArtifactType(self.expected_artifact_type))
        elif not isinstance(self.expected_artifact_type, ArtifactType):
            raise TypeError("expected_artifact_type must be an ArtifactType instance.")

        mimes = []
        if isinstance(self.required_mime_types, (list, tuple, set)):
            for m in self.required_mime_types:
                m_str = str(m).strip().lower()
                if m_str and m_str not in mimes:
                    mimes.append(m_str)
        object.__setattr__(self, "required_mime_types", tuple(mimes))

        score = float(self.min_quality_score)
        if not (0.0 <= score <= 1.0):
            raise ValueError(f"min_quality_score ({score}) must be between 0.0 and 1.0.")
        object.__setattr__(self, "min_quality_score", score)

        object.__setattr__(self, "allow_tainted", bool(self.allow_tainted))

        if self.schema_definition is not None:
            if not isinstance(self.schema_definition, dict):
                raise TypeError("schema_definition must be a dictionary or None.")
            object.__setattr__(self, "schema_definition", dict(self.schema_definition))

        sz = int(self.max_size_bytes)
        if sz <= 0 or sz > MAX_ARTIFACT_SIZE_BYTES:
            raise ValueError(f"max_size_bytes ({sz}) must be between 1 and {MAX_ARTIFACT_SIZE_BYTES}.")
        object.__setattr__(self, "max_size_bytes", sz)

        vers = []
        if isinstance(self.allowed_versions, (list, tuple, set)):
            for v in self.allowed_versions:
                v_int = int(v)
                if v_int >= 1 and v_int not in vers:
                    vers.append(v_int)
        object.__setattr__(self, "allowed_versions", tuple(vers))

        if self.producer_role_id is not None:
            object.__setattr__(self, "producer_role_id", str(self.producer_role_id).strip() or None)

        object.__setattr__(self, "metadata", _sanitize_campaign_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "expected_artifact_type": self.expected_artifact_type.value,
            "required_mime_types": list(self.required_mime_types),
            "min_quality_score": self.min_quality_score,
            "allow_tainted": self.allow_tainted,
            "schema_definition": copy.deepcopy(self.schema_definition) if self.schema_definition else None,
            "max_size_bytes": self.max_size_bytes,
            "allowed_versions": list(self.allowed_versions),
            "producer_role_id": self.producer_role_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactContract:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        return cls(
            contract_id=str(data.get("contract_id", "")),
            expected_artifact_type=ArtifactType(data.get("expected_artifact_type", ArtifactType.DOCUMENT.value)),
            required_mime_types=tuple(data.get("required_mime_types", ())),
            min_quality_score=float(data.get("min_quality_score", 0.0)),
            allow_tainted=bool(data.get("allow_tainted", False)),
            schema_definition=data.get("schema_definition"),
            max_size_bytes=int(data.get("max_size_bytes", MAX_ARTIFACT_SIZE_BYTES)),
            allowed_versions=tuple(data.get("allowed_versions", ())),
            producer_role_id=data.get("producer_role_id"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class DataflowBinding:
    """Explicit cross-goal artifact routing channel binding."""

    binding_id: str
    source_goal_id: str
    target_goal_id: str
    source_artifact_name: str
    target_input_key: str
    channel_type: DataflowChannelType = DataflowChannelType.DIRECT
    contract: ArtifactContract | None = None
    transform_rule: str | None = None
    is_optional: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        bid = str(self.binding_id).strip()
        if not bid:
            raise ValueError("binding_id must be a non-empty string.")
        object.__setattr__(self, "binding_id", bid)

        sg = str(self.source_goal_id).strip()
        if not sg:
            raise ValueError("source_goal_id must be a non-empty string.")
        object.__setattr__(self, "source_goal_id", sg)

        tg = str(self.target_goal_id).strip()
        if not tg:
            raise ValueError("target_goal_id must be a non-empty string.")
        object.__setattr__(self, "target_goal_id", tg)

        sn = str(self.source_artifact_name).strip()
        if not sn:
            raise ValueError("source_artifact_name must be a non-empty string.")
        object.__setattr__(self, "source_artifact_name", sn)

        tk = str(self.target_input_key).strip()
        if not tk:
            raise ValueError("target_input_key must be a non-empty string.")
        object.__setattr__(self, "target_input_key", tk)

        if isinstance(self.channel_type, str):
            object.__setattr__(self, "channel_type", DataflowChannelType(self.channel_type))
        elif not isinstance(self.channel_type, DataflowChannelType):
            raise TypeError("channel_type must be a DataflowChannelType instance.")

        if self.contract is not None and not isinstance(self.contract, ArtifactContract):
            raise TypeError("contract must be an ArtifactContract instance or None.")

        if self.transform_rule is not None:
            object.__setattr__(self, "transform_rule", str(self.transform_rule).strip() or None)

        object.__setattr__(self, "is_optional", bool(self.is_optional))
        object.__setattr__(self, "metadata", _sanitize_campaign_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "source_goal_id": self.source_goal_id,
            "target_goal_id": self.target_goal_id,
            "source_artifact_name": self.source_artifact_name,
            "target_input_key": self.target_input_key,
            "channel_type": self.channel_type.value,
            "contract": self.contract.to_dict() if self.contract else None,
            "transform_rule": self.transform_rule,
            "is_optional": self.is_optional,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DataflowBinding:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        contract_data = data.get("contract")
        contract = ArtifactContract.from_dict(contract_data) if contract_data else None
        return cls(
            binding_id=str(data.get("binding_id", "")),
            source_goal_id=str(data.get("source_goal_id", "")),
            target_goal_id=str(data.get("target_goal_id", "")),
            source_artifact_name=str(data.get("source_artifact_name", "")),
            target_input_key=str(data.get("target_input_key", "")),
            channel_type=DataflowChannelType(data.get("channel_type", DataflowChannelType.DIRECT.value)),
            contract=contract,
            transform_rule=data.get("transform_rule"),
            is_optional=bool(data.get("is_optional", False)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class CompensatingAction:
    """Action registered to safely revert, clean up, or tombstone state on failure."""

    action_id: str
    action_type: CompensatingActionType
    target_id: str
    parameters: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    requires_approval: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        aid = str(self.action_id).strip()
        if not aid:
            raise ValueError("action_id must be a non-empty string.")
        object.__setattr__(self, "action_id", aid)

        if isinstance(self.action_type, str):
            object.__setattr__(self, "action_type", CompensatingActionType(self.action_type))
        elif not isinstance(self.action_type, CompensatingActionType):
            raise TypeError("action_type must be a CompensatingActionType instance.")

        tid = str(self.target_id).strip()
        if not tid:
            raise ValueError("target_id must be a non-empty string.")
        object.__setattr__(self, "target_id", tid)

        object.__setattr__(self, "description", str(self.description).strip())
        object.__setattr__(self, "requires_approval", bool(self.requires_approval))
        object.__setattr__(self, "parameters", _sanitize_campaign_metadata(self.parameters))
        object.__setattr__(self, "metadata", _sanitize_campaign_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type.value,
            "target_id": self.target_id,
            "parameters": dict(self.parameters),
            "description": self.description,
            "requires_approval": self.requires_approval,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompensatingAction:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        return cls(
            action_id=str(data.get("action_id", "")),
            action_type=CompensatingActionType(data.get("action_type", CompensatingActionType.TOMBSTONE_ARTIFACT.value)),
            target_id=str(data.get("target_id", "")),
            parameters=dict(data.get("parameters", {})),
            description=str(data.get("description", "")),
            requires_approval=bool(data.get("requires_approval", False)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class SagaStep:
    """Recorded forward step and associated compensating action stack in a saga."""

    step_id: str
    goal_id: str
    phase_id: str
    status: SagaStepStatus
    forward_execution_result: dict[str, Any] = field(default_factory=dict)
    compensating_actions: tuple[CompensatingAction, ...] = field(default_factory=tuple)
    compensation_status: str = "none"
    executed_at: float = field(default_factory=time.time)
    compensated_at: float | None = None
    error: str | None = None

    def __post_init__(self):
        sid = str(self.step_id).strip()
        if not sid:
            raise ValueError("step_id must be a non-empty string.")
        object.__setattr__(self, "step_id", sid)

        gid = str(self.goal_id).strip()
        if not gid:
            raise ValueError("goal_id must be a non-empty string.")
        object.__setattr__(self, "goal_id", gid)

        pid = str(self.phase_id).strip()
        if not pid:
            raise ValueError("phase_id must be a non-empty string.")
        object.__setattr__(self, "phase_id", pid)

        if isinstance(self.status, str):
            object.__setattr__(self, "status", SagaStepStatus(self.status))
        elif not isinstance(self.status, SagaStepStatus):
            raise TypeError("status must be a SagaStepStatus instance.")

        actions = []
        if isinstance(self.compensating_actions, (list, tuple, set)):
            for act in self.compensating_actions:
                if isinstance(act, CompensatingAction):
                    actions.append(act)
        object.__setattr__(self, "compensating_actions", tuple(actions[:MAX_COMPENSATING_ACTIONS_PER_STEP]))
        object.__setattr__(self, "forward_execution_result", _sanitize_campaign_metadata(self.forward_execution_result))
        object.__setattr__(self, "executed_at", float(self.executed_at))
        if self.compensated_at is not None:
            object.__setattr__(self, "compensated_at", float(self.compensated_at))
        if self.error is not None:
            object.__setattr__(self, "error", str(self.error).strip())

    def with_status(
        self,
        status: SagaStepStatus,
        compensated_at: float | None = None,
        error: str | None = None,
    ) -> SagaStep:
        return SagaStep(
            step_id=self.step_id,
            goal_id=self.goal_id,
            phase_id=self.phase_id,
            status=status,
            forward_execution_result=dict(self.forward_execution_result),
            compensating_actions=self.compensating_actions,
            compensation_status=status.value,
            executed_at=self.executed_at,
            compensated_at=compensated_at if compensated_at is not None else self.compensated_at,
            error=error if error is not None else self.error,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "goal_id": self.goal_id,
            "phase_id": self.phase_id,
            "status": self.status.value,
            "forward_execution_result": dict(self.forward_execution_result),
            "compensating_actions": [a.to_dict() for a in self.compensating_actions],
            "compensation_status": self.compensation_status,
            "executed_at": self.executed_at,
            "compensated_at": self.compensated_at,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SagaStep:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        actions = [CompensatingAction.from_dict(a) for a in data.get("compensating_actions", [])]
        return cls(
            step_id=str(data.get("step_id", "")),
            goal_id=str(data.get("goal_id", "")),
            phase_id=str(data.get("phase_id", "")),
            status=SagaStepStatus(data.get("status", SagaStepStatus.FORWARD_EXECUTED.value)),
            forward_execution_result=dict(data.get("forward_execution_result", {})),
            compensating_actions=tuple(actions),
            compensation_status=str(data.get("compensation_status", "none")),
            executed_at=float(data.get("executed_at", time.time())),
            compensated_at=data.get("compensated_at"),
            error=data.get("error"),
        )


@dataclass(frozen=True)
class CampaignMilestone:
    """Verification milestone evaluating goal convergence and artifact completeness."""

    milestone_id: str
    title: str
    criteria: tuple[str, ...]
    required_artifacts: tuple[str, ...] = field(default_factory=tuple)
    min_evaluation_score: float = 0.8
    is_verified: bool = False
    evaluation_score: float = 0.0
    verified_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        mid = str(self.milestone_id).strip()
        if not mid:
            raise ValueError("milestone_id must be a non-empty string.")
        object.__setattr__(self, "milestone_id", mid)

        object.__setattr__(self, "title", str(self.title).strip() or "Untitled Milestone")

        crits = []
        if isinstance(self.criteria, (list, tuple, set)):
            for c in self.criteria:
                c_str = str(c).strip()
                if c_str and c_str not in crits:
                    crits.append(c_str)
        object.__setattr__(self, "criteria", tuple(crits[:20]))

        arts = []
        if isinstance(self.required_artifacts, (list, tuple, set)):
            for a in self.required_artifacts:
                a_str = str(a).strip()
                if a_str and a_str not in arts:
                    arts.append(a_str)
        object.__setattr__(self, "required_artifacts", tuple(arts[:20]))

        score = float(self.min_evaluation_score)
        if not (0.0 <= score <= 1.0):
            raise ValueError(f"min_evaluation_score ({score}) must be between 0.0 and 1.0.")
        object.__setattr__(self, "min_evaluation_score", score)

        object.__setattr__(self, "is_verified", bool(self.is_verified))
        object.__setattr__(self, "evaluation_score", max(0.0, min(1.0, float(self.evaluation_score))))
        if self.verified_at is not None:
            object.__setattr__(self, "verified_at", float(self.verified_at))
        object.__setattr__(self, "metadata", _sanitize_campaign_metadata(self.metadata))

    def with_verification(self, is_verified: bool, score: float, verified_at: float | None = None) -> CampaignMilestone:
        return CampaignMilestone(
            milestone_id=self.milestone_id,
            title=self.title,
            criteria=self.criteria,
            required_artifacts=self.required_artifacts,
            min_evaluation_score=self.min_evaluation_score,
            is_verified=is_verified,
            evaluation_score=score,
            verified_at=verified_at if verified_at is not None else time.time(),
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "milestone_id": self.milestone_id,
            "title": self.title,
            "criteria": list(self.criteria),
            "required_artifacts": list(self.required_artifacts),
            "min_evaluation_score": self.min_evaluation_score,
            "is_verified": self.is_verified,
            "evaluation_score": self.evaluation_score,
            "verified_at": self.verified_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CampaignMilestone:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        return cls(
            milestone_id=str(data.get("milestone_id", "")),
            title=str(data.get("title", "")),
            criteria=tuple(data.get("criteria", ())),
            required_artifacts=tuple(data.get("required_artifacts", ())),
            min_evaluation_score=float(data.get("min_evaluation_score", 0.8)),
            is_verified=bool(data.get("is_verified", False)),
            evaluation_score=float(data.get("evaluation_score", 0.0)),
            verified_at=data.get("verified_at"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class CampaignPhase:
    """Cohesive execution phase within a campaign DAG."""

    phase_id: str
    name: str
    goal_ids: tuple[str, ...]
    depends_on_phase_ids: tuple[str, ...] = field(default_factory=tuple)
    milestones: tuple[CampaignMilestone, ...] = field(default_factory=tuple)
    assigned_team_id: str | None = None
    assigned_role_id: str | None = None
    max_concurrency: int = 4
    timeout_seconds: float = 3600.0
    status: PhaseStatus = PhaseStatus.PENDING
    is_contingency: bool = False
    contingency_for_phase_id: str | None = None
    started_at: float | None = None
    completed_at: float | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        pid = str(self.phase_id).strip()
        if not pid:
            raise ValueError("phase_id must be a non-empty string.")
        object.__setattr__(self, "phase_id", pid)

        object.__setattr__(self, "name", str(self.name).strip() or "Untitled Phase")

        gids = []
        if isinstance(self.goal_ids, (list, tuple, set)):
            for g in self.goal_ids:
                g_str = str(g).strip()
                if g_str and g_str not in gids:
                    gids.append(g_str)
        if not gids:
            raise ValueError("phase must contain at least one goal_id.")
        object.__setattr__(self, "goal_ids", tuple(gids[:32]))

        dep_phases = []
        if isinstance(self.depends_on_phase_ids, (list, tuple, set)):
            for d in self.depends_on_phase_ids:
                d_str = str(d).strip()
                if d_str and d_str not in dep_phases:
                    dep_phases.append(d_str)
        object.__setattr__(self, "depends_on_phase_ids", tuple(dep_phases[:MAX_CAMPAIGN_PHASES]))

        stones = []
        if isinstance(self.milestones, (list, tuple, set)):
            for m in self.milestones:
                if isinstance(m, CampaignMilestone):
                    stones.append(m)
        object.__setattr__(self, "milestones", tuple(stones[:MAX_CAMPAIGN_MILESTONES]))

        if self.assigned_team_id is not None:
            object.__setattr__(self, "assigned_team_id", str(self.assigned_team_id).strip() or None)
        if self.assigned_role_id is not None:
            object.__setattr__(self, "assigned_role_id", str(self.assigned_role_id).strip() or None)

        conc = int(self.max_concurrency)
        if conc <= 0 or conc > 32:
            raise ValueError(f"max_concurrency ({conc}) must be between 1 and 32.")
        object.__setattr__(self, "max_concurrency", conc)

        to = float(self.timeout_seconds)
        if to <= 0.0 or to > MAX_CAMPAIGN_TIMEOUT_SECONDS:
            raise ValueError(f"timeout_seconds ({to}) must be between 1.0 and {MAX_CAMPAIGN_TIMEOUT_SECONDS}.")
        object.__setattr__(self, "timeout_seconds", to)

        if isinstance(self.status, str):
            object.__setattr__(self, "status", PhaseStatus(self.status))
        elif not isinstance(self.status, PhaseStatus):
            raise TypeError("status must be a PhaseStatus instance.")

        object.__setattr__(self, "is_contingency", bool(self.is_contingency))
        if self.contingency_for_phase_id is not None:
            object.__setattr__(self, "contingency_for_phase_id", str(self.contingency_for_phase_id).strip() or None)

        if self.started_at is not None:
            object.__setattr__(self, "started_at", float(self.started_at))
        if self.completed_at is not None:
            object.__setattr__(self, "completed_at", float(self.completed_at))
        if self.error is not None:
            object.__setattr__(self, "error", str(self.error).strip())
        object.__setattr__(self, "metadata", _sanitize_campaign_metadata(self.metadata))

    def with_status(
        self,
        status: PhaseStatus,
        started_at: float | None = None,
        completed_at: float | None = None,
        error: str | None = None,
        milestones: tuple[CampaignMilestone, ...] | None = None,
    ) -> CampaignPhase:
        return CampaignPhase(
            phase_id=self.phase_id,
            name=self.name,
            goal_ids=self.goal_ids,
            depends_on_phase_ids=self.depends_on_phase_ids,
            milestones=milestones if milestones is not None else self.milestones,
            assigned_team_id=self.assigned_team_id,
            assigned_role_id=self.assigned_role_id,
            max_concurrency=self.max_concurrency,
            timeout_seconds=self.timeout_seconds,
            status=status,
            is_contingency=self.is_contingency,
            contingency_for_phase_id=self.contingency_for_phase_id,
            started_at=started_at if started_at is not None else self.started_at,
            completed_at=completed_at if completed_at is not None else self.completed_at,
            error=error if error is not None else self.error,
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase_id": self.phase_id,
            "name": self.name,
            "goal_ids": list(self.goal_ids),
            "depends_on_phase_ids": list(self.depends_on_phase_ids),
            "milestones": [m.to_dict() for m in self.milestones],
            "assigned_team_id": self.assigned_team_id,
            "assigned_role_id": self.assigned_role_id,
            "max_concurrency": self.max_concurrency,
            "timeout_seconds": self.timeout_seconds,
            "status": self.status.value,
            "is_contingency": self.is_contingency,
            "contingency_for_phase_id": self.contingency_for_phase_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CampaignPhase:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        stones = [CampaignMilestone.from_dict(m) for m in data.get("milestones", [])]
        return cls(
            phase_id=str(data.get("phase_id", "")),
            name=str(data.get("name", "")),
            goal_ids=tuple(data.get("goal_ids", ())),
            depends_on_phase_ids=tuple(data.get("depends_on_phase_ids", ())),
            milestones=tuple(stones),
            assigned_team_id=data.get("assigned_team_id"),
            assigned_role_id=data.get("assigned_role_id"),
            max_concurrency=int(data.get("max_concurrency", 4)),
            timeout_seconds=float(data.get("timeout_seconds", 3600.0)),
            status=PhaseStatus(data.get("status", PhaseStatus.PENDING.value)),
            is_contingency=bool(data.get("is_contingency", False)),
            contingency_for_phase_id=data.get("contingency_for_phase_id"),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            error=data.get("error"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class CampaignDefinition:
    """Master declarative definition for a multi-phase mission campaign."""

    campaign_id: str
    title: str
    description: str
    phases: tuple[CampaignPhase, ...]
    dataflows: tuple[DataflowBinding, ...] = field(default_factory=tuple)
    session_id: str | None = None
    budget_tokens: int | None = None
    budget_cost_usd: float | None = None
    max_total_time_seconds: float = 86400.0
    auto_compensate_on_failure: bool = True
    allow_contingency_branches: bool = True
    # M27 self-healing fields — default False preserves all M25 behavior
    auto_heal_on_failure: bool = False
    max_healing_attempts_per_phase: int = 2
    healing_budget_seconds: float = 60.0
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        cid = str(self.campaign_id).strip()
        if not cid:
            raise ValueError("campaign_id must be a non-empty string.")
        object.__setattr__(self, "campaign_id", cid)

        object.__setattr__(self, "title", str(self.title).strip() or "Untitled Campaign")
        object.__setattr__(self, "description", str(self.description).strip())

        phs = []
        if isinstance(self.phases, (list, tuple, set)):
            for p in self.phases:
                if isinstance(p, CampaignPhase):
                    phs.append(p)
        if not phs:
            raise ValueError("campaign must contain at least one phase.")
        if len(phs) > MAX_CAMPAIGN_PHASES:
            raise ValueError(f"phases count ({len(phs)}) exceeds limit ({MAX_CAMPAIGN_PHASES}).")
        object.__setattr__(self, "phases", tuple(phs))

        dfs = []
        if isinstance(self.dataflows, (list, tuple, set)):
            for df in self.dataflows:
                if isinstance(df, DataflowBinding):
                    dfs.append(df)
        if len(dfs) > MAX_DATAFLOW_BINDINGS:
            raise ValueError(f"dataflows count ({len(dfs)}) exceeds limit ({MAX_DATAFLOW_BINDINGS}).")
        object.__setattr__(self, "dataflows", tuple(dfs))

        if self.session_id is not None:
            object.__setattr__(self, "session_id", str(self.session_id).strip() or None)

        if self.budget_tokens is not None:
            bt = int(self.budget_tokens)
            if bt <= 0:
                raise ValueError("budget_tokens must be positive.")
            object.__setattr__(self, "budget_tokens", bt)

        if self.budget_cost_usd is not None:
            bc = float(self.budget_cost_usd)
            if bc <= 0.0:
                raise ValueError("budget_cost_usd must be positive.")
            object.__setattr__(self, "budget_cost_usd", bc)

        tt = float(self.max_total_time_seconds)
        if tt <= 0.0 or tt > MAX_CAMPAIGN_TIMEOUT_SECONDS:
            raise ValueError(f"max_total_time_seconds ({tt}) must be between 1.0 and {MAX_CAMPAIGN_TIMEOUT_SECONDS}.")
        object.__setattr__(self, "max_total_time_seconds", tt)

        object.__setattr__(self, "auto_compensate_on_failure", bool(self.auto_compensate_on_failure))
        object.__setattr__(self, "allow_contingency_branches", bool(self.allow_contingency_branches))

        # M27 healing field validation
        object.__setattr__(self, "auto_heal_on_failure", bool(self.auto_heal_on_failure))
        mha = int(self.max_healing_attempts_per_phase)
        if mha < 1:
            raise ValueError("max_healing_attempts_per_phase must be at least 1.")
        object.__setattr__(self, "max_healing_attempts_per_phase", mha)
        hbs = float(self.healing_budget_seconds)
        if hbs <= 0.0:
            raise ValueError("healing_budget_seconds must be positive.")
        object.__setattr__(self, "healing_budget_seconds", hbs)

        object.__setattr__(self, "created_at", float(self.created_at))
        object.__setattr__(self, "metadata", _sanitize_campaign_metadata(self.metadata))

    def get_phase(self, phase_id: str) -> CampaignPhase | None:
        """Find a phase by ID."""
        for p in self.phases:
            if p.phase_id == phase_id:
                return p
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "title": self.title,
            "description": self.description,
            "phases": [p.to_dict() for p in self.phases],
            "dataflows": [df.to_dict() for df in self.dataflows],
            "session_id": self.session_id,
            "budget_tokens": self.budget_tokens,
            "budget_cost_usd": self.budget_cost_usd,
            "max_total_time_seconds": self.max_total_time_seconds,
            "auto_compensate_on_failure": self.auto_compensate_on_failure,
            "allow_contingency_branches": self.allow_contingency_branches,
            "auto_heal_on_failure": self.auto_heal_on_failure,
            "max_healing_attempts_per_phase": self.max_healing_attempts_per_phase,
            "healing_budget_seconds": self.healing_budget_seconds,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CampaignDefinition:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        phs = [CampaignPhase.from_dict(p) for p in data.get("phases", [])]
        dfs = [DataflowBinding.from_dict(df) for df in data.get("dataflows", [])]
        return cls(
            campaign_id=str(data.get("campaign_id", "")),
            title=str(data.get("title", "")),
            description=str(data.get("description", "")),
            phases=tuple(phs),
            dataflows=tuple(dfs),
            session_id=data.get("session_id"),
            budget_tokens=data.get("budget_tokens"),
            budget_cost_usd=data.get("budget_cost_usd"),
            max_total_time_seconds=float(data.get("max_total_time_seconds", 86400.0)),
            auto_compensate_on_failure=bool(data.get("auto_compensate_on_failure", True)),
            allow_contingency_branches=bool(data.get("allow_contingency_branches", True)),
            auto_heal_on_failure=bool(data.get("auto_heal_on_failure", False)),
            max_healing_attempts_per_phase=int(data.get("max_healing_attempts_per_phase", 2)),
            healing_budget_seconds=float(data.get("healing_budget_seconds", 60.0)),
            created_at=float(data.get("created_at", time.time())),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class CampaignExecutionResult:
    """Summary result of a mission campaign execution run."""

    campaign_id: str
    status: CampaignStatus
    completed_phases: tuple[str, ...] = field(default_factory=tuple)
    failed_phases: tuple[str, ...] = field(default_factory=tuple)
    compensated_phases: tuple[str, ...] = field(default_factory=tuple)
    produced_artifact_ids: tuple[str, ...] = field(default_factory=tuple)
    milestone_scores: dict[str, float] = field(default_factory=dict)
    total_duration_seconds: float = 0.0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        cid = str(self.campaign_id).strip()
        if not cid:
            raise ValueError("campaign_id must be a non-empty string.")
        object.__setattr__(self, "campaign_id", cid)

        if isinstance(self.status, str):
            object.__setattr__(self, "status", CampaignStatus(self.status))
        elif not isinstance(self.status, CampaignStatus):
            raise TypeError("status must be a CampaignStatus instance.")

        object.__setattr__(self, "completed_phases", tuple(self.completed_phases))
        object.__setattr__(self, "failed_phases", tuple(self.failed_phases))
        object.__setattr__(self, "compensated_phases", tuple(self.compensated_phases))
        object.__setattr__(self, "produced_artifact_ids", tuple(self.produced_artifact_ids))
        object.__setattr__(self, "milestone_scores", dict(self.milestone_scores))
        object.__setattr__(self, "total_duration_seconds", max(0.0, float(self.total_duration_seconds)))
        if self.error is not None:
            object.__setattr__(self, "error", str(self.error).strip())
        object.__setattr__(self, "metadata", _sanitize_campaign_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "status": self.status.value,
            "completed_phases": list(self.completed_phases),
            "failed_phases": list(self.failed_phases),
            "compensated_phases": list(self.compensated_phases),
            "produced_artifact_ids": list(self.produced_artifact_ids),
            "milestone_scores": dict(self.milestone_scores),
            "total_duration_seconds": self.total_duration_seconds,
            "error": self.error,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CampaignExecutionResult:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        return cls(
            campaign_id=str(data.get("campaign_id", "")),
            status=CampaignStatus(data.get("status", CampaignStatus.FAILED.value)),
            completed_phases=tuple(data.get("completed_phases", ())),
            failed_phases=tuple(data.get("failed_phases", ())),
            compensated_phases=tuple(data.get("compensated_phases", ())),
            produced_artifact_ids=tuple(data.get("produced_artifact_ids", ())),
            milestone_scores=dict(data.get("milestone_scores", {})),
            total_duration_seconds=float(data.get("total_duration_seconds", 0.0)),
            error=data.get("error"),
            metadata=dict(data.get("metadata", {})),
        )
