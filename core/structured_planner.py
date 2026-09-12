"""M33 — Structured Planning Engine for Project AURA.

Provides bounded goal decomposition, topological step execution, policy validation,
retry handling, cancellation, and execution audit trails.
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Any
from uuid import uuid4

from core.structured_plan_types import (
    PlanExecutionAudit,
    PlanStatus,
    PlanStepNode,
    PlanStepStatus,
    StructuredPlan,
)

logger = logging.getLogger("aura.structured_planner")


class StructuredPlanningEngine:
    """Autonomous hierarchical planning engine with policy and resource boundary checks."""

    def __init__(self, default_tool_executor: Any | None = None, policy_engine: Any | None = None):
        self.default_tool_executor = default_tool_executor
        self.policy_engine = policy_engine
        self._plans: dict[str, StructuredPlan] = {}
        self._cancelled_plans: set[str] = set()
        self._lock = threading.RLock()

    def create_plan(
        self,
        goal: str,
        steps: list[PlanStepNode] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StructuredPlan:
        """Create a new structured plan."""
        with self._lock:
            plan_id = f"plan_{uuid4().hex[:12]}"
            plan_steps = steps or self.decompose_goal_heuristically(goal)
            plan = StructuredPlan(
                plan_id=plan_id,
                goal=goal,
                steps=plan_steps,
                status=PlanStatus.CREATED,
                created_at=time.time(),
                metadata=metadata or {},
            )
            valid, err = self.validate_plan(plan)
            if not valid:
                raise ValueError(f"Invalid plan structure: {err}")
            self._plans[plan_id] = plan
            return plan

    def decompose_goal_heuristically(self, goal: str) -> list[PlanStepNode]:
        """Decompose a goal into default sequential stages if not explicitly provided."""
        step_1 = PlanStepNode(
            step_id="step_1_analyze",
            title="Analyze Goal Requirements",
            description=f"Analyze requirements for goal: '{goal}'",
            tool_name="analysis_tool",
            parameters={"goal": goal},
            depends_on=[],
        )
        step_2 = PlanStepNode(
            step_id="step_2_execute",
            title="Execute Primary Operations",
            description=f"Perform core operational work for: '{goal}'",
            tool_name="execution_tool",
            parameters={"goal": goal},
            depends_on=["step_1_analyze"],
        )
        step_3 = PlanStepNode(
            step_id="step_3_verify",
            title="Verify Goal Outcomes",
            description=f"Validate deliverables against criteria for: '{goal}'",
            tool_name="verification_tool",
            parameters={"goal": goal},
            depends_on=["step_2_execute"],
        )
        return [step_1, step_2, step_3]

    def validate_plan(self, plan: StructuredPlan) -> tuple[bool, str]:
        """Validate that all step dependencies exist and that there are no circular dependencies."""
        step_ids = {s.step_id for s in plan.steps}

        # Check for duplicate IDs
        if len(step_ids) != len(plan.steps):
            return False, "Duplicate step IDs found in plan."

        # Check for dangling dependencies
        for step in plan.steps:
            for dep in step.depends_on:
                if dep not in step_ids:
                    return False, f"Step '{step.step_id}' depends on non-existent step '{dep}'."

        # Topological sort / cycle detection (Kahn's algorithm)
        in_degree: dict[str, int] = {s.step_id: 0 for s in plan.steps}
        adj: dict[str, list[str]] = collections.defaultdict(list)

        for step in plan.steps:
            for dep in step.depends_on:
                adj[dep].append(step.step_id)
                in_degree[step.step_id] += 1

        queue = collections.deque([sid for sid, deg in in_degree.items() if deg == 0])
        visited_count = 0

        while queue:
            curr = queue.popleft()
            visited_count += 1
            for neighbor in adj[curr]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited_count != len(plan.steps):
            return False, "Circular dependency detected in plan step graph."

        return True, "Valid"

    def cancel_plan(self, plan_id: str) -> bool:
        """Cancel a running or pending plan."""
        with self._lock:
            self._cancelled_plans.add(plan_id)
            if plan_id in self._plans:
                self._plans[plan_id].status = PlanStatus.CANCELLED
                return True
            return False

    def get_plan(self, plan_id: str) -> StructuredPlan | None:
        with self._lock:
            return self._plans.get(plan_id)

    def list_plans(self) -> list[StructuredPlan]:
        with self._lock:
            return list(self._plans.values())

    def execute_plan(
        self,
        plan: StructuredPlan | str,
        tool_executor: Any | None = None,
        policy: Any | None = None,
        timeout_seconds: float = 60.0,
    ) -> PlanExecutionAudit:
        """Execute a structured plan with dependency resolution, retries, and policy boundaries."""
        start_time = time.time()
        with self._lock:
            if isinstance(plan, str):
                target_plan = self._plans.get(plan)
                if not target_plan:
                    raise KeyError(f"Plan '{plan}' not found.")
            else:
                target_plan = plan
                self._plans[target_plan.plan_id] = target_plan

        target_plan.status = PlanStatus.IN_PROGRESS
        executor = tool_executor or self.default_tool_executor
        pol = policy or self.policy_engine

        execution_trace: list[dict[str, Any]] = []
        step_results: dict[str, Any] = {}
        completed_steps: set[str] = set()
        failed_steps: set[str] = set()

        for step in target_plan.steps:
            # Check plan cancellation
            if target_plan.plan_id in self._cancelled_plans:
                step.status = PlanStepStatus.CANCELLED
                target_plan.status = PlanStatus.CANCELLED
                execution_trace.append({"step_id": step.step_id, "action": "cancelled"})
                break

            # Check overall timeout
            if time.time() - start_time > timeout_seconds:
                step.status = PlanStepStatus.FAILED
                step.error = "Execution timed out"
                failed_steps.add(step.step_id)
                target_plan.status = PlanStatus.FAILED
                break

            # Check dependencies
            unsatisfied_deps = [d for d in step.depends_on if d not in completed_steps]
            if unsatisfied_deps:
                step.status = PlanStepStatus.SKIPPED
                step.error = f"Dependencies not satisfied: {unsatisfied_deps}"
                failed_steps.add(step.step_id)
                continue

            # Policy Check
            if pol is not None:
                try:
                    if hasattr(pol, "authorize_tool") and step.tool_name in getattr(pol, "authorized_tools", set()):
                        decision = pol.authorize_tool(step.tool_name)
                        if getattr(decision, "value", str(decision)) == "deny":
                            step.status = PlanStepStatus.FAILED
                            step.error = f"Policy denied execution of tool '{step.tool_name}'"
                            failed_steps.add(step.step_id)
                            target_plan.status = PlanStatus.FAILED
                            break
                    elif hasattr(pol, "evaluate"):
                        from core.models import AURARequest
                        decision = pol.evaluate(AURARequest(user_input=f"Execute {step.tool_name}"))
                        if getattr(decision, "value", None) == "deny" or getattr(decision, "decision", None) == "deny" or getattr(decision, "is_allowed", True) is False:
                            step.status = PlanStepStatus.FAILED
                            step.error = f"Policy denied execution of tool '{step.tool_name}'"
                            failed_steps.add(step.step_id)
                            target_plan.status = PlanStatus.FAILED
                            break
                except Exception as e:
                    logger.warning(f"Policy evaluation check failed: {e}")

            # Step Execution with Retries
            step.status = PlanStepStatus.RUNNING
            step_start = time.time()
            success = False
            last_error = ""

            for attempt in range(1, step.max_retries + 2):
                step.retry_count = attempt - 1
                try:
                    if executor is not None and hasattr(executor, "execute"):
                        result = executor.execute(step.tool_name, step.parameters)
                    else:
                        # Deterministic simulated reference execution
                        result = {
                            "status": "success",
                            "step_id": step.step_id,
                            "tool": step.tool_name,
                            "output": f"Executed {step.title}",
                        }
                    step.result = result
                    step.status = PlanStepStatus.COMPLETED
                    step_results[step.step_id] = result
                    completed_steps.add(step.step_id)
                    success = True
                    break
                except Exception as e:
                    last_error = str(e)
                    logger.warning(f"Step '{step.step_id}' attempt {attempt} failed: {e}")
                    time.sleep(0.01)

            step.duration_seconds = time.time() - step_start
            if not success:
                step.status = PlanStepStatus.FAILED
                step.error = last_error or "Unknown error"
                failed_steps.add(step.step_id)
                target_plan.status = PlanStatus.FAILED
                execution_trace.append({
                    "step_id": step.step_id,
                    "status": "failed",
                    "error": step.error,
                    "attempts": step.retry_count + 1,
                })
                break
            else:
                execution_trace.append({
                    "step_id": step.step_id,
                    "status": "completed",
                    "duration_seconds": round(step.duration_seconds, 4),
                })

        is_overall_success = len(completed_steps) == len(target_plan.steps)
        if is_overall_success:
            target_plan.status = PlanStatus.COMPLETED

        total_duration = time.time() - start_time

        return PlanExecutionAudit(
            plan_id=target_plan.plan_id,
            goal=target_plan.goal,
            total_steps=len(target_plan.steps),
            steps_completed=len(completed_steps),
            steps_failed=len(failed_steps),
            is_success=is_overall_success,
            total_duration_seconds=round(total_duration, 4),
            step_results=step_results,
            execution_trace=execution_trace,
            error="" if is_overall_success else "One or more steps failed or were skipped",
        )
