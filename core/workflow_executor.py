import logging
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from core.agent_runtime import AgentRequest, AgentResult, AgentRuntime
from core.approval import ApprovalDecisionType, ApprovalGateway, ApprovalRequest
from core.task_planner import ExecutionPlan, PlanStep, TaskPlanner
from core.task_state import StepState, StepStatus, TaskState, TaskStatus
from core.task_state_store import TaskStateStore

logger = logging.getLogger("aura.workflow_executor")


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
    ):
        if not isinstance(runtime, AgentRuntime):
            raise TypeError("runtime must be an instance of AgentRuntime.")

        if planner is not None and not isinstance(planner, TaskPlanner):
            raise TypeError("planner must be an instance of TaskPlanner or None.")

        if state_store is not None and not isinstance(state_store, TaskStateStore):
            raise TypeError("state_store must be an instance of TaskStateStore or None.")

        if approval_gateway is not None and not isinstance(approval_gateway, ApprovalGateway):
            raise TypeError("approval_gateway must be an instance of ApprovalGateway or None.")

        self.runtime = runtime
        self.planner = planner if planner is not None else TaskPlanner(runtime.skill_registry)
        self.state_store = state_store
        self.approval_gateway = approval_gateway

    def _resolve_step_input(
        self,
        step: PlanStep,
        step_results: dict[str, AgentResult],
    ) -> Any:
        """Resolve the input data for a step, allowing outputs from prior steps to be consumed."""
        input_data = step.input_data

        if callable(input_data):
            return input_data(step_results)

        if isinstance(input_data, dict):
            if "$from_step" in input_data:
                source_id = input_data["$from_step"]
                if source_id in step_results:
                    return step_results[source_id].output
            if not input_data and step.dependencies:
                last_dep = step.dependencies[-1]
                if last_dep in step_results:
                    return step_results[last_dep].output
            return input_data

        if (input_data is None or input_data == "") and step.dependencies:
            last_dep = step.dependencies[-1]
            if last_dep in step_results:
                return step_results[last_dep].output

        return input_data

    def execute(
        self,
        plan: ExecutionPlan,
        task_id: str | None = None,
        timeout: float | None = None,
    ) -> WorkflowResult:
        """Execute an ExecutionPlan in dependency order with optional state persistence and approval gating."""
        if not isinstance(plan, ExecutionPlan):
            raise TypeError("plan must be an instance of ExecutionPlan.")

        # Validate plan before execution
        self.planner.validate_plan(plan)

        ordered_steps = self.planner.get_execution_order(plan)

        task_state: TaskState | None = None
        actual_task_id = task_id

        # 1. Initialize or load task state if state_store is configured
        if self.state_store is not None:
            if actual_task_id is not None and self.state_store.exists(actual_task_id):
                task_state = self.state_store.get(actual_task_id)
                if task_state.plan_id != plan.plan_id:
                    raise ValueError(
                        f"Plan ID mismatch: task '{actual_task_id}' has plan '{task_state.plan_id}', "
                        f"but got '{plan.plan_id}'."
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
                        plan_id=plan.plan_id,
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
                    plan_id=plan.plan_id,
                    plan=plan,
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

        for step in ordered_steps:
            # Check if this step is already completed
            if task_state is not None:
                st = task_state.step_states.get(step.step_id)
                if st is not None and st.status == StepStatus.COMPLETED:
                    executed_steps.append(step.step_id)
                    if st.output is not None:
                        last_output = st.output
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
                        plan_id=plan.plan_id,
                        step_results=step_results,
                        executed_steps=executed_steps,
                        failed_step_id=step.step_id,
                        error=f"Prerequisite step '{dep}' failed for step '{step.step_id}'.",
                        task_id=actual_task_id,
                    )

            # Approval Gateway Evaluation
            if self.approval_gateway is not None:
                eval_task_id = actual_task_id or "ephemeral_task"
                decision = self.approval_gateway.evaluate_step(step, plan, eval_task_id)

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
                        plan_id=plan.plan_id,
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
                        plan_id=plan.plan_id,
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
            meta["workflow_plan_id"] = plan.plan_id
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
            step_results[step.step_id] = agent_res

            if not agent_res.success:
                logger.warning(
                    "Step '%s' failed in workflow '%s': %s",
                    step.step_id,
                    plan.plan_id,
                    agent_res.error,
                )
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
                    plan_id=plan.plan_id,
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

            executed_steps.append(step.step_id)
            last_output = agent_res.output

        if task_state is not None:
            task_state.status = TaskStatus.COMPLETED
            task_state.final_output = last_output
            self.state_store.save(task_state)

        return WorkflowResult(
            success=True,
            plan_id=plan.plan_id,
            step_results=step_results,
            executed_steps=executed_steps,
            final_output=last_output,
            task_id=actual_task_id,
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
