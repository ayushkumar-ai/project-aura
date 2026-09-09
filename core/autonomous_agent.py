import copy
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from app.config import settings
from core.agent_plan import (
    AgentPlan,
    AgentPlanStep,
    ExecutionTrace,
    Observation,
    StepDependency,
    StepStatus,
    deserialize_agent_plan,
    deserialize_execution_trace,
    serialize_agent_plan,
    serialize_execution_trace,
)
from core.agent_runtime import AgentRequest, AgentResult, AgentRuntime
from core.approval import ApprovalDecisionType, ApprovalGateway, ApprovalRequest
from core.model_router import TaskRequirements
from core.provenance import (
    TaintedValue,
    extract_provenance,
    is_tainted,
    render_for_prompt,
    unwrap_tainted,
    wrap_tainted,
)
from core.task_planner import ReplanContext, TaskPlanner
from core.task_state import StepState, StepStatus as LegacyStepStatus, TaskState, TaskStatus
from core.task_state_store import TaskStateStore

logger = logging.getLogger("aura.autonomous_agent")


def is_recoverable_failure(agent_result: AgentResult | None = None, error: str | None = None) -> bool:
    """Check whether an execution failure is potentially recoverable via retry or replanning."""
    err_text = error
    if err_text is None and agent_result is not None:
        err_text = agent_result.error

    if not err_text:
        return True

    err_lower = err_text.lower()
    non_recoverable_markers = [
        "not authorized",
        "denied by policy",
        "permissionerror",
        "approval rejected",
        "security review denied",
        "unauthorized",
        "denied by approval gateway",
        "security boundary",
        "access denied",
        "untrusted injection attempt",
    ]
    for marker in non_recoverable_markers:
        if marker in err_lower:
            return False

    return True


@dataclass(frozen=True)
class AgentLoopConfig:
    """Configuration parameters and resource bounds for the autonomous agent execution loop."""

    max_plan_steps: int = 20
    max_execution_iterations: int = 50
    max_retries_per_step: int = 2
    max_total_execution_time: float = 300.0
    max_tool_calls: int = 50
    max_replan_depth: int = 3
    auto_pause_on_approval: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.max_plan_steps, int) or self.max_plan_steps <= 0:
            raise ValueError("max_plan_steps must be a positive integer.")
        if not isinstance(self.max_execution_iterations, int) or self.max_execution_iterations <= 0:
            raise ValueError("max_execution_iterations must be a positive integer.")
        if not isinstance(self.max_retries_per_step, int) or self.max_retries_per_step < 0:
            raise ValueError("max_retries_per_step must be a non-negative integer.")
        if not isinstance(self.max_total_execution_time, (int, float)) or self.max_total_execution_time <= 0:
            raise ValueError("max_total_execution_time must be a positive number.")
        if not isinstance(self.max_tool_calls, int) or self.max_tool_calls <= 0:
            raise ValueError("max_tool_calls must be a positive integer.")
        if not isinstance(self.max_replan_depth, int) or self.max_replan_depth < 0:
            raise ValueError("max_replan_depth must be a non-negative integer.")
        if not isinstance(self.auto_pause_on_approval, bool):
            raise TypeError("auto_pause_on_approval must be a boolean.")


@dataclass(frozen=True)
class AutonomousAgentResult:
    """Represents the final outcome of an autonomous agent execution."""

    success: bool
    task_id: str
    plan: AgentPlan
    trace: ExecutionTrace
    final_output: Any = None
    error: str | None = None
    is_paused: bool = False
    approval_request: ApprovalRequest | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AutonomousAgentExecutor:
    """Autonomous Agent Loop implementing bounded Plan -> Execute -> Observe -> Adapt cycles."""

    def __init__(
        self,
        runtime: AgentRuntime,
        planner: TaskPlanner | None = None,
        state_store: TaskStateStore | None = None,
        approval_gateway: ApprovalGateway | None = None,
        config: AgentLoopConfig | None = None,
    ):
        if not isinstance(runtime, AgentRuntime):
            raise TypeError("runtime must be an instance of AgentRuntime.")

        if planner is not None and not isinstance(planner, TaskPlanner):
            raise TypeError("planner must be an instance of TaskPlanner or None.")

        if state_store is not None and not isinstance(state_store, TaskStateStore):
            raise TypeError("state_store must be an instance of TaskStateStore or None.")

        if approval_gateway is not None and not isinstance(approval_gateway, ApprovalGateway):
            raise TypeError("approval_gateway must be an instance of ApprovalGateway or None.")

        if config is not None and not isinstance(config, AgentLoopConfig):
            raise TypeError("config must be an instance of AgentLoopConfig or None.")

        self.runtime = runtime
        self.planner = planner if planner is not None else TaskPlanner(runtime.skill_registry)
        self.state_store = state_store
        self.approval_gateway = approval_gateway
        if self.approval_gateway is not None and self.approval_gateway.skill_registry is None:
            self.approval_gateway.skill_registry = runtime.skill_registry
        self.config = config if config is not None else AgentLoopConfig(
            max_plan_steps=getattr(settings, "aura_max_plan_steps", 20),
            max_execution_iterations=getattr(settings, "aura_max_execution_iterations", 50),
            max_retries_per_step=getattr(settings, "aura_max_retries_per_step", 2),
            max_total_execution_time=getattr(settings, "aura_max_total_execution_time", 300.0),
            max_tool_calls=getattr(settings, "aura_max_tool_calls", 50),
            max_replan_depth=getattr(settings, "aura_max_replan_depth", 3),
        )

    def _resolve_step_input(
        self,
        step: AgentPlanStep,
        plan: AgentPlan,
        trace: ExecutionTrace,
    ) -> Any:
        """Resolve step input data, propagating TaintedValue provenance across dependencies."""
        input_data = step.input_data

        obs_by_step: dict[str, Observation] = {}
        for obs in trace.observations:
            obs_by_step[obs.step_id] = obs

        def _resolve_nested(val: Any) -> Any:
            if isinstance(val, dict):
                if "$from_step" in val and len(val) == 1:
                    source_id = str(val["$from_step"]).strip()
                    if source_id in obs_by_step:
                        return obs_by_step[source_id].output
                    step_obj = next((s for s in plan.steps if s.step_id == source_id), None)
                    if step_obj and step_obj.result:
                        return step_obj.result.output
                return {k: _resolve_nested(v) for k, v in val.items()}
            elif isinstance(val, list):
                return [_resolve_nested(item) for item in val]
            elif isinstance(val, tuple):
                return tuple(_resolve_nested(item) for item in val)
            return val

        resolved = _resolve_nested(input_data)

        # If resolved is empty dict/None/empty and there are dependencies, use last dependency's output
        if (resolved is None or resolved == "" or resolved == {}) and step.dependencies:
            last_dep = step.dependencies[-1]
            if last_dep in obs_by_step:
                return obs_by_step[last_dep].output
            step_obj = next((s for s in plan.steps if s.step_id == last_dep), None)
            if step_obj and step_obj.result:
                return step_obj.result.output

        return resolved

    def run(
        self,
        task: str,
        task_id: str | None = None,
        task_requirements: TaskRequirements | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AutonomousAgentResult:
        """Plan and autonomously execute a task from natural language."""
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task must be a non-empty string.")

        actual_task_id = str(task_id).strip() if task_id else str(uuid4())
        meta = dict(metadata) if metadata is not None else {}

        # 1. Plan creation
        agent_plan = self.planner.plan_agent(
            task=task.strip(),
            task_requirements=task_requirements,
            metadata=meta,
        )

        return self.execute_plan(
            plan=agent_plan,
            task_id=actual_task_id,
            task_description=task.strip(),
        )

    def execute_plan(
        self,
        plan: AgentPlan,
        task_id: str | None = None,
        task_description: str | None = None,
        trace: ExecutionTrace | None = None,
    ) -> AutonomousAgentResult:
        """Execute an AgentPlan through the iterative bounded execution loop."""
        if not isinstance(plan, AgentPlan):
            raise TypeError("plan must be an instance of AgentPlan.")

        # Validate plan structure & skill requirements
        self.planner.validate_agent_plan(plan)

        if len(plan.steps) > self.config.max_plan_steps:
            raise ValueError(
                f"Plan step count ({len(plan.steps)}) exceeds configured maximum ({self.config.max_plan_steps})."
            )

        actual_task_id = str(task_id).strip() if task_id else str(uuid4())
        task_desc = task_description or plan.task_goal or "Autonomous Agent Execution"

        # Initialize or reuse trace
        current_trace = trace if trace is not None else ExecutionTrace(
            task_id=actual_task_id,
            plan_id=plan.plan_id,
        )

        current_plan = plan
        start_time = time.time()
        iteration_count = 0

        # State persistence initialization
        task_state: TaskState | None = None
        if self.state_store is not None:
            g_id = current_plan.metadata.get("goal_id") if isinstance(current_plan.metadata, dict) else None
            p_g_id = current_plan.metadata.get("parent_goal_id") if isinstance(current_plan.metadata, dict) else None
            if self.state_store.exists(actual_task_id):
                task_state = self.state_store.get(actual_task_id)
                task_state.status = TaskStatus.RUNNING
                task_state.plan = current_plan
                if g_id and not task_state.goal_id:
                    task_state.goal_id = str(g_id).strip()
                if p_g_id and not task_state.parent_goal_id:
                    task_state.parent_goal_id = str(p_g_id).strip()
                self.state_store.save(task_state)
            else:
                task_state = self.state_store.create(
                    task_id=actual_task_id,
                    plan_id=current_plan.plan_id,
                    plan=current_plan,
                    goal_id=str(g_id).strip() if g_id else None,
                    parent_goal_id=str(p_g_id).strip() if p_g_id else None,
                )
                task_state.status = TaskStatus.RUNNING
                self.state_store.save(task_state)

        # Main Loop: bounded by max_execution_iterations and max_total_execution_time
        while iteration_count < self.config.max_execution_iterations:
            iteration_count += 1

            # 1. Check wall-clock timeout
            elapsed_total = time.time() - start_time
            if elapsed_total > self.config.max_total_execution_time:
                err_msg = (
                    f"Execution exceeded max_total_execution_time limit "
                    f"({self.config.max_total_execution_time}s)."
                )
                current_plan = AgentPlan(
                    plan_id=current_plan.plan_id,
                    task_goal=current_plan.task_goal,
                    steps=current_plan.steps,
                    status=StepStatus.FAILED,
                )
                self._persist_state(actual_task_id, current_plan, current_trace, TaskStatus.FAILED, err_msg)
                return AutonomousAgentResult(
                    success=False,
                    task_id=actual_task_id,
                    plan=current_plan,
                    trace=current_trace,
                    error=err_msg,
                )

            # 2. Check tool calls limit
            if current_trace.tool_calls_count > self.config.max_tool_calls:
                err_msg = (
                    f"Execution exceeded max_tool_calls limit "
                    f"({self.config.max_tool_calls})."
                )
                current_plan = AgentPlan(
                    plan_id=current_plan.plan_id,
                    task_goal=current_plan.task_goal,
                    steps=current_plan.steps,
                    status=StepStatus.FAILED,
                )
                self._persist_state(actual_task_id, current_plan, current_trace, TaskStatus.FAILED, err_msg)
                return AutonomousAgentResult(
                    success=False,
                    task_id=actual_task_id,
                    plan=current_plan,
                    trace=current_trace,
                    error=err_msg,
                )

            # 3. Check if all steps succeeded
            if current_plan.is_completed():
                final_out = self._get_final_output(current_plan, current_trace)
                self._persist_state(actual_task_id, current_plan, current_trace, TaskStatus.COMPLETED, final_output=final_out)
                return AutonomousAgentResult(
                    success=True,
                    task_id=actual_task_id,
                    plan=current_plan,
                    trace=current_trace,
                    final_output=final_out,
                )

            # 4. Check for ready steps
            ready_steps = current_plan.get_ready_steps()
            if not ready_steps:
                # Check if there are steps that are permanently blocked or failed
                pending_or_ready = [s for s in current_plan.steps if s.status in (StepStatus.PENDING, StepStatus.READY)]
                if pending_or_ready:
                    # Some steps are pending but their dependencies failed or were skipped
                    err_msg = "Execution deadlock or unsatisfied dependencies with no remaining ready steps."
                    current_plan = AgentPlan(
                        plan_id=current_plan.plan_id,
                        task_goal=current_plan.task_goal,
                        steps=current_plan.steps,
                        status=StepStatus.FAILED,
                    )
                    self._persist_state(actual_task_id, current_plan, current_trace, TaskStatus.FAILED, err_msg)
                    return AutonomousAgentResult(
                        success=False,
                        task_id=actual_task_id,
                        plan=current_plan,
                        trace=current_trace,
                        error=err_msg,
                    )
                else:
                    err_msg = "Execution finished without all steps succeeding."
                    self._persist_state(actual_task_id, current_plan, current_trace, TaskStatus.FAILED, err_msg)
                    return AutonomousAgentResult(
                        success=False,
                        task_id=actual_task_id,
                        plan=current_plan,
                        trace=current_trace,
                        error=err_msg,
                    )

            # Pick the next ready step
            step = ready_steps[0]

            # 5. Approval Gateway Boundary
            if self.approval_gateway is not None:
                decision = self.approval_gateway.evaluate_step(step, current_plan, actual_task_id)

                if decision.is_denied:
                    logger.warning("Step '%s' denied by approval gateway: %s", step.step_id, decision.reason)
                    obs = Observation(
                        step_id=step.step_id,
                        task_id=actual_task_id,
                        skill_name=step.skill_name,
                        success=False,
                        error=decision.reason,
                    )
                    current_trace = current_trace.add_observation(obs)
                    current_plan = current_plan.with_step_update(step.step_id, status=StepStatus.FAILED, result=obs)
                    self._persist_state(actual_task_id, current_plan, current_trace, TaskStatus.FAILED, decision.reason)
                    return AutonomousAgentResult(
                        success=False,
                        task_id=actual_task_id,
                        plan=current_plan,
                        trace=current_trace,
                        error=decision.reason,
                        approval_request=decision.approval_request,
                        metadata={"denied": True},
                    )

                elif decision.requires_approval:
                    logger.info("Step '%s' requires approval: %s", step.step_id, decision.reason)
                    if self.config.auto_pause_on_approval:
                        obs = Observation(
                            step_id=step.step_id,
                            task_id=actual_task_id,
                            skill_name=step.skill_name,
                            success=False,
                            error=f"Awaiting approval: {decision.reason}",
                        )
                        current_plan = current_plan.with_step_update(step.step_id, status=StepStatus.PAUSED, result=obs)
                        self._persist_state(actual_task_id, current_plan, current_trace, TaskStatus.PAUSED, decision.reason)
                        return AutonomousAgentResult(
                            success=False,
                            task_id=actual_task_id,
                            plan=current_plan,
                            trace=current_trace,
                            is_paused=True,
                            approval_request=decision.approval_request,
                            error=f"Step '{step.step_id}' requires approval: {decision.reason}",
                            metadata={"approval_required": True},
                        )
                    else:
                        obs = Observation(
                            step_id=step.step_id,
                            task_id=actual_task_id,
                            skill_name=step.skill_name,
                            success=False,
                            error=f"Approval required but auto_pause_on_approval is disabled.",
                        )
                        current_trace = current_trace.add_observation(obs)
                        current_plan = current_plan.with_step_update(step.step_id, status=StepStatus.FAILED, result=obs)
                        self._persist_state(actual_task_id, current_plan, current_trace, TaskStatus.FAILED, obs.error)
                        return AutonomousAgentResult(
                            success=False,
                            task_id=actual_task_id,
                            plan=current_plan,
                            trace=current_trace,
                            error=obs.error,
                        )

            # 6. Resolve Step Input
            resolved_input = self._resolve_step_input(step, current_plan, current_trace)

            # Update step status to RUNNING
            current_plan = current_plan.with_step_update(step.step_id, status=StepStatus.RUNNING)
            self._persist_state(actual_task_id, current_plan, current_trace, TaskStatus.RUNNING)

            # Prepare execution request
            meta = dict(step.metadata)
            meta["agent_plan_id"] = current_plan.plan_id
            meta["step_id"] = step.step_id
            meta["task_id"] = actual_task_id

            agent_req = AgentRequest(
                skill_name=step.skill_name,
                input_data=resolved_input,
                task_requirements=step.task_requirements,
                metadata=meta,
            )

            # 7. Step Execution
            step_start = time.time()
            try:
                agent_res = self.runtime.execute(agent_req)
            except Exception as e:
                logger.error("Exception executing step '%s': %s", step.step_id, e)
                agent_res = AgentResult(
                    success=False,
                    skill_name=step.skill_name,
                    error=str(e),
                )
            step_duration_ms = (time.time() - step_start) * 1000.0

            # 8. Construct Observation
            is_untrusted_output = False
            source_urls_tuple: tuple[str, ...] = ()
            if isinstance(agent_res.output, TaintedValue):
                is_untrusted_output = agent_res.output.is_untrusted
                source_urls_tuple = agent_res.output.source_urls

            declared_tools = self.runtime.skill_registry.get(step.skill_name).tools if self.runtime.skill_registry.has(step.skill_name) else ()
            tool_name = declared_tools[0] if declared_tools else None

            obs = Observation(
                step_id=step.step_id,
                task_id=actual_task_id,
                skill_name=step.skill_name,
                tool_name=tool_name,
                success=agent_res.success,
                output=agent_res.output,
                error=agent_res.error,
                is_untrusted=is_untrusted_output,
                source_urls=source_urls_tuple,
                execution_time_ms=step_duration_ms,
                timestamp=time.time(),
                metadata=dict(agent_res.metadata),
            )
            current_trace = current_trace.add_observation(obs)

            # 9. Handle Success or Failure
            if agent_res.success:
                current_plan = current_plan.with_step_update(
                    step_id=step.step_id,
                    status=StepStatus.SUCCEEDED,
                    result=obs,
                )
                self._persist_state(actual_task_id, current_plan, current_trace, TaskStatus.RUNNING)
            else:
                # Failure branch: check if recoverable
                if not is_recoverable_failure(agent_res):
                    logger.warning("Step '%s' encountered non-recoverable security error: %s", step.step_id, agent_res.error)
                    current_plan = current_plan.with_step_update(
                        step_id=step.step_id,
                        status=StepStatus.FAILED,
                        result=obs,
                    )
                    self._persist_state(actual_task_id, current_plan, current_trace, TaskStatus.FAILED, agent_res.error)
                    return AutonomousAgentResult(
                        success=False,
                        task_id=actual_task_id,
                        plan=current_plan,
                        trace=current_trace,
                        error=agent_res.error or "Step failed non-recoverable check",
                    )

                # Check retry bounds
                if step.retry_count < step.max_retries and step.retry_count < self.config.max_retries_per_step:
                    logger.info("Retrying step '%s' (attempt %d/%d)", step.step_id, step.retry_count + 1, step.max_retries)
                    current_plan = current_plan.with_step_update(
                        step_id=step.step_id,
                        status=StepStatus.READY,
                        result=obs,
                        retry_count=step.retry_count + 1,
                    )
                    self._persist_state(actual_task_id, current_plan, current_trace, TaskStatus.RUNNING)
                else:
                    # Retries exhausted: check replan bounds
                    if len(current_trace.replan_history) < self.config.max_replan_depth:
                        logger.info("Retries exhausted for step '%s'. Triggering autonomous replanning.", step.step_id)
                        replan_res = self._attempt_replan(
                            task=task_desc,
                            failed_step=step,
                            error_message=agent_res.error or "Step failed",
                            current_plan=current_plan,
                            trace=current_trace,
                        )
                        if replan_res is not None:
                            new_plan, new_trace = replan_res
                            current_plan = new_plan
                            current_trace = new_trace
                            self._persist_state(actual_task_id, current_plan, current_trace, TaskStatus.RUNNING)
                            continue

                    # Cannot replan or replan failed
                    current_plan = current_plan.with_step_update(
                        step_id=step.step_id,
                        status=StepStatus.FAILED,
                        result=obs,
                    )
                    self._persist_state(actual_task_id, current_plan, current_trace, TaskStatus.FAILED, agent_res.error)
                    return AutonomousAgentResult(
                        success=False,
                        task_id=actual_task_id,
                        plan=current_plan,
                        trace=current_trace,
                        error=f"Step '{step.step_id}' failed: {agent_res.error}",
                    )

        # Loop limit exceeded
        err_msg = f"Autonomous loop exceeded max_execution_iterations ({self.config.max_execution_iterations})."
        current_plan = AgentPlan(
            plan_id=current_plan.plan_id,
            task_goal=current_plan.task_goal,
            steps=current_plan.steps,
            status=StepStatus.FAILED,
        )
        self._persist_state(actual_task_id, current_plan, current_trace, TaskStatus.FAILED, err_msg)
        return AutonomousAgentResult(
            success=False,
            task_id=actual_task_id,
            plan=current_plan,
            trace=current_trace,
            error=err_msg,
        )

    def resume(
        self,
        task_id: str,
        approved_requests: list[str] | None = None,
    ) -> AutonomousAgentResult:
        """Resume execution of a paused autonomous agent task from a checkpoint."""
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string.")

        actual_task_id = task_id.strip()
        if self.state_store is None or not self.state_store.exists(actual_task_id):
            raise KeyError(f"Task '{actual_task_id}' not found in state store.")

        task_state = self.state_store.get(actual_task_id)

        # Reconstruct plan and trace
        plan: AgentPlan
        if isinstance(task_state.plan, AgentPlan):
            plan = task_state.plan
        elif "agent_plan_json" in task_state.metadata:
            plan = deserialize_agent_plan(task_state.metadata["agent_plan_json"])
        else:
            raise ValueError(f"No valid AgentPlan found for task '{actual_task_id}'.")

        trace: ExecutionTrace
        if "execution_trace_json" in task_state.metadata:
            trace = deserialize_execution_trace(task_state.metadata["execution_trace_json"])
        else:
            trace = ExecutionTrace(task_id=actual_task_id, plan_id=plan.plan_id)

        # If approvals were granted, unpause PAUSED steps
        new_steps = []
        for s in plan.steps:
            if s.status == StepStatus.PAUSED:
                # If approval gateway confirms approval or approved_requests contains step_id/approval_id
                unpause = False
                if approved_requests:
                    if s.step_id in approved_requests or task_state.metadata.get("approval_id") in approved_requests:
                        unpause = True
                if self.approval_gateway is not None:
                    dec = self.approval_gateway.evaluate_step(s, plan, actual_task_id)
                    if dec.is_allowed:
                        unpause = True

                if unpause:
                    new_steps.append(
                        AgentPlanStep(
                            step_id=s.step_id,
                            skill_name=s.skill_name,
                            objective=s.objective,
                            input_data=s.input_data,
                            dependencies=s.dependencies,
                            task_requirements=s.task_requirements,
                            status=StepStatus.READY,
                            retry_count=s.retry_count,
                            max_retries=s.max_retries,
                            result=s.result,
                            metadata=dict(s.metadata),
                        )
                    )
                else:
                    new_steps.append(s)
            else:
                new_steps.append(s)

        resumed_plan = AgentPlan(
            plan_id=plan.plan_id,
            task_goal=plan.task_goal,
            steps=tuple(new_steps),
            status=StepStatus.RUNNING,
            created_at=plan.created_at,
            updated_at=time.time(),
            metadata=dict(plan.metadata),
        )

        return self.execute_plan(
            plan=resumed_plan,
            task_id=actual_task_id,
            task_description=plan.task_goal,
            trace=trace,
        )

    def _attempt_replan(
        self,
        task: str,
        failed_step: AgentPlanStep,
        error_message: str,
        current_plan: AgentPlan,
        trace: ExecutionTrace,
    ) -> tuple[AgentPlan, ExecutionTrace] | None:
        """Attempt to autonomously replan remaining steps after recoverable failure."""
        completed_step_ids = tuple(s.step_id for s in current_plan.steps if s.status == StepStatus.SUCCEEDED)
        step_outputs: dict[str, Any] = {}
        for s in current_plan.steps:
            if s.status == StepStatus.SUCCEEDED and s.result is not None:
                step_outputs[s.step_id] = s.result.output

        replan_ctx = ReplanContext(
            task=task,
            failed_step_id=failed_step.step_id,
            error_message=error_message,
            completed_steps=completed_step_ids,
            step_outputs=step_outputs,
            original_plan_id=current_plan.plan_id,
        )

        try:
            replacement_plan = self.planner.replan_agent(replan_ctx)
        except Exception as e:
            logger.warning("Autonomous replan failed: %s", e)
            return None

        # Build merged plan: keep completed steps, append new replacement steps
        merged_steps: list[AgentPlanStep] = []
        for s in current_plan.steps:
            if s.status == StepStatus.SUCCEEDED:
                merged_steps.append(s)

        for s in replacement_plan.steps:
            if s.step_id not in completed_step_ids:
                merged_steps.append(s)

        new_plan = AgentPlan(
            plan_id=str(uuid4()),
            task_goal=current_plan.task_goal,
            steps=tuple(merged_steps),
            status=StepStatus.RUNNING,
            created_at=current_plan.created_at,
            updated_at=time.time(),
            metadata={
                "replanned": True,
                "original_plan_id": current_plan.plan_id,
                "replan_depth": len(trace.replan_history) + 1,
            },
        )

        new_trace = trace.add_replan({
            "failed_step_id": failed_step.step_id,
            "error_message": error_message,
            "new_plan_id": new_plan.plan_id,
            "timestamp": time.time(),
        })

        return new_plan, new_trace

    def _get_final_output(self, plan: AgentPlan, trace: ExecutionTrace) -> Any:
        """Extract final output from completed steps preserving TaintedValue."""
        if not plan.steps:
            return None
        last_step = plan.steps[-1]
        if last_step.result is not None:
            return last_step.result.output
        for s in reversed(plan.steps):
            if s.result is not None:
                return s.result.output
        return None

    def _persist_state(
        self,
        task_id: str,
        plan: AgentPlan,
        trace: ExecutionTrace,
        status: TaskStatus,
        error: str | None = None,
        final_output: Any = None,
    ) -> None:
        """Persist state to TaskStateStore if configured."""
        if self.state_store is None:
            return

        g_id = plan.metadata.get("goal_id") if isinstance(plan.metadata, dict) else None
        p_g_id = plan.metadata.get("parent_goal_id") if isinstance(plan.metadata, dict) else None
        state = TaskState(
            task_id=task_id,
            plan_id=plan.plan_id,
            status=status,
            plan=plan,
            error=error,
            final_output=final_output,
            goal_id=str(g_id).strip() if g_id else None,
            parent_goal_id=str(p_g_id).strip() if p_g_id else None,
            metadata={
                "agent_plan_json": serialize_agent_plan(plan),
                "execution_trace_json": serialize_execution_trace(trace),
            },
        )
        for s in plan.steps:
            state.step_states[s.step_id] = StepState(
                step_id=s.step_id,
                status=(
                    LegacyStepStatus.COMPLETED
                    if s.status == StepStatus.SUCCEEDED
                    else LegacyStepStatus.FAILED
                    if s.status == StepStatus.FAILED
                    else LegacyStepStatus.RUNNING
                    if s.status == StepStatus.RUNNING
                    else LegacyStepStatus.SKIPPED
                    if s.status == StepStatus.SKIPPED
                    else LegacyStepStatus.NOT_STARTED
                ),
                output=s.result.output if s.result else None,
                error=s.result.error if s.result else None,
            )

        self.state_store.save(state)
