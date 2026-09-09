import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.agent_runtime import AgentResult
from core.task_planner import ExecutionPlan


class TaskStatus(str, Enum):
    """Lifecycle status of a task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


class StepStatus(str, Enum):
    """Execution status of an individual step within a task."""

    NOT_STARTED = "not_started"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class StepState:
    """Represents the execution state of an individual step in a task."""

    step_id: str
    status: StepStatus = StepStatus.NOT_STARTED
    agent_result: AgentResult | None = None
    error: str | None = None
    output: Any = None
    started_at: float | None = None
    completed_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.step_id, str) or not self.step_id.strip():
            raise ValueError("step_id must be a non-empty string.")
        self.step_id = self.step_id.strip()

        if isinstance(self.status, str):
            self.status = StepStatus(self.status)
        elif not isinstance(self.status, StepStatus):
            raise TypeError("status must be a StepStatus enum instance.")

        if self.agent_result is not None and not isinstance(self.agent_result, AgentResult):
            raise TypeError("agent_result must be an instance of AgentResult or None.")

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")


@dataclass
class TaskState:
    """Represents the complete runtime and lifecycle state of a task."""

    task_id: str
    plan_id: str
    status: TaskStatus = TaskStatus.PENDING
    step_states: dict[str, StepState] = field(default_factory=dict)
    plan: ExecutionPlan | None = None
    failed_step_id: str | None = None
    error: str | None = None
    final_output: Any = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("task_id must be a non-empty string.")
        self.task_id = self.task_id.strip()

        if not isinstance(self.plan_id, str) or not self.plan_id.strip():
            raise ValueError("plan_id must be a non-empty string.")
        self.plan_id = self.plan_id.strip()

        if isinstance(self.status, str):
            self.status = TaskStatus(self.status)
        elif not isinstance(self.status, TaskStatus):
            raise TypeError("status must be a TaskStatus enum instance.")

        if not isinstance(self.step_states, dict):
            raise TypeError("step_states must be a dict.")

        if self.plan is not None and not isinstance(self.plan, ExecutionPlan):
            raise TypeError("plan must be an instance of ExecutionPlan or None.")

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")

    def get_step_state(self, step_id: str) -> StepState:
        """Retrieve the state of a step by ID."""
        if not isinstance(step_id, str) or not step_id.strip():
            raise KeyError(f"Invalid step ID: {step_id}")
        norm_id = step_id.strip()
        if norm_id not in self.step_states:
            raise KeyError(f"Step '{norm_id}' not found in task state.")
        return self.step_states[norm_id]

    def is_completed(self) -> bool:
        """Check if the task has completed successfully."""
        return self.status == TaskStatus.COMPLETED

    def is_failed(self) -> bool:
        """Check if the task has failed."""
        return self.status == TaskStatus.FAILED
