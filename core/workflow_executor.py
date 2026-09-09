import logging
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from core.agent_runtime import AgentRequest, AgentResult, AgentRuntime
from core.approval import ApprovalDecisionType, ApprovalGateway, ApprovalRequest
from core.provenance import TaintedValue, is_tainted, unwrap_tainted, wrap_tainted, render_for_prompt
from core.task_planner import ExecutionPlan, PlanStep, ReplanContext, TaskPlanner
from core.task_state import StepState, StepStatus, TaskState, TaskStatus
from core.task_state_store import TaskStateStore

logger = logging.getLogger("aura.workflow_executor")


def is_recoverable_failure(agent_result: AgentResult | None = None, error: str | None = None) -> bool:
    """Check whether an execution failure is potentially recoverable via adaptive re-planning."""
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
    ]
    for marker in non_recoverable_markers:
        if marker in err_lower:
            return False

    return True


@dataclass(frozen=True)
class WorkflowResult:
    """Represents the final outcome of an executed multi-step workflow."""

    success: bool
    plan_id: str
    step_results: dict[str, AgentResult] = field(default_factory=dict)
    executed_steps: list[str] = field(default_factory=list)
    failed_step_id: str | None = None
    final_output: Any = None
    error: str | None = None
    task_id: str | None = None
    approval_request: ApprovalRequest | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class WorkflowExecutor:
    """Executes multi-step ExecutionPlans using AgentRuntime, optional TaskStateStore, and ApprovalGateway."""

    def __init__(
        self,
        runtime: AgentRuntime,
        planner: TaskPlanner | None = None,
        state_store: TaskStateStore | None = None,
        approval_gateway: ApprovalGateway | None = None,
        max_replans: int = 0,
        task_description: str | None = None,
    ):
        if not isinstance(runtime, AgentRuntime):
            raise TypeError("runtime must be an instance of AgentRuntime.")

        if planner is not None and not isinstance(planner, TaskPlanner):
            raise TypeError("planner must be an instance of TaskPlanner or None.")

        if state_store is not None and not isinstance(state_store, TaskStateStore):
            raise TypeError("state_store must be an instance of TaskStateStore or None.")

        if approval_gateway is not None and not isinstance(approval_gateway, ApprovalGateway):
            raise TypeError("approval_gateway must be an instance of ApprovalGateway or None.")

        if not isinstance(max_replans, int) or max_replans < 0:
            raise ValueError("max_replans must be a non-negative integer.")

        self.runtime = runtime
        self.planner = planner if planner is not None else TaskPlanner(runtime.skill_registry)
        self.state_store = state_store
        self.approval_gateway = approval_gateway
        self.max_replans = max_replans
        self.task_description = task_description

    def _resolve_step_input(
        self,
        step: PlanStep,
        step_results: dict[str, AgentResult],
    ) -> Any:
        """Resolve the input data for a step, preserving provenance and taint envelopes across dependencies."""
        input_data = step.input_data

        if callable(input_data):
            return input_data(step_results)

        def _resolve_nested(val: Any) -> Any:
            if isinstance(val, dict):
                if "$from_step" in val and len(val) == 1:
                    source_id = str(val["$from_step"]).strip()
                    if source_id in step_results:
                        return step_results[source_id].output
                return {k: _resolve_nested(v) for k, v in val.items()}
            elif isinstance(val, list):
                return [_resolve_nested(item) for item in val]
            elif isinstance(val, tuple):
                return tuple(_resolve_nested(item) for item in val)
            return val

        resolved = _resolve_nested(input_data)

        if isinstance(resolved, dict):
            if "$from_step" in resolved and len(resolved) == 1:
                source_id = str(resolved["$from_step"]).strip()
                if source_id in step_results:
                    return step_results[source_id].output
            if not resolved and step.dependencies:
                last_dep = step.dependencies[-1]
                if last_dep in step_results:
                    return step_results[last_dep].output
            return resolved

        if (resolved is None or resolved == "") and step.dependencies:
            last_dep = step.dependencies[-1]
            if last_dep in step_results:
                return step_results[last_dep].output

        return resolved

    def execute(
        self,
        plan: ExecutionPlan,
        task_id: str | None = None,
        timeout: float | None = None,
    ) -> WorkflowResult:
        """Execute an ExecutionPlan in dependency order with state persistence, approval gating, and adaptive re-planning."""
        if not isinstance(plan, ExecutionPlan):
            raise TypeError("plan must be an instance of ExecutionPlan.")

        # Validate plan before execution
        self.planner.validate_plan(plan)

        current_plan = plan
        ordered_steps = self.planner.get_execution_order(current_plan)

        task_state: TaskState | None = None
        actual_task_id = task_id

        # 1. Initialize or load task state if state_store is configured
        if self.state_store is not None:
            if actual_task_id is not None and self.state_store.exists(actual_task_id):
                task_state = self.state_store.get(actual_task_id)
                # If plan ID mismatch and not replanned, validate
                if task_state.plan_id != current_plan.plan_id and not task_state.metadata.get("replan_count"):
                    raise ValueError(
                        f"Plan ID mismatch: task '{actual_task_id}' has plan '{task_state.plan_id}', "
                        f"but got '{current_plan.plan_id}'."
                    )

                # If already completed, return cached result
                if task_state.is_completed():
                    step_res = {
                        s_id: st.agent_result
                        for s_id, st in task_state.step_states.items()
                        if st.agent_result is not None
                    }
                    exec_steps = [
                        s_id
                        for s_id, st in task_state.step_states.items()
                        if st.status == StepStatus.COMPLETED
                    ]
                    return WorkflowResult(
                        success=True,
                        plan_id=current_plan.plan_id,
                        step_results=step_res,
                        executed_steps=exec_steps,
                        final_output=task_state.final_output,
                        task_id=actual_task_id,
                    )

                # Reset any interrupted RUNNING step back to NOT_STARTED for conservative retry
                for st in task_state.step_states.values():
                    if st.status == StepStatus.RUNNING:
                        st.status = StepStatus.NOT_STARTED

                task_state.status = TaskStatus.RUNNING
                self.state_store.save(task_state)
            else:
                actual_task_id = actual_task_id or str(uuid4())
                task_state = self.state_store.create(
                    task_id=actual_task_id,
                    plan_id=current_plan.plan_id,
                    plan=current_plan,
                )
                task_state.status = TaskStatus.RUNNING
                for step in ordered_steps:
                    task_state.step_states[step.step_id] = StepState(
                        step_id=step.step_id,
                        status=StepStatus.NOT_STARTED,
                    )
                self.state_store.save(task_state)

        step_results: dict[str, AgentResult] = {}
        executed_steps: list[str] = []
        last_output: Any = None

        # Populate pre-existing completed step results if resuming
        if task_state is not None:
            for s_id, st in task_state.step_states.items():
                if st.status == StepStatus.COMPLETED and st.agent_result is not None:
                    step_results[s_id] = st.agent_result

        step_index = 0
        while step_index < len(ordered_steps):
            step = ordered_steps[step_index]

            # Check if this step is already completed
            if task_state is not None:
                st = task_state.step_states.get(step.step_id)
                if st is not None and st.status == StepStatus.COMPLETED:
                    if step.step_id not in executed_steps:
                        executed_steps.append(step.step_id)
                    if st.output is not None:
                        last_output = st.output
                    step_index += 1
                    continue

            # Check that all prerequisites completed successfully
            for dep in step.dependencies:
                dep_res = step_results.get(dep)
                if dep_res is None or not dep_res.success:
                    logger.warning(
                        "Prerequisite step '%s' failed or not run for step '%s'",
                        dep,
                        step.step_id,
                    )
                    if task_state is not None:
                        task_state.step_states[step.step_id].status = StepStatus.SKIPPED
                        for remaining_step in ordered_steps:
                            rem_st = task_state.step_states.get(remaining_step.step_id)
                            if rem_st and rem_st.status == StepStatus.NOT_STARTED:
                                rem_st.status = StepStatus.SKIPPED
                        task_state.status = TaskStatus.FAILED
                        task_state.failed_step_id = step.step_id
                        task_state.error = f"Prerequisite step '{dep}' failed for step '{step.step_id}'."
                        self.state_store.save(task_state)

                    return WorkflowResult(
                        success=False,
                        plan_id=current_plan.plan_id,
                        step_results=step_results,
                        executed_steps=executed_steps,
                        failed_step_id=step.step_id,
                        error=f"Prerequisite step '{dep}' failed for step '{step.step_id}'.",
                        task_id=actual_task_id,
                    )

            # Approval Gateway Evaluation
            if self.approval_gateway is not None:
                eval_task_id = actual_task_id or "ephemeral_task"
                decision = self.approval_gateway.evaluate_step(step, current_plan, eval_task_id)

                if decision.is_denied:
                    logger.warning(
                        "Step '%s' denied by approval gateway: %s",
                        step.step_id,
                        decision.reason,
                    )
                    if task_state is not None:
                        task_state.step_states[step.step_id].status = StepStatus.FAILED
                        task_state.step_states[step.step_id].error = decision.reason
                        for remaining_step in ordered_steps:
                            if remaining_step.step_id != step.step_id:
                                rem_st = task_state.step_states.get(remaining_step.step_id)
                                if rem_st and rem_st.status == StepStatus.NOT_STARTED:
                                    rem_st.status = StepStatus.SKIPPED
                        task_state.status = TaskStatus.FAILED
                        task_state.failed_step_id = step.step_id
                        task_state.error = decision.reason
                        self.state_store.save(task_state)

                    return WorkflowResult(
                        success=False,
                        plan_id=current_plan.plan_id,
                        step_results=step_results,
                        executed_steps=executed_steps,
                        failed_step_id=step.step_id,
                        error=decision.reason,
                        task_id=actual_task_id,
                        approval_request=decision.approval_request,
                        metadata={"denied": True},
                    )

                elif decision.requires_approval:
                    logger.info(
                        "Step '%s' requires approval: %s",
                        step.step_id,
                        decision.reason,
                    )
                    if task_state is not None:
                        task_state.status = TaskStatus.PAUSED
                        task_state.metadata["approval_required"] = True
                        if decision.approval_request is not None:
                            task_state.metadata["approval_id"] = decision.approval_request.approval_id
                        self.state_store.save(task_state)

                    return WorkflowResult(
                        success=False,
                        plan_id=current_plan.plan_id,
                        step_results=step_results,
                        executed_steps=executed_steps,
                        failed_step_id=step.step_id,
                        error=f"Step '{step.step_id}' requires approval: {decision.reason}",
                        task_id=actual_task_id,
                        approval_request=decision.approval_request,
                        metadata={
                            "approval_required": True,
                            "approval_id": decision.approval_request.approval_id if decision.approval_request else None,
                        },
                    )

            # Resolve input
            resolved_input = self._resolve_step_input(step, step_results)

            # Update step state to RUNNING
            if task_state is not None:
                task_state.step_states[step.step_id].status = StepStatus.RUNNING
                task_state.step_states[step.step_id].started_at = time.time()
                self.state_store.save(task_state)

            # Prepare metadata
            meta = dict(step.metadata)
            meta["workflow_plan_id"] = current_plan.plan_id
            meta["step_id"] = step.step_id
            if actual_task_id:
                meta["task_id"] = actual_task_id

            agent_req = AgentRequest(
                skill_name=step.skill_name,
                input_data=resolved_input,
                task_requirements=step.task_requirements,
                metadata=meta,
            )

            # Execute via AgentRuntime
            agent_res = self.runtime.execute(agent_req, timeout=timeout)

            # Preserve & tag provenance for research / untrusted skill outputs
            if agent_res.success and agent_res.output is not None:
                is_untrusted_skill = (
                    step.skill_name in ("research_web", "web_search", "fetch_url")
                    or step.metadata.get("untrusted_source") is True
                    or (self.runtime.skill_registry.has(step.skill_name) and self.runtime.skill_registry.get(step.skill_name).metadata.get("capability") == "web_research")
                )
                effective_output = agent_res.output
                if is_untrusted_skill and not isinstance(effective_output, TaintedValue):
                    source_urls = []
                    if isinstance(effective_output, dict):
                        for src in effective_output.get("sources", []):
                            if isinstance(src, dict) and "url" in src:
                                source_urls.append(src["url"])
                    elif isinstance(effective_output, str):
                        found_urls = re.findall(r"https?://[^\s\)\>\]]+", effective_output)
                        source_urls.extend(found_urls[:10])

                    effective_output = wrap_tainted(
                        value=effective_output,
                        is_untrusted=True,
                        source_type="external_web",
                        originating_step_id=step.step_id,
                        source_urls=source_urls,
                        metadata={"skill_name": step.skill_name},
                    )
                elif isinstance(effective_output, TaintedValue) and effective_output.originating_step_id is None:
                    effective_output = wrap_tainted(
                        value=effective_output,
                        originating_step_id=step.step_id,
                    )

                if effective_output is not agent_res.output:
                    agent_res = AgentResult(
                        success=agent_res.success,
                        skill_name=agent_res.skill_name,
                        output=effective_output,
                        selected_model_id=agent_res.selected_model_id,
                        selected_provider_id=agent_res.selected_provider_id,
                        error=agent_res.error,
                        request_id=agent_res.request_id,
                        metadata=dict(agent_res.metadata),
                    )

            step_results[step.step_id] = agent_res

            if not agent_res.success:
                logger.warning(
                    "Step '%s' failed in workflow '%s': %s",
                    step.step_id,
                    current_plan.plan_id,
                    agent_res.error,
                )

                # Check if adaptive re-planning is enabled and recoverable
                replan_count = task_state.metadata.get("replan_count", 0) if task_state is not None else 0
                can_replan = (
                    self.max_replans > 0
                    and replan_count < self.max_replans
                    and is_recoverable_failure(agent_res, agent_res.error)
                )

                if can_replan:
                    logger.info(
                        "Triggering adaptive re-planning for failed step '%s' (attempt %d/%d)",
                        step.step_id,
                        replan_count + 1,
                        self.max_replans,
                    )
                    task_goal = (
                        self.task_description
                        or current_plan.metadata.get("task")
                        or f"Task {actual_task_id or current_plan.plan_id}"
                    )
                    replan_ctx = ReplanContext(
                        task=task_goal,
                        failed_step_id=step.step_id,
                        error_message=agent_res.error or "Step execution failed",
                        completed_steps=tuple(executed_steps),
                        step_outputs={
                            s_id: step_results[s_id].output
                            for s_id in executed_steps
                            if s_id in step_results and step_results[s_id].output is not None
                        },
                        original_plan_id=current_plan.plan_id,
                    )

                    try:
                        new_plan = self.planner.replan(replan_ctx)
                        logger.info(
                            "Generated replacement plan '%s' with %d steps",
                            new_plan.plan_id,
                            len(new_plan.steps),
                        )

                        # Update TaskState
                        if task_state is not None:
                            task_state.plan_id = new_plan.plan_id
                            task_state.plan = new_plan
                            task_state.metadata["replan_count"] = replan_count + 1
                            # Register new step states while keeping completed ones
                            for s in new_plan.steps:
                                if s.step_id not in task_state.step_states:
                                    task_state.step_states[s.step_id] = StepState(
                                        step_id=s.step_id,
                                        status=StepStatus.NOT_STARTED,
                                    )
                            self.state_store.save(task_state)

                        # Restart loop over new plan
                        current_plan = new_plan
                        ordered_steps = self.planner.get_execution_order(current_plan)
                        step_index = 0
                        continue
                    except Exception as replan_err:
                        logger.warning("Adaptive re-planning failed: %s", replan_err)
                        fail_msg = f"Step '{step.step_id}' failed: {agent_res.error}. Re-planning failed: {str(replan_err)}"
                        if task_state is not None:
                            task_state.step_states[step.step_id].status = StepStatus.FAILED
                            task_state.step_states[step.step_id].agent_result = agent_res
                            task_state.step_states[step.step_id].error = agent_res.error
                            task_state.step_states[step.step_id].completed_at = time.time()
                            for remaining_step in ordered_steps:
                                if remaining_step.step_id != step.step_id:
                                    rem_st = task_state.step_states.get(remaining_step.step_id)
                                    if rem_st and rem_st.status == StepStatus.NOT_STARTED:
                                        rem_st.status = StepStatus.SKIPPED
                            task_state.status = TaskStatus.FAILED
                            task_state.failed_step_id = step.step_id
                            task_state.error = fail_msg
                            self.state_store.save(task_state)

                        return WorkflowResult(
                            success=False,
                            plan_id=current_plan.plan_id,
                            step_results=step_results,
                            executed_steps=executed_steps,
                            failed_step_id=step.step_id,
                            error=fail_msg,
                            task_id=actual_task_id,
                            metadata={"replan_failed": True},
                        )

                # Re-planning not enabled or not possible
                if task_state is not None:
                    task_state.step_states[step.step_id].status = StepStatus.FAILED
                    task_state.step_states[step.step_id].agent_result = agent_res
                    task_state.step_states[step.step_id].error = agent_res.error
                    task_state.step_states[step.step_id].completed_at = time.time()
                    for remaining_step in ordered_steps:
                        if remaining_step.step_id != step.step_id:
                            rem_st = task_state.step_states.get(remaining_step.step_id)
                            if rem_st and rem_st.status == StepStatus.NOT_STARTED:
                                rem_st.status = StepStatus.SKIPPED
                    task_state.status = TaskStatus.FAILED
                    task_state.failed_step_id = step.step_id
                    task_state.error = agent_res.error
                    self.state_store.save(task_state)

                return WorkflowResult(
                    success=False,
                    plan_id=current_plan.plan_id,
                    step_results=step_results,
                    executed_steps=executed_steps,
                    failed_step_id=step.step_id,
                    error=agent_res.error,
                    task_id=actual_task_id,
                )

            # Step completed successfully
            if task_state is not None:
                task_state.step_states[step.step_id].status = StepStatus.COMPLETED
                task_state.step_states[step.step_id].agent_result = agent_res
                task_state.step_states[step.step_id].output = agent_res.output
                task_state.step_states[step.step_id].completed_at = time.time()
                self.state_store.save(task_state)

            if step.step_id not in executed_steps:
                executed_steps.append(step.step_id)
            last_output = agent_res.output
            step_index += 1

        if task_state is not None:
            task_state.status = TaskStatus.COMPLETED
            task_state.final_output = last_output
            self.state_store.save(task_state)

        return WorkflowResult(
            success=True,
            plan_id=current_plan.plan_id,
            step_results=step_results,
            executed_steps=executed_steps,
            final_output=last_output,
            task_id=actual_task_id,
            metadata={"replan_count": task_state.metadata.get("replan_count", 0)} if task_state is not None else {},
        )

    def resume(
        self,
        task_id: str,
        plan: ExecutionPlan | None = None,
        timeout: float | None = None,
    ) -> WorkflowResult:
        """Resume an interrupted or failed task using its persisted state."""
        if self.state_store is None:
            raise ValueError("TaskStateStore is required to resume a task.")

        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string.")

        task_state = self.state_store.get(task_id)

        target_plan = plan
        if target_plan is None:
            if task_state.plan is None:
                raise ValueError("ExecutionPlan must be provided when not stored in TaskState.")
            target_plan = task_state.plan

        return self.execute(plan=target_plan, task_id=task_id, timeout=timeout)

    def run(
        self,
        plan: ExecutionPlan,
        task_id: str | None = None,
        timeout: float | None = None,
    ) -> WorkflowResult:
        """Alias for execute."""
        return self.execute(plan, task_id=task_id, timeout=timeout)
