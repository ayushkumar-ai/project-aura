"""M33 — Structured Planning Engine Types.

Defines schemas for goal decomposition, plan steps, step dependencies, execution states,
and plan audits.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class PlanStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class PlanStatus(str, Enum):
    CREATED = "created"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class PlanStepNode:
    step_id: str
    title: str
    description: str
    tool_name: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    max_retries: int = 2
    timeout_seconds: float = 30.0
    status: PlanStepStatus = PlanStepStatus.PENDING
    retry_count: int = 0
    result: Any = None
    error: str = ""
    duration_seconds: float = 0.0
    provenance: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanStepNode:
        d = dict(data)
        if isinstance(d.get("status"), str):
            d["status"] = PlanStepStatus(d["status"])
        valid_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in d.items() if k in valid_fields}
        return cls(**filtered)


@dataclass
class StructuredPlan:
    plan_id: str
    goal: str
    steps: list[PlanStepNode] = field(default_factory=list)
    status: PlanStatus = PlanStatus.CREATED
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def get_step(self, step_id: str) -> PlanStepNode | None:
        for s in self.steps:
            if s.step_id == step_id:
                return s
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "steps": [s.to_dict() for s in self.steps],
            "status": self.status.value,
            "created_at": self.created_at,
            "metadata": self.metadata,
            "provenance": self.provenance,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StructuredPlan:
        status_val = data.get("status", PlanStatus.CREATED.value)
        status_enum = PlanStatus(status_val) if isinstance(status_val, str) else PlanStatus.CREATED
        steps = [PlanStepNode.from_dict(s) for s in data.get("steps", []) if isinstance(s, dict)]
        return cls(
            plan_id=data.get("plan_id", f"plan_{int(time.time())}"),
            goal=data.get("goal", ""),
            steps=steps,
            status=status_enum,
            created_at=data.get("created_at", time.time()),
            metadata=data.get("metadata", {}),
            provenance=data.get("provenance", {}),
        )


@dataclass
class PlanExecutionAudit:
    plan_id: str
    goal: str
    total_steps: int
    steps_completed: int
    steps_failed: int
    is_success: bool
    total_duration_seconds: float = 0.0
    step_results: dict[str, Any] = field(default_factory=dict)
    execution_trace: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
