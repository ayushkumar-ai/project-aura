import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from core.agent_runtime import AgentRequest, AgentResult, AgentRuntime
from core.task_planner import ExecutionPlan, PlanStep, TaskPlanner

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
    metadata: dict[str, Any] = field(default_factory=dict)


class WorkflowExecutor:
    """Executes multi-step ExecutionPlans using the AgentRuntime."""

    def __init__(
        self,
        runtime: AgentRuntime,
        planner: TaskPlanner | None = None,
    ):
        if not isinstance(runtime, AgentRuntime):
            raise TypeError("runtime must be an instance of AgentRuntime.")

        if planner is not None and not isinstance(planner, TaskPlanner):
            raise TypeError("planner must be an instance of TaskPlanner or None.")

        self.runtime = runtime
        self.planner = planner if planner is not None else TaskPlanner(runtime.skill_registry)

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
        timeout: float | None = None,
    ) -> WorkflowResult:
        """Execute an ExecutionPlan in dependency order."""
        if not isinstance(plan, ExecutionPlan):
            raise TypeError("plan must be an instance of ExecutionPlan.")

        # Validate plan before execution
        self.planner.validate_plan(plan)

        ordered_steps = self.planner.get_execution_order(plan)

        step_results: dict[str, AgentResult] = {}
        executed_steps: list[str] = []
        last_output: Any = None

        for step in ordered_steps:
            # Check that all prerequisites completed successfully
            for dep in step.dependencies:
                dep_res = step_results.get(dep)
                if dep_res is None or not dep_res.success:
                    logger.warning(
                        "Prerequisite step '%s' failed or not run for step '%s'",
                        dep,
                        step.step_id,
                    )
                    return WorkflowResult(
                        success=False,
                        plan_id=plan.plan_id,
                        step_results=step_results,
                        executed_steps=executed_steps,
                        failed_step_id=step.step_id,
                        error=f"Prerequisite step '{dep}' failed for step '{step.step_id}'.",
                    )

            # Resolve input
            resolved_input = self._resolve_step_input(step, step_results)

            # Prepare metadata
            meta = dict(step.metadata)
            meta["workflow_plan_id"] = plan.plan_id
            meta["step_id"] = step.step_id

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
                return WorkflowResult(
                    success=False,
                    plan_id=plan.plan_id,
                    step_results=step_results,
                    executed_steps=executed_steps,
                    failed_step_id=step.step_id,
                    error=agent_res.error,
                )

            executed_steps.append(step.step_id)
            last_output = agent_res.output

        return WorkflowResult(
            success=True,
            plan_id=plan.plan_id,
            step_results=step_results,
            executed_steps=executed_steps,
            final_output=last_output,
        )

    def run(
        self,
        plan: ExecutionPlan,
        timeout: float | None = None,
    ) -> WorkflowResult:
        """Alias for execute."""
        return self.execute(plan, timeout=timeout)
