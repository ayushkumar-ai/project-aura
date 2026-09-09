import copy
import time
from abc import ABC, abstractmethod
from typing import Any

from core.task_planner import ExecutionPlan
from core.task_state import StepState, StepStatus, TaskState, TaskStatus


class TaskStateStore(ABC):
    """Contract for persisting and retrieving TaskState in AURA."""

    @abstractmethod
    def create(
        self,
        task_id: str,
        plan_id: str,
        plan: ExecutionPlan | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskState:
        """Create and persist a new TaskState."""
        raise NotImplementedError

    @abstractmethod
    def get(self, task_id: str) -> TaskState:
        """Retrieve a persisted TaskState by ID."""
        raise NotImplementedError

    @abstractmethod
    def save(self, state: TaskState) -> None:
        """Save/update an existing TaskState."""
        raise NotImplementedError

    @abstractmethod
    def exists(self, task_id: str) -> bool:
        """Check if a TaskState exists for the given ID."""
        raise NotImplementedError

    @abstractmethod
    def list_tasks(self) -> list[str]:
        """Return the IDs of all persisted tasks."""
        raise NotImplementedError


class InMemoryTaskStateStore(TaskStateStore):
    """In-memory reference implementation of TaskStateStore."""

    def __init__(self):
        self._store: dict[str, TaskState] = {}

    def create(
        self,
        task_id: str,
        plan_id: str,
        plan: ExecutionPlan | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TaskState:
        """Create and store a new TaskState."""
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string.")
        if not isinstance(plan_id, str) or not plan_id.strip():
            raise ValueError("plan_id must be a non-empty string.")

        norm_id = task_id.strip()
        if norm_id in self._store:
            raise ValueError(f"Task already exists: {norm_id}")

        step_states = {}
        if plan is not None:
            for step in plan.steps:
                step_states[step.step_id] = StepState(
                    step_id=step.step_id,
                    status=StepStatus.NOT_STARTED,
                )

        state = TaskState(
            task_id=norm_id,
            plan_id=plan_id.strip(),
            status=TaskStatus.PENDING,
            step_states=step_states,
            plan=plan,
            metadata=metadata if metadata is not None else {},
        )
        self._store[norm_id] = copy.deepcopy(state)
        return copy.deepcopy(state)

    def get(self, task_id: str) -> TaskState:
        """Retrieve a copy of the persisted TaskState."""
        if not isinstance(task_id, str) or not task_id.strip():
            raise KeyError(f"Unknown task: {task_id}")

        norm_id = task_id.strip()
        if norm_id not in self._store:
            raise KeyError(f"Unknown task: {task_id}")

        return copy.deepcopy(self._store[norm_id])

    def save(self, state: TaskState) -> None:
        """Save a copy of the TaskState to the store."""
        if not isinstance(state, TaskState):
            raise TypeError("state must be an instance of TaskState.")

        state.updated_at = time.time()
        self._store[state.task_id] = copy.deepcopy(state)

    def exists(self, task_id: str) -> bool:
        """Check if a task ID exists in the store."""
        if not isinstance(task_id, str) or not task_id.strip():
            return False
        return task_id.strip() in self._store

    def list_tasks(self) -> list[str]:
        """List all stored task IDs."""
        return list(self._store.keys())
