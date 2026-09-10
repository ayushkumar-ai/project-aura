import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from core.model_router import TaskRequirements
from core.provenance import TaintedValue, is_tainted, unwrap_tainted, wrap_tainted


def _sanitize_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Recursively sanitize metadata dictionary to ensure no callables or untrusted permission overrides."""
    if not isinstance(meta, dict):
        raise TypeError("metadata must be a dictionary.")

    forbidden_keys = frozenset({
        "approved",
        "approval_status",
        "is_approved",
        "auto_approve",
        "permission",
        "authorized",
    })

    cleaned: dict[str, Any] = {}
    for k, v in meta.items():
        k_str = str(k)
        if k_str.lower() in forbidden_keys:
            continue
        if callable(v):
            continue
        if isinstance(v, dict):
            cleaned[k_str] = _sanitize_metadata(v)
        elif isinstance(v, (list, tuple)):
            cleaned[k_str] = [
                _sanitize_metadata(item) if isinstance(item, dict) else (str(item) if not callable(item) else "")
                for item in v
            ]
        elif isinstance(v, (str, int, float, bool)) or v is None:
            cleaned[k_str] = v
        else:
            cleaned[k_str] = repr(v)
    return cleaned


def _canonical_value(val: Any) -> Any:
    """Convert values into deterministic, JSON-serializable primitives preserving TaintedValue."""
    if isinstance(val, TaintedValue):
        return {
            "__tainted__": True,
            "raw_value": _canonical_value(val.raw_value),
            "is_untrusted": val.is_untrusted,
            "source_type": val.source_type,
            "originating_step_id": val.originating_step_id,
            "source_urls": list(val.source_urls),
            "metadata": {str(k): _canonical_value(v) for k, v in sorted(val.metadata.items())},
        }
    elif isinstance(val, (str, int, float, bool)) or val is None:
        return val
    elif isinstance(val, (list, tuple, set, frozenset)):
        return [_canonical_value(x) for x in val]
    elif isinstance(val, dict):
        return {str(k): _canonical_value(v) for k, v in sorted(val.items())}
    elif callable(val):
        raise ValueError("Cannot serialize callable value in plan or observation.")
    else:
        return repr(val)


def _restore_value(val: Any) -> Any:
    """Restore values from serialized JSON primitives restoring TaintedValue envelopes."""
    if isinstance(val, dict):
        if val.get("__tainted__") is True and "raw_value" in val:
            return wrap_tainted(
                value=_restore_value(val.get("raw_value")),
                is_untrusted=bool(val.get("is_untrusted", True)),
                source_type=str(val.get("source_type", "external_web")),
                originating_step_id=val.get("originating_step_id"),
                source_urls=val.get("source_urls", ()),
                metadata=dict(val.get("metadata", {})),
            )
        return {k: _restore_value(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [_restore_value(x) for x in val]
    return val


class StepStatus(str, Enum):
    """Authoritative execution lifecycle status of an autonomous plan step."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    PAUSED = "paused"

    def is_terminal(self) -> bool:
        """Check if step has reached a final execution state."""
        return self in (
            StepStatus.SUCCEEDED,
            StepStatus.FAILED,
            StepStatus.BLOCKED,
            StepStatus.SKIPPED,
            StepStatus.CANCELLED,
        )

    def is_successful(self) -> bool:
        """Check if step completed successfully."""
        return self == StepStatus.SUCCEEDED

    def is_failure(self) -> bool:
        """Check if step failed or was blocked."""
        return self in (StepStatus.FAILED, StepStatus.BLOCKED)


@dataclass(frozen=True)
class StepDependency:
    """Explicit dependency relation between plan steps."""

    step_id: str
    required_status: StepStatus = StepStatus.SUCCEEDED
    allow_failure: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.step_id, str) or not self.step_id.strip():
            raise ValueError("step_id must be a non-empty string.")
        object.__setattr__(self, "step_id", self.step_id.strip())

        if isinstance(self.required_status, str):
            object.__setattr__(self, "required_status", StepStatus(self.required_status))
        elif not isinstance(self.required_status, StepStatus):
            raise TypeError("required_status must be an instance of StepStatus.")

        if not isinstance(self.allow_failure, bool):
            raise TypeError("allow_failure must be a boolean.")

        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))


@dataclass(frozen=True)
class Observation:
    """Structured observation and result record from executing a plan step."""

    step_id: str
    task_id: str
    skill_name: str
    tool_name: str | None = None
    success: bool = True
    output: Any = None
    error: str | None = None
    is_untrusted: bool = False
    source_urls: tuple[str, ...] = field(default_factory=tuple)
    execution_time_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.step_id, str) or not self.step_id.strip():
            raise ValueError("step_id must be a non-empty string.")
        object.__setattr__(self, "step_id", self.step_id.strip())

        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("task_id must be a non-empty string.")
        object.__setattr__(self, "task_id", self.task_id.strip())

        if not isinstance(self.skill_name, str) or not self.skill_name.strip():
            raise ValueError("skill_name must be a non-empty string.")
        object.__setattr__(self, "skill_name", self.skill_name.strip())

        if self.tool_name is not None:
            if not isinstance(self.tool_name, str) or not self.tool_name.strip():
                raise ValueError("tool_name must be a non-empty string or None.")
            object.__setattr__(self, "tool_name", self.tool_name.strip())

        if not isinstance(self.success, bool):
            raise TypeError("success must be a boolean.")

        if callable(self.output):
            raise ValueError("Observation output cannot be callable.")

        if self.error is not None and not isinstance(self.error, str):
            raise TypeError("error must be a string or None.")

        # Determine taint and source_urls from output if output is TaintedValue
        untrusted = self.is_untrusted
        urls = list(self.source_urls) if isinstance(self.source_urls, (list, tuple)) else []
        if isinstance(self.output, TaintedValue):
            untrusted = untrusted or self.output.is_untrusted
            urls.extend(self.output.source_urls)

        object.__setattr__(self, "is_untrusted", untrusted)
        object.__setattr__(self, "source_urls", tuple(sorted(set(str(u).strip() for u in urls if str(u).strip()))))

        if not isinstance(self.execution_time_ms, (int, float)) or self.execution_time_ms < 0:
            raise ValueError("execution_time_ms must be a non-negative number.")
        object.__setattr__(self, "execution_time_ms", float(self.execution_time_ms))

        if not isinstance(self.timestamp, (int, float)):
            raise TypeError("timestamp must be a numeric timestamp.")
        object.__setattr__(self, "timestamp", float(self.timestamp))

        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))


@dataclass(frozen=True)
class AgentPlanStep:
    """Explicit, bounded executable step in an AgentPlan."""

    step_id: str
    skill_name: str
    objective: str = ""
    input_data: Any = field(default_factory=dict)
    dependencies: tuple[str, ...] = field(default_factory=tuple)
    task_requirements: TaskRequirements | None = None
    status: StepStatus = StepStatus.PENDING
    retry_count: int = 0
    max_retries: int = 2
    result: Observation | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.step_id, str) or not self.step_id.strip():
            raise ValueError("step_id must be a non-empty string.")
        object.__setattr__(self, "step_id", self.step_id.strip())

        if not isinstance(self.skill_name, str) or not self.skill_name.strip():
            raise ValueError("skill_name must be a non-empty string.")
        object.__setattr__(self, "skill_name", self.skill_name.strip())

        if not isinstance(self.objective, str):
            raise TypeError("objective must be a string.")
        object.__setattr__(self, "objective", self.objective.strip())

        if callable(self.input_data):
            raise ValueError("PlanStep input_data cannot be a callable in M10 explicit contracts.")

        # Normalize dependencies
        deps: list[str] = []
        if isinstance(self.dependencies, (list, tuple, set, frozenset)):
            for d in self.dependencies:
                if isinstance(d, StepDependency):
                    deps.append(d.step_id)
                elif isinstance(d, str) and d.strip():
                    deps.append(d.strip())
                else:
                    raise ValueError("Invalid dependency format.")
        else:
            raise TypeError("dependencies must be a sequence of strings or StepDependency instances.")
        object.__setattr__(self, "dependencies", tuple(deps))

        if self.task_requirements is not None and not isinstance(self.task_requirements, TaskRequirements):
            raise TypeError("task_requirements must be an instance of TaskRequirements or None.")

        if isinstance(self.status, str):
            object.__setattr__(self, "status", StepStatus(self.status))
        elif not isinstance(self.status, StepStatus):
            raise TypeError("status must be an instance of StepStatus.")

        if not isinstance(self.retry_count, int) or self.retry_count < 0:
            raise ValueError("retry_count must be a non-negative integer.")

        if not isinstance(self.max_retries, int) or self.max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer.")

        if self.result is not None and not isinstance(self.result, Observation):
            raise TypeError("result must be an instance of Observation or None.")

        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))


@dataclass(frozen=True)
class AgentPlan:
    """Explicit, immutable, deterministic multi-step agent execution plan."""

    plan_id: str = field(default_factory=lambda: str(uuid4()))
    task_goal: str = ""
    steps: tuple[AgentPlanStep, ...] = field(default_factory=tuple)
    status: StepStatus = StepStatus.PENDING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.plan_id, str) or not self.plan_id.strip():
            raise ValueError("plan_id must be a non-empty string.")
        object.__setattr__(self, "plan_id", self.plan_id.strip())

        if not isinstance(self.task_goal, str):
            raise TypeError("task_goal must be a string.")
        object.__setattr__(self, "task_goal", self.task_goal.strip())

        if isinstance(self.steps, (list, tuple)):
            for s in self.steps:
                if not isinstance(s, AgentPlanStep):
                    raise TypeError("All items in steps must be AgentPlanStep instances.")
            object.__setattr__(self, "steps", tuple(self.steps))
        else:
            raise TypeError("steps must be a sequence of AgentPlanStep instances.")

        if isinstance(self.status, str):
            object.__setattr__(self, "status", StepStatus(self.status))
        elif not isinstance(self.status, StepStatus):
            raise TypeError("status must be an instance of StepStatus.")

        if not isinstance(self.created_at, (int, float)):
            raise TypeError("created_at must be a numeric timestamp.")
        if not isinstance(self.updated_at, (int, float)):
            raise TypeError("updated_at must be a numeric timestamp.")

        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))

    def get_step(self, step_id: str) -> AgentPlanStep:
        """Retrieve a step by ID."""
        if not isinstance(step_id, str) or not step_id.strip():
            raise KeyError(f"Invalid step ID: '{step_id}'")
        clean_id = step_id.strip()
        for s in self.steps:
            if s.step_id == clean_id:
                return s
        raise KeyError(f"Step '{clean_id}' not found in plan '{self.plan_id}'.")

    def has_step(self, step_id: str) -> bool:
        """Check if step exists in plan."""
        if not isinstance(step_id, str) or not step_id.strip():
            return False
        clean_id = step_id.strip()
        return any(s.step_id == clean_id for s in self.steps)

    def get_ready_steps(self) -> tuple[AgentPlanStep, ...]:
        """Return all steps that are pending/ready/paused and have all dependencies satisfied."""
        completed_ids = {s.step_id for s in self.steps if s.status == StepStatus.SUCCEEDED}
        ready: list[AgentPlanStep] = []
        for s in self.steps:
            if s.status in (StepStatus.PENDING, StepStatus.READY, StepStatus.PAUSED):
                if all(dep in completed_ids for dep in s.dependencies):
                    ready.append(s)
        return tuple(ready)

    def is_completed(self) -> bool:
        """Check if all steps in the plan succeeded."""
        if not self.steps:
            return True
        return all(s.status == StepStatus.SUCCEEDED for s in self.steps)

    def is_failed(self) -> bool:
        """Check if any step in the plan failed and cannot be recovered."""
        return any(s.status in (StepStatus.FAILED, StepStatus.BLOCKED) for s in self.steps)

    def with_step_update(
        self,
        step_id: str,
        status: StepStatus,
        result: Observation | None = None,
        retry_count: int | None = None,
    ) -> "AgentPlan":
        """Return a new immutable AgentPlan with the given step state updated."""
        target_id = step_id.strip()
        new_steps: list[AgentPlanStep] = []
        found = False

        for s in self.steps:
            if s.step_id == target_id:
                found = True
                new_retries = retry_count if retry_count is not None else s.retry_count
                new_step = AgentPlanStep(
                    step_id=s.step_id,
                    skill_name=s.skill_name,
                    objective=s.objective,
                    input_data=s.input_data,
                    dependencies=s.dependencies,
                    task_requirements=s.task_requirements,
                    status=status,
                    retry_count=new_retries,
                    max_retries=s.max_retries,
                    result=result if result is not None else s.result,
                    metadata=dict(s.metadata),
                )
                new_steps.append(new_step)
            else:
                new_steps.append(s)

        if not found:
            raise KeyError(f"Step '{target_id}' not found in plan '{self.plan_id}'.")

        # Determine overall plan status
        new_plan_status = self.status
        if all(st.status == StepStatus.SUCCEEDED for st in new_steps):
            new_plan_status = StepStatus.SUCCEEDED
        elif any(st.status in (StepStatus.FAILED, StepStatus.BLOCKED) for st in new_steps):
            new_plan_status = StepStatus.FAILED
        elif any(st.status == StepStatus.RUNNING for st in new_steps):
            new_plan_status = StepStatus.RUNNING
        elif any(st.status == StepStatus.PAUSED for st in new_steps):
            new_plan_status = StepStatus.PAUSED

        return AgentPlan(
            plan_id=self.plan_id,
            task_goal=self.task_goal,
            steps=tuple(new_steps),
            status=new_plan_status,
            created_at=self.created_at,
            updated_at=time.time(),
            metadata=dict(self.metadata),
        )


@dataclass(frozen=True)
class ExecutionTrace:
    """Audit log of observations, decisions, and execution timings across a task."""

    trace_id: str = field(default_factory=lambda: str(uuid4()))
    task_id: str = ""
    plan_id: str = ""
    observations: tuple[Observation, ...] = field(default_factory=tuple)
    replan_history: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    tool_calls_count: int = 0
    total_execution_time_ms: float = 0.0
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.trace_id, str) or not self.trace_id.strip():
            raise ValueError("trace_id must be a non-empty string.")
        object.__setattr__(self, "trace_id", self.trace_id.strip())

        if not isinstance(self.task_id, str):
            raise TypeError("task_id must be a string.")
        object.__setattr__(self, "task_id", self.task_id.strip())

        if not isinstance(self.plan_id, str):
            raise TypeError("plan_id must be a string.")
        object.__setattr__(self, "plan_id", self.plan_id.strip())

        if isinstance(self.observations, (list, tuple)):
            for o in self.observations:
                if not isinstance(o, Observation):
                    raise TypeError("All items in observations must be Observation instances.")
            object.__setattr__(self, "observations", tuple(self.observations))
        else:
            raise TypeError("observations must be a sequence of Observation instances.")

        if isinstance(self.replan_history, (list, tuple)):
            object.__setattr__(self, "replan_history", tuple(dict(rh) for rh in self.replan_history))
        else:
            raise TypeError("replan_history must be a sequence of dictionaries.")

        if not isinstance(self.tool_calls_count, int) or self.tool_calls_count < 0:
            raise ValueError("tool_calls_count must be a non-negative integer.")

        if not isinstance(self.total_execution_time_ms, (int, float)) or self.total_execution_time_ms < 0:
            raise ValueError("total_execution_time_ms must be a non-negative number.")
        object.__setattr__(self, "total_execution_time_ms", float(self.total_execution_time_ms))

        if not isinstance(self.created_at, (int, float)):
            raise TypeError("created_at must be a numeric timestamp.")

        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))

    def add_observation(self, observation: Observation) -> "ExecutionTrace":
        """Return a new immutable ExecutionTrace with the observation appended."""
        if not isinstance(observation, Observation):
            raise TypeError("observation must be an instance of Observation.")
        new_obs = self.observations + (observation,)
        tools_delta = 1 if observation.tool_name else 0
        new_time = self.total_execution_time_ms + observation.execution_time_ms
        return ExecutionTrace(
            trace_id=self.trace_id,
            task_id=self.task_id,
            plan_id=self.plan_id,
            observations=new_obs,
            replan_history=self.replan_history,
            tool_calls_count=self.tool_calls_count + tools_delta,
            total_execution_time_ms=new_time,
            created_at=self.created_at,
            metadata=dict(self.metadata),
        )

    def add_replan(self, replan_info: dict[str, Any]) -> "ExecutionTrace":
        """Return a new immutable ExecutionTrace with a replan event recorded."""
        if not isinstance(replan_info, dict):
            raise TypeError("replan_info must be a dictionary.")
        new_history = self.replan_history + (_sanitize_metadata(replan_info),)
        return ExecutionTrace(
            trace_id=self.trace_id,
            task_id=self.task_id,
            plan_id=self.plan_id,
            observations=self.observations,
            replan_history=new_history,
            tool_calls_count=self.tool_calls_count,
            total_execution_time_ms=self.total_execution_time_ms,
            created_at=self.created_at,
            metadata=dict(self.metadata),
        )


def serialize_observation(obs: Observation) -> dict[str, Any]:
    """Serialize an Observation into a deterministic JSON-compatible dictionary preserving provenance."""
    if not isinstance(obs, Observation):
        raise TypeError("obs must be an Observation instance.")
    return {
        "step_id": obs.step_id,
        "task_id": obs.task_id,
        "skill_name": obs.skill_name,
        "tool_name": obs.tool_name,
        "success": obs.success,
        "output": _canonical_value(obs.output),
        "error": obs.error,
        "is_untrusted": obs.is_untrusted,
        "source_urls": list(obs.source_urls),
        "execution_time_ms": obs.execution_time_ms,
        "timestamp": obs.timestamp,
        "metadata": _canonical_value(obs.metadata),
    }


def deserialize_observation(data: dict[str, Any]) -> Observation:
    """Deserialize an Observation from a dictionary restoring TaintedValue envelopes."""
    if not isinstance(data, dict):
        raise TypeError("data must be a dictionary.")
    return Observation(
        step_id=str(data.get("step_id", "")),
        task_id=str(data.get("task_id", "")),
        skill_name=str(data.get("skill_name", "")),
        tool_name=data.get("tool_name"),
        success=bool(data.get("success", True)),
        output=_restore_value(data.get("output")),
        error=data.get("error"),
        is_untrusted=bool(data.get("is_untrusted", False)),
        source_urls=tuple(data.get("source_urls", ())),
        execution_time_ms=float(data.get("execution_time_ms", 0.0)),
        timestamp=float(data.get("timestamp", time.time())),
        metadata=dict(data.get("metadata", {})),
    )


def serialize_agent_plan(plan: AgentPlan) -> dict[str, Any]:
    """Serialize an AgentPlan into a deterministic dictionary."""
    if not isinstance(plan, AgentPlan):
        raise TypeError("plan must be an AgentPlan instance.")
    steps_data = []
    for s in plan.steps:
        s_dict: dict[str, Any] = {
            "step_id": s.step_id,
            "skill_name": s.skill_name,
            "objective": s.objective,
            "input_data": _canonical_value(s.input_data),
            "dependencies": list(s.dependencies),
            "status": s.status.value,
            "retry_count": s.retry_count,
            "max_retries": s.max_retries,
            "result": serialize_observation(s.result) if s.result is not None else None,
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
        "plan_id": plan.plan_id,
        "task_goal": plan.task_goal,
        "steps": steps_data,
        "status": plan.status.value,
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
        "metadata": _canonical_value(plan.metadata),
    }


def deserialize_agent_plan(data: dict[str, Any]) -> AgentPlan:
    """Deserialize an AgentPlan from a dictionary."""
    if not isinstance(data, dict):
        raise TypeError("data must be a dictionary.")

    steps: list[AgentPlanStep] = []
    for sd in data.get("steps", []):
        if not isinstance(sd, dict):
            continue
        req_obj = None
        if "task_requirements" in sd and isinstance(sd["task_requirements"], dict):
            tr_d = sd["task_requirements"]
            req_obj = TaskRequirements(
                required_capabilities=tr_d.get("required_capabilities", ()),
                preferred_model=tr_d.get("preferred_model"),
                preferred_provider=tr_d.get("preferred_provider"),
            )

        res_obj = None
        if "result" in sd and isinstance(sd["result"], dict):
            res_obj = deserialize_observation(sd["result"])

        step = AgentPlanStep(
            step_id=str(sd.get("step_id", "")),
            skill_name=str(sd.get("skill_name", "")),
            objective=str(sd.get("objective", "")),
            input_data=_restore_value(sd.get("input_data", {})),
            dependencies=tuple(sd.get("dependencies", ())),
            task_requirements=req_obj,
            status=StepStatus(sd.get("status", StepStatus.PENDING.value)),
            retry_count=int(sd.get("retry_count", 0)),
            max_retries=int(sd.get("max_retries", 2)),
            result=res_obj,
            metadata=dict(sd.get("metadata", {})),
        )
        steps.append(step)

    return AgentPlan(
        plan_id=str(data.get("plan_id", uuid4())),
        task_goal=str(data.get("task_goal", "")),
        steps=tuple(steps),
        status=StepStatus(data.get("status", StepStatus.PENDING.value)),
        created_at=float(data.get("created_at", time.time())),
        updated_at=float(data.get("updated_at", time.time())),
        metadata=dict(data.get("metadata", {})),
    )


def serialize_execution_trace(trace: ExecutionTrace) -> dict[str, Any]:
    """Serialize an ExecutionTrace into a dictionary."""
    if not isinstance(trace, ExecutionTrace):
        raise TypeError("trace must be an ExecutionTrace instance.")
    return {
        "trace_id": trace.trace_id,
        "task_id": trace.task_id,
        "plan_id": trace.plan_id,
        "observations": [serialize_observation(o) for o in trace.observations],
        "replan_history": [_canonical_value(rh) for rh in trace.replan_history],
        "tool_calls_count": trace.tool_calls_count,
        "total_execution_time_ms": trace.total_execution_time_ms,
        "created_at": trace.created_at,
        "metadata": _canonical_value(trace.metadata),
    }


def deserialize_execution_trace(data: dict[str, Any]) -> ExecutionTrace:
    """Deserialize an ExecutionTrace from a dictionary."""
    if not isinstance(data, dict):
        raise TypeError("data must be a dictionary.")
    observations = [
        deserialize_observation(od)
        for od in data.get("observations", [])
        if isinstance(od, dict)
    ]
    replan_history = [
        dict(rh)
        for rh in data.get("replan_history", [])
        if isinstance(rh, dict)
    ]
    return ExecutionTrace(
        trace_id=str(data.get("trace_id", uuid4())),
        task_id=str(data.get("task_id", "")),
        plan_id=str(data.get("plan_id", "")),
        observations=tuple(observations),
        replan_history=tuple(replan_history),
        tool_calls_count=int(data.get("tool_calls_count", 0)),
        total_execution_time_ms=float(data.get("total_execution_time_ms", 0.0)),
        created_at=float(data.get("created_at", time.time())),
        metadata=dict(data.get("metadata", {})),
    )
