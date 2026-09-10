"""Goal-Team Binding & Execution Convergence Models (M22).

Defines structured, immutable, and validated contracts representing the binding
between long-running Goals and multi-agent TeamDefinitions.
Converts multi-agent team outcomes into GoalProgress and GoalObservation entities
while strictly preserving data provenance and stripping privilege escalation metadata.
"""

from __future__ import annotations

import copy
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence
from uuid import uuid4

from core.goal import GoalObservation, GoalProgress, _sanitize_metadata
from core.provenance import TaintedValue, is_tainted, wrap_tainted
from core.team_types import TeamDefinition, TeamExecutionResult, TeamTopology

logger = logging.getLogger("aura.goal_team_binding")

FORBIDDEN_BINDING_METADATA_KEYS = frozenset({
    "is_authorized",
    "is_admin",
    "approved",
    "approval_status",
    "is_approved",
    "bypass_policy",
    "sudo",
    "role_override",
    "system_override",
})


def sanitize_binding_metadata(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Recursively strip forbidden privilege/authorization keys from metadata."""
    if meta is None or not isinstance(meta, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for k, v in meta.items():
        k_str = str(k).strip()
        if k_str.lower() in FORBIDDEN_BINDING_METADATA_KEYS:
            continue
        if callable(v):
            continue
        if isinstance(v, dict):
            cleaned[k_str] = sanitize_binding_metadata(v)
        elif isinstance(v, (list, tuple)):
            cleaned[k_str] = [
                sanitize_binding_metadata(item) if isinstance(item, dict) else (str(item) if not callable(item) else "")
                for item in v
            ]
        elif isinstance(v, (str, int, float, bool)) or v is None or isinstance(v, TaintedValue):
            cleaned[k_str] = v
        else:
            cleaned[k_str] = repr(v)
    return cleaned


class GoalTeamBindingStatus(str, Enum):
    """Lifecycle status for a Goal-Team execution binding."""

    CREATED = "created"
    BOUND = "bound"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class GoalTeamBinding:
    """Explicit, validated binding contract between an active Goal and a specialized TeamDefinition."""

    goal_id: str
    team_definition: TeamDefinition
    task_description: str
    binding_id: str = field(default_factory=lambda: str(uuid4()))
    target_criteria: tuple[str, ...] = field(default_factory=tuple)
    status: GoalTeamBindingStatus = GoalTeamBindingStatus.CREATED
    is_untrusted: bool = False
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.binding_id, str) or not self.binding_id.strip():
            raise ValueError("binding_id must be a non-empty string.")
        object.__setattr__(self, "binding_id", self.binding_id.strip())

        if not isinstance(self.goal_id, str) or not self.goal_id.strip():
            raise ValueError("goal_id must be a non-empty string.")
        object.__setattr__(self, "goal_id", self.goal_id.strip().lower())

        if not isinstance(self.team_definition, TeamDefinition):
            raise TypeError("team_definition must be an instance of TeamDefinition.")

        if not isinstance(self.task_description, str) or not self.task_description.strip():
            raise ValueError("task_description must be a non-empty string.")
        object.__setattr__(self, "task_description", self.task_description.strip())

        # Normalize target criteria
        if isinstance(self.target_criteria, (list, tuple, set)):
            criteria_list = [str(c).strip() for c in self.target_criteria if str(c).strip()]
            object.__setattr__(self, "target_criteria", tuple(criteria_list))
        else:
            raise TypeError("target_criteria must be a sequence of strings.")

        if isinstance(self.status, str):
            object.__setattr__(self, "status", GoalTeamBindingStatus(self.status))
        elif not isinstance(self.status, GoalTeamBindingStatus):
            raise TypeError("status must be an instance of GoalTeamBindingStatus.")

        object.__setattr__(self, "metadata", sanitize_binding_metadata(self.metadata))

    @property
    def team_id(self) -> str:
        return self.team_definition.team_id

    @property
    def topology(self) -> TeamTopology:
        return self.team_definition.topology

    @property
    def lead_role_id(self) -> str | None:
        lead = self.team_definition.get_lead_member()
        return lead.role_id if lead else None

    @property
    def required_role_ids(self) -> tuple[str, ...]:
        return tuple(self.team_definition.get_member_roles())

    def with_status(self, new_status: GoalTeamBindingStatus | str) -> GoalTeamBinding:
        """Return a copy of the binding with an updated status."""
        eff_status = GoalTeamBindingStatus(new_status) if isinstance(new_status, str) else new_status
        return GoalTeamBinding(
            binding_id=self.binding_id,
            goal_id=self.goal_id,
            team_definition=self.team_definition,
            task_description=self.task_description,
            target_criteria=self.target_criteria,
            status=eff_status,
            is_untrusted=self.is_untrusted,
            created_at=self.created_at,
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert binding to a JSON-serializable dictionary."""
        return {
            "binding_id": self.binding_id,
            "goal_id": self.goal_id,
            "team_definition": {
                "team_id": self.team_definition.team_id,
                "name": self.team_definition.name,
                "description": self.team_definition.description,
                "topology": self.team_definition.topology.value,
                "consensus_strategy": self.team_definition.consensus_strategy.value,
                "max_iterations": self.team_definition.max_iterations,
                "timeout_seconds": self.team_definition.timeout_seconds,
                "members": [
                    {
                        "role_id": m.role_id,
                        "instance_id": m.instance_id,
                        "weight": m.weight,
                        "is_lead": m.is_lead,
                    }
                    for m in self.team_definition.members
                ],
                "metadata": dict(self.team_definition.metadata),
            },
            "task_description": self.task_description,
            "target_criteria": list(self.target_criteria),
            "status": self.status.value,
            "is_untrusted": self.is_untrusted,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class GoalTeamExecutionResult:
    """Outcome of executing a GoalTeamBinding through TeamOrchestrator."""

    binding_id: str
    goal_id: str
    team_id: str
    success: bool
    final_output: str
    subtask_results: dict[str, str] = field(default_factory=dict)
    consensus_score: float = 1.0
    messages_exchanged: int = 0
    total_latency_seconds: float = 0.0
    is_untrusted: bool = True
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.binding_id, str) or not self.binding_id.strip():
            raise ValueError("binding_id must be a non-empty string.")
        if not isinstance(self.goal_id, str) or not self.goal_id.strip():
            raise ValueError("goal_id must be a non-empty string.")
        if not isinstance(self.team_id, str) or not self.team_id.strip():
            raise ValueError("team_id must be a non-empty string.")
        if not isinstance(self.success, bool):
            raise TypeError("success must be a boolean.")
        if not isinstance(self.final_output, str):
            raise TypeError("final_output must be a string.")

        object.__setattr__(self, "metadata", sanitize_binding_metadata(self.metadata))

    @classmethod
    def from_team_result(
        cls,
        binding: GoalTeamBinding,
        team_result: TeamExecutionResult,
    ) -> GoalTeamExecutionResult:
        """Construct a GoalTeamExecutionResult from a TeamOrchestrator result."""
        return cls(
            binding_id=binding.binding_id,
            goal_id=binding.goal_id,
            team_id=team_result.team_id,
            success=team_result.success,
            final_output=team_result.final_output,
            subtask_results=dict(team_result.subtask_results),
            consensus_score=team_result.consensus_score,
            messages_exchanged=team_result.messages_exchanged,
            total_latency_seconds=team_result.total_latency_seconds,
            is_untrusted=binding.is_untrusted,
            error=team_result.error,
            metadata=dict(team_result.metadata),
        )

    def to_goal_observation(self, source: str = "multi_agent_team") -> GoalObservation:
        """Convert team execution result into a structured GoalObservation."""
        data_payload: dict[str, Any] = {
            "binding_id": self.binding_id,
            "team_id": self.team_id,
            "success": self.success,
            "final_output": self.final_output,
            "consensus_score": self.consensus_score,
            "subtask_results": self.subtask_results,
            "messages_exchanged": self.messages_exchanged,
        }
        return GoalObservation(
            goal_id=self.goal_id,
            source=source,
            data=data_payload,
            is_untrusted=self.is_untrusted,
            timestamp=time.time(),
            metadata={
                "team_id": self.team_id,
                "latency_seconds": self.total_latency_seconds,
            },
        )

    def to_goal_progress(
        self,
        current_progress: GoalProgress,
        satisfied_criteria: Sequence[str] = (),
        remaining_criteria: Sequence[str] = (),
        new_stage: str | None = None,
    ) -> GoalProgress:
        """Derive updated GoalProgress based on team execution outcome."""
        existing_satisfied = set(current_progress.satisfied_criteria)
        for c in satisfied_criteria:
            existing_satisfied.add(str(c).strip())

        eff_remaining = tuple(str(c).strip() for c in remaining_criteria if str(c).strip())
        if not remaining_criteria and current_progress.remaining_criteria:
            eff_remaining = tuple(c for c in current_progress.remaining_criteria if c not in existing_satisfied)

        total_criteria_count = len(existing_satisfied) + len(eff_remaining)
        pct = (
            len(existing_satisfied) / max(1, total_criteria_count)
            if total_criteria_count > 0
            else (1.0 if self.success else current_progress.percentage)
        )

        stage = new_stage if new_stage is not None else ("completed" if self.success and not eff_remaining else current_progress.current_stage)

        return GoalProgress(
            percentage=round(pct, 4),
            current_stage=stage,
            satisfied_criteria=tuple(sorted(existing_satisfied)),
            remaining_criteria=eff_remaining,
            confidence=min(1.0, max(0.0, float(self.consensus_score))),
            summary=self.final_output[:2000],
            last_evaluated_at=time.time(),
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert execution result to a JSON-serializable dictionary."""
        return {
            "binding_id": self.binding_id,
            "goal_id": self.goal_id,
            "team_id": self.team_id,
            "success": self.success,
            "final_output": self.final_output,
            "subtask_results": dict(self.subtask_results),
            "consensus_score": self.consensus_score,
            "messages_exchanged": self.messages_exchanged,
            "total_latency_seconds": self.total_latency_seconds,
            "is_untrusted": self.is_untrusted,
            "error": self.error,
            "metadata": dict(self.metadata),
        }
