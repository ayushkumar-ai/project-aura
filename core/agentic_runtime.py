import logging
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from core.agent_plan import AgentPlan
from core.agent_runtime import AgentRuntime
from core.approval import ApprovalGateway
from core.autonomous_agent import AutonomousAgentExecutor, AutonomousAgentResult
from core.capability_registry import ModelCapability
from core.goal import Goal, GoalStatus
from core.goal_engine import GoalEngine
from core.goal_reasoner import GoalEvaluationResult
from core.goal_store import GoalStore, InMemoryGoalStore
from core.model_router import ModelRouter, TaskRequirements
from core.models import AURARequest, AURAResponse
from core.policy import Policy
from core.skill_registry import SkillRegistry
from core.task_planner import ExecutionPlan, PlanStep, TaskPlanner
from core.task_state import TaskState, TaskStatus
from core.task_state_store import TaskStateStore
from core.workflow_executor import WorkflowExecutor, WorkflowResult
from interfaces.model import ModelInterface
from interfaces.tool_executor import ToolExecutor

logger = logging.getLogger("aura.agentic_runtime")


class ExecutionMode(str, Enum):
    """Execution modes supported by the unified AgenticRuntime."""

    STANDARD_WORKFLOW = "standard_workflow"
    AUTONOMOUS_AGENT = "autonomous_agent"
    GOAL_DRIVEN = "goal_driven"


class AgenticRuntime:
    """End-to-end agentic coordinator unifying WorkflowExecutor, AutonomousAgentExecutor, and GoalEngine."""

    def __init__(
        self,
        skill_registry: SkillRegistry | None = None,
        model_router: ModelRouter | None = None,
        model: ModelInterface | None = None,
        tool_executor: ToolExecutor | None = None,
        policy: Policy | None = None,
        runtime: AgentRuntime | None = None,
        planner: TaskPlanner | None = None,
        workflow_executor: WorkflowExecutor | None = None,
        autonomous_executor: AutonomousAgentExecutor | None = None,
        goal_store: GoalStore | None = None,
        goal_engine: GoalEngine | None = None,
        state_store: TaskStateStore | None = None,
        approval_gateway: ApprovalGateway | None = None,
        max_replans: int = 0,
        default_timeout: float | None = None,
        default_mode: ExecutionMode = ExecutionMode.STANDARD_WORKFLOW,
    ):
        if skill_registry is not None and not isinstance(skill_registry, SkillRegistry):
            raise TypeError("skill_registry must be an instance of SkillRegistry or None.")
        if planner is not None and not isinstance(planner, TaskPlanner):
            raise TypeError("planner must be an instance of TaskPlanner or None.")
        if runtime is not None and not isinstance(runtime, AgentRuntime):
            raise TypeError("runtime must be an instance of AgentRuntime or None.")
        if workflow_executor is not None and not isinstance(workflow_executor, WorkflowExecutor):
            raise TypeError("workflow_executor must be an instance of WorkflowExecutor or None.")
        if autonomous_executor is not None and not isinstance(autonomous_executor, AutonomousAgentExecutor):
            raise TypeError("autonomous_executor must be an instance of AutonomousAgentExecutor or None.")
        if goal_store is not None and not isinstance(goal_store, GoalStore):
            raise TypeError("goal_store must be an instance of GoalStore or None.")
        if goal_engine is not None and not isinstance(goal_engine, GoalEngine):
            raise TypeError("goal_engine must be an instance of GoalEngine or None.")
        if state_store is not None and not isinstance(state_store, TaskStateStore):
            raise TypeError("state_store must be an instance of TaskStateStore or None.")
        if approval_gateway is not None and not isinstance(approval_gateway, ApprovalGateway):
            raise TypeError("approval_gateway must be an instance of ApprovalGateway or None.")
        if model_router is not None and not isinstance(model_router, ModelRouter):
            raise TypeError("model_router must be an instance of ModelRouter or None.")
        if model is not None and not isinstance(model, ModelInterface):
            raise TypeError("model must be an instance of ModelInterface or None.")
        if tool_executor is not None and not isinstance(tool_executor, ToolExecutor):
            raise TypeError("tool_executor must be an instance of ToolExecutor or None.")
        if policy is not None and not isinstance(policy, Policy):
            raise TypeError("policy must be an instance of Policy or None.")
        if not isinstance(max_replans, int) or max_replans < 0:
            raise ValueError("max_replans must be a non-negative integer.")
        if isinstance(default_mode, str):
            default_mode = ExecutionMode(default_mode)
        elif not isinstance(default_mode, ExecutionMode):
            raise TypeError("default_mode must be an instance of ExecutionMode.")

        # Determine effective skill_registry
        if skill_registry is None:
            if runtime is not None:
                skill_registry = runtime.skill_registry
            elif planner is not None:
                skill_registry = planner.skill_registry
            elif workflow_executor is not None:
                skill_registry = workflow_executor.runtime.skill_registry
            elif autonomous_executor is not None:
                skill_registry = autonomous_executor.runtime.skill_registry
            else:
                skill_registry = SkillRegistry()

        self.skill_registry = skill_registry
        self.model_router = model_router
        self.model = model
        self.tool_executor = tool_executor
        self.policy = policy
        self.state_store = state_store
        self.approval_gateway = approval_gateway
        self.max_replans = max_replans
        self.default_timeout = default_timeout
        self.default_mode = default_mode

        # Resolve or create AgentRuntime
        if runtime is not None:
            self.runtime = runtime
        elif workflow_executor is not None:
            self.runtime = workflow_executor.runtime
        elif autonomous_executor is not None:
            self.runtime = autonomous_executor.runtime
        else:
            self.runtime = AgentRuntime(
                skill_registry=self.skill_registry,
                model_router=self.model_router,
                tool_executor=self.tool_executor,
                policy=self.policy,
                default_timeout=self.default_timeout,
            )

        # Resolve or create TaskPlanner
        if planner is not None:
            self.planner = planner
        elif workflow_executor is not None and workflow_executor.planner is not None:
            self.planner = workflow_executor.planner
        elif autonomous_executor is not None:
            self.planner = autonomous_executor.planner
        else:
            self.planner = TaskPlanner(
                skill_registry=self.skill_registry,
                model=self.model,
                model_router=self.model_router,
            )

        # Resolve or create WorkflowExecutor (M8)
        if workflow_executor is not None:
            self.workflow_executor = workflow_executor
        else:
            self.workflow_executor = WorkflowExecutor(
                runtime=self.runtime,
                planner=self.planner,
                state_store=self.state_store,
                approval_gateway=self.approval_gateway,
                max_replans=self.max_replans,
            )

        # Resolve or create AutonomousAgentExecutor (M10)
        if autonomous_executor is not None:
            self.autonomous_executor = autonomous_executor
        else:
            self.autonomous_executor = AutonomousAgentExecutor(
                runtime=self.runtime,
                planner=self.planner,
                state_store=self.state_store,
                approval_gateway=self.approval_gateway,
            )

        # Resolve or create GoalStore & GoalEngine (M11 / M12)
        if goal_store is not None:
            self.goal_store = goal_store
        elif goal_engine is not None:
            self.goal_store = goal_engine.goal_store
        else:
            self.goal_store = InMemoryGoalStore()

        if goal_engine is not None:
            self.goal_engine = goal_engine
        else:
            self.goal_engine = GoalEngine(
                goal_store=self.goal_store,
                runtime=self.runtime,
                executor=self.autonomous_executor,
                state_store=self.state_store,
                approval_gateway=self.approval_gateway,
            )

    def execute(
        self,
        task: str | ExecutionPlan | AgentPlan | Goal | AURARequest,
        mode: ExecutionMode | str | None = None,
        task_id: str | None = None,
        task_requirements: TaskRequirements | None = None,
        timeout: float | None = None,
        metadata: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        trigger_id: str | None = None,
        parent_goal_id: str | None = None,
        depends_on_goal_ids: list[str] | tuple[str, ...] = (),
    ) -> WorkflowResult | AutonomousAgentResult | GoalEvaluationResult:
        """Execute a task in the specified ExecutionMode (STANDARD_WORKFLOW, AUTONOMOUS_AGENT, GOAL_DRIVEN)."""
        effective_mode = self.default_mode
        if mode is not None:
            effective_mode = ExecutionMode(mode) if isinstance(mode, str) else mode
        elif isinstance(task, AgentPlan):
            effective_mode = ExecutionMode.AUTONOMOUS_AGENT
        elif isinstance(task, Goal):
            effective_mode = ExecutionMode.GOAL_DRIVEN
        elif isinstance(task, ExecutionPlan):
            effective_mode = ExecutionMode.STANDARD_WORKFLOW

        if effective_mode == ExecutionMode.STANDARD_WORKFLOW:
            return self.execute_task(
                task=task,
                task_id=task_id,
                task_requirements=task_requirements,
                timeout=timeout,
                metadata=metadata,
            )
        elif effective_mode == ExecutionMode.AUTONOMOUS_AGENT:
            return self.execute_autonomous(
                task=task,
                task_id=task_id,
                task_requirements=task_requirements,
                metadata=metadata,
            )
        elif effective_mode == ExecutionMode.GOAL_DRIVEN:
            return self.execute_goal(
                goal=task,
                trigger_id=trigger_id,
                context=context,
                metadata=metadata,
                parent_goal_id=parent_goal_id,
                depends_on_goal_ids=depends_on_goal_ids,
            )
        else:
            raise ValueError(f"Unsupported execution mode: {effective_mode}")

    def execute_autonomous(
        self,
        task: str | AgentPlan | AURARequest,
        task_id: str | None = None,
        task_requirements: TaskRequirements | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AutonomousAgentResult:
        """Execute an autonomous task or plan through AutonomousAgentExecutor."""
        actual_task_id = task_id
        meta = dict(metadata) if metadata is not None else {}

        if isinstance(task, AURARequest):
            actual_task_id = actual_task_id or str(task.request_id)
            task_desc = task.user_input
            meta.update(task.metadata)
            return self.autonomous_executor.run(
                task=task_desc,
                task_id=actual_task_id,
                task_requirements=task_requirements,
                metadata=meta,
            )
        elif isinstance(task, str):
            if not task.strip():
                raise ValueError("Task description cannot be empty.")
            return self.autonomous_executor.run(
                task=task.strip(),
                task_id=actual_task_id,
                task_requirements=task_requirements,
                metadata=meta,
            )
        elif isinstance(task, AgentPlan):
            return self.autonomous_executor.execute_plan(
                plan=task,
                task_id=actual_task_id,
            )
        else:
            raise TypeError("task must be a string, AgentPlan, or AURARequest for autonomous execution.")

    def execute_goal(
        self,
        goal: str | Goal | AURARequest,
        trigger_id: str | None = None,
        context: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        parent_goal_id: str | None = None,
        depends_on_goal_ids: list[str] | tuple[str, ...] = (),
    ) -> GoalEvaluationResult:
        """Execute or evaluate a goal through GoalEngine."""
        if isinstance(goal, AURARequest):
            g = self.goal_engine.create_goal(
                title=goal.user_input,
                metadata=dict(goal.metadata),
                parent_goal_id=parent_goal_id,
                depends_on_goal_ids=depends_on_goal_ids,
            )
            return self.goal_engine.evaluate_goal(
                goal_id=g.goal_id,
                trigger_id=trigger_id,
                context=context,
            )
        elif isinstance(goal, str):
            if not goal.strip():
                raise ValueError("Goal title cannot be empty.")
            g = self.goal_engine.create_goal(
                title=goal.strip(),
                metadata=dict(metadata or {}),
                parent_goal_id=parent_goal_id,
                depends_on_goal_ids=depends_on_goal_ids,
            )
            return self.goal_engine.evaluate_goal(
                goal_id=g.goal_id,
                trigger_id=trigger_id,
                context=context,
            )
        elif isinstance(goal, Goal):
            if not self.goal_store.exists(goal.goal_id):
                self.goal_store.create(goal)
            return self.goal_engine.evaluate_goal(
                goal_id=goal.goal_id,
                trigger_id=trigger_id,
                context=context,
            )
        else:
            raise TypeError("goal must be a string, Goal, or AURARequest for goal-driven execution.")

    def execute_task(
        self,
        task: str | ExecutionPlan | AURARequest,
        task_id: str | None = None,
        task_requirements: TaskRequirements | None = None,
        timeout: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowResult:
        """Execute a task through the standard workflow executor pipeline."""
        effective_timeout = timeout if timeout is not None else self.default_timeout
        actual_task_id = task_id
        meta = dict(metadata) if metadata is not None else {}

        if isinstance(task, AURARequest):
            actual_task_id = actual_task_id or str(task.request_id)
            task_desc = task.user_input
            meta.update(task.metadata)
        elif isinstance(task, str):
            if not task.strip():
                raise ValueError("Task description cannot be empty.")
            task_desc = task.strip()
        elif isinstance(task, ExecutionPlan):
            return self.workflow_executor.execute(
                plan=task,
                task_id=actual_task_id,
                timeout=effective_timeout,
            )
        else:
            raise TypeError("task must be a string, ExecutionPlan, or AURARequest.")

        actual_task_id = actual_task_id or str(uuid4())

        # 1. Generate execution plan via TaskPlanner
        try:
            plan = self.planner.plan(
                task=task_desc,
                task_requirements=task_requirements,
                metadata={"task": task_desc, **meta},
            )
        except Exception as e:
            logger.warning("Agentic task planning failed for '%s': %s", task_desc, e)
            return WorkflowResult(
                success=False,
                plan_id="",
                task_id=actual_task_id,
                error=f"Task planning failed: {str(e)}",
                metadata={"planning_error": True, "task": task_desc},
            )

        # 2. Execute plan via WorkflowExecutor
        prev_task_desc = self.workflow_executor.task_description
        try:
            self.workflow_executor.task_description = task_desc
            return self.workflow_executor.execute(
                plan=plan,
                task_id=actual_task_id,
                timeout=effective_timeout,
            )
        finally:
            self.workflow_executor.task_description = prev_task_desc

    def resume_task(
        self,
        task_id: str,
        plan: ExecutionPlan | None = None,
        timeout: float | None = None,
    ) -> WorkflowResult:
        """Resume an interrupted or paused task through WorkflowExecutor."""
        effective_timeout = timeout if timeout is not None else self.default_timeout
        return self.workflow_executor.resume(
            task_id=task_id,
            plan=plan,
            timeout=effective_timeout,
        )

    def run_request(
        self,
        request: AURARequest,
        timeout: float | None = None,
    ) -> AURAResponse:
        """Execute an AURARequest through the agentic runtime and return a standard AURAResponse."""
        if not isinstance(request, AURARequest):
            raise TypeError("request must be an instance of AURARequest.")

        result = self.execute_task(task=request, timeout=timeout)
        content = (
            str(result.final_output)
            if result.success and result.final_output is not None
            else (result.error or "")
        )
        resp_metadata = {
            "agentic": "true",
            "success": str(result.success).lower(),
            "plan_id": str(result.plan_id),
            "task_id": str(result.task_id or request.request_id),
            "executed_steps": str(result.executed_steps),
        }
        for k, v in result.metadata.items():
            resp_metadata[str(k)] = str(v)

        return AURAResponse(
            request_id=request.request_id,
            content=content,
            metadata=resp_metadata,
        )

    def run(
        self,
        task: str | ExecutionPlan | AgentPlan | Goal | AURARequest,
        task_id: str | None = None,
        task_requirements: TaskRequirements | None = None,
        timeout: float | None = None,
        metadata: dict[str, Any] | None = None,
        mode: ExecutionMode | str | None = None,
    ) -> WorkflowResult | AutonomousAgentResult | GoalEvaluationResult:
        """Unified entry point to execute tasks across all modes."""
        return self.execute(
            task=task,
            mode=mode,
            task_id=task_id,
            task_requirements=task_requirements,
            timeout=timeout,
            metadata=metadata,
        )
