"""File-backed persistent TaskStateStore implementation (M18).

Provides durable, atomic, and thread-safe persistence for TaskState records across
system restarts and execution interruptions, fully preserving:
- StepState and TaskStatus lifecycle transitions
- ExecutionPlan and AgentPlan structures
- Goal lineage (goal_id, parent_goal_id)
- TaintedValue provenance across inputs, outputs, and metadata
"""

from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from core.agent_plan import (
    AgentPlan,
    _canonical_value,
    _restore_value,
    deserialize_agent_plan,
    serialize_agent_plan,
)
from core.agent_runtime import AgentResult
from core.model_router import TaskRequirements
from core.provenance import TaintedValue, is_tainted, unwrap_tainted, wrap_tainted
from core.task_planner import ExecutionPlan, PlanStep
from core.task_state import StepState, StepStatus, TaskState, TaskStatus
from core.task_state_store import TaskStateStore

logger = logging.getLogger("aura.file_task_state_store")


def _serialize_agent_result(res: AgentResult | None) -> dict[str, Any] | None:
    if res is None:
        return None
    return {
        "success": res.success,
        "skill_name": res.skill_name,
        "output": _canonical_value(res.output),
        "selected_model_id": res.selected_model_id,
        "selected_provider_id": res.selected_provider_id,
        "error": res.error,
        "request_id": str(res.request_id) if res.request_id else None,
        "metadata": _canonical_value(res.metadata),
    }


def _deserialize_agent_result(data: dict[str, Any] | None) -> AgentResult | None:
    if not isinstance(data, dict):
        return None
    req_id = None
    if data.get("request_id"):
        try:
            req_id = UUID(str(data["request_id"]))
        except Exception:
            req_id = None
    return AgentResult(
        success=bool(data.get("success", False)),
        skill_name=str(data.get("skill_name", "")),
        output=_restore_value(data.get("output")),
        selected_model_id=data.get("selected_model_id"),
        selected_provider_id=data.get("selected_provider_id"),
        error=data.get("error"),
        request_id=req_id,
        metadata=_restore_value(data.get("metadata", {})),
    )


def _serialize_step_state(step: StepState) -> dict[str, Any]:
    return {
        "step_id": step.step_id,
        "status": step.status.value,
        "agent_result": _serialize_agent_result(step.agent_result),
        "error": step.error,
        "output": _canonical_value(step.output),
        "started_at": step.started_at,
        "completed_at": step.completed_at,
        "metadata": _canonical_value(step.metadata),
    }


def _deserialize_step_state(data: dict[str, Any]) -> StepState:
    return StepState(
        step_id=str(data.get("step_id", "")),
        status=StepStatus(data.get("status", StepStatus.NOT_STARTED.value)),
        agent_result=_deserialize_agent_result(data.get("agent_result")),
        error=data.get("error"),
        output=_restore_value(data.get("output")),
        started_at=data.get("started_at"),
        completed_at=data.get("completed_at"),
        metadata=_restore_value(data.get("metadata", {})),
    )


def _serialize_plan(plan: ExecutionPlan | AgentPlan | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    if isinstance(plan, AgentPlan):
        return {
            "__type__": "AgentPlan",
            "data": serialize_agent_plan(plan),
        }
    elif isinstance(plan, ExecutionPlan):
        steps_data = []
        for s in plan.steps:
            s_dict: dict[str, Any] = {
                "step_id": s.step_id,
                "skill_name": s.skill_name,
                "input_data": _canonical_value(s.input_data),
                "dependencies": list(s.dependencies),
                "metadata": _canonical_value(s.metadata),
            }
            if s.task_requirements is not None:
                s_dict["task_requirements"] = {
                    "required_capabilities": list(s.task_requirements.required_capabilities),
                    "preferred_model": s.task_requirements.preferred_model,
                    "preferred_provider": s.task_requirements.preferred_provider,
                }
            steps_data.append(s_dict)
        return {
            "__type__": "ExecutionPlan",
            "plan_id": plan.plan_id,
            "steps": steps_data,
            "metadata": _canonical_value(plan.metadata),
        }
    return None


def _deserialize_plan(data: dict[str, Any] | None) -> ExecutionPlan | AgentPlan | None:
    if not isinstance(data, dict):
        return None
    plan_type = data.get("__type__")
    if plan_type == "AgentPlan" and "data" in data and isinstance(data["data"], dict):
        return deserialize_agent_plan(data["data"])
    elif plan_type == "ExecutionPlan":
        steps: list[PlanStep] = []
        for sd in data.get("steps", []):
            if not isinstance(sd, dict):
                continue
            req = None
            if "task_requirements" in sd and isinstance(sd["task_requirements"], dict):
                tr = sd["task_requirements"]
                req = TaskRequirements(
                    required_capabilities=tuple(tr.get("required_capabilities", ())),
                    preferred_model=tr.get("preferred_model"),
                    preferred_provider=tr.get("preferred_provider"),
                )
            steps.append(
                PlanStep(
                    step_id=str(sd.get("step_id", "")),
                    skill_name=str(sd.get("skill_name", "")),
                    input_data=_restore_value(sd.get("input_data", {})),
                    dependencies=tuple(sd.get("dependencies", ())),
                    task_requirements=req,
                    metadata=_restore_value(sd.get("metadata", {})),
                )
            )
        return ExecutionPlan(
            plan_id=str(data.get("plan_id", str(uuid4()))),
            steps=tuple(steps),
            metadata=_restore_value(data.get("metadata", {})),
        )
    return None


def serialize_task_state(state: TaskState) -> dict[str, Any]:
    """Serialize a TaskState object into a JSON-compatible dictionary."""
    if not isinstance(state, TaskState):
        raise TypeError("state must be a TaskState instance.")
    return {
        "task_id": state.task_id,
        "plan_id": state.plan_id,
        "status": state.status.value,
        "step_states": {
            k: _serialize_step_state(v)
            for k, v in state.step_states.items()
        },
        "plan": _serialize_plan(state.plan),
        "failed_step_id": state.failed_step_id,
        "error": state.error,
        "final_output": _canonical_value(state.final_output),
        "created_at": state.created_at,
        "updated_at": state.updated_at,
        "metadata": _canonical_value(state.metadata),
        "goal_id": state.goal_id,
        "parent_goal_id": state.parent_goal_id,
    }


def deserialize_task_state(data: dict[str, Any]) -> TaskState:
    """Deserialize a TaskState object from a dictionary."""
    if not isinstance(data, dict):
        raise TypeError("data must be a dictionary.")

    step_states: dict[str, StepState] = {}
    for k, v in data.get("step_states", {}).items():
        if isinstance(v, dict):
            step_states[str(k)] = _deserialize_step_state(v)

    return TaskState(
        task_id=str(data.get("task_id", "")),
        plan_id=str(data.get("plan_id", "")),
        status=TaskStatus(data.get("status", TaskStatus.PENDING.value)),
        step_states=step_states,
        plan=_deserialize_plan(data.get("plan")),
        failed_step_id=data.get("failed_step_id"),
        error=data.get("error"),
        final_output=_restore_value(data.get("final_output")),
        created_at=float(data.get("created_at", time.time())),
        updated_at=float(data.get("updated_at", time.time())),
        metadata=_restore_value(data.get("metadata", {})),
        goal_id=data.get("goal_id"),
        parent_goal_id=data.get("parent_goal_id"),
    )


class FileTaskStateStore(TaskStateStore):
    """File-backed, thread-safe, crash-resilient implementation of TaskStateStore."""

    def __init__(self, storage_dir: str | Path) -> None:
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _task_path(self, task_id: str) -> Path:
        clean_id = str(task_id).strip()
        safe_filename = clean_id.replace("/", "_").replace("\\", "_").replace(":", "_")
        return self.storage_dir / f"{safe_filename}.json"

    def _write_json_atomic(self, path: Path, data: Any) -> None:
        temp_file = tempfile.NamedTemporaryFile(
            "w",
            dir=str(path.parent),
            delete=False,
            encoding="utf-8",
        )
        try:
            json.dump(data, temp_file, indent=2)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_file.close()
            shutil.move(temp_file.name, str(path))
        except Exception:
            if os.path.exists(temp_file.name):
                os.remove(temp_file.name)
            raise

    def create(
        self,
        task_id: str,
        plan_id: str,
        plan: ExecutionPlan | AgentPlan | None = None,
        metadata: dict[str, Any] | None = None,
        goal_id: str | None = None,
        parent_goal_id: str | None = None,
    ) -> TaskState:
        """Create and persist a new TaskState to disk."""
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string.")
        if not isinstance(plan_id, str) or not plan_id.strip():
            raise ValueError("plan_id must be a non-empty string.")

        norm_id = task_id.strip()
        path = self._task_path(norm_id)

        with self._lock:
            if path.exists():
                raise ValueError(f"Task already exists: {norm_id}")

            step_states: dict[str, StepState] = {}
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
                goal_id=goal_id,
                parent_goal_id=parent_goal_id,
            )
            self._write_json_atomic(path, serialize_task_state(state))
            return copy.deepcopy(state)

    def get(self, task_id: str) -> TaskState:
        """Retrieve a persisted TaskState from disk."""
        if not isinstance(task_id, str) or not task_id.strip():
            raise KeyError(f"Unknown task: {task_id}")

        norm_id = task_id.strip()
        path = self._task_path(norm_id)

        with self._lock:
            if not path.exists():
                raise KeyError(f"Unknown task: {task_id}")

            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return deserialize_task_state(data)
            except Exception as e:
                logger.error("Error reading task state from '%s': %s", path, e)
                raise KeyError(f"Corrupted or invalid task state for '{task_id}': {e}") from e

    def save(self, state: TaskState) -> None:
        """Persist/update a TaskState on disk."""
        if not isinstance(state, TaskState):
            raise TypeError("state must be an instance of TaskState.")

        path = self._task_path(state.task_id)
        with self._lock:
            state.updated_at = time.time()
            self._write_json_atomic(path, serialize_task_state(state))

    def exists(self, task_id: str) -> bool:
        """Check if a TaskState file exists on disk."""
        if not isinstance(task_id, str) or not task_id.strip():
            return False
        path = self._task_path(task_id.strip())
        with self._lock:
            return path.exists()

    def list_tasks(self) -> list[str]:
        """List all stored task IDs."""
        with self._lock:
            tasks: list[str] = []
            for file in sorted(self.storage_dir.glob("*.json")):
                try:
                    with open(file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    tid = data.get("task_id")
                    if tid:
                        tasks.append(str(tid))
                except Exception as e:
                    logger.warning("Error reading task file '%s': %s", file, e)
            return tasks

    def delete(self, task_id: str) -> bool:
        """Delete a TaskState file from disk."""
        if not isinstance(task_id, str) or not task_id.strip():
            return False
        path = self._task_path(task_id.strip())
        with self._lock:
            if path.exists():
                path.unlink()
                return True
            return False
