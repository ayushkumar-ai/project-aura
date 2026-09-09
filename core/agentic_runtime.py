import logging
from typing import Any
from uuid import UUID, uuid4

from core.agent_runtime import AgentRuntime
from core.approval import ApprovalGateway
from core.capability_registry import ModelCapability
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


class AgenticRuntime:
    """End-to-end agentic coordinator integrating TaskPlanner, WorkflowExecutor, and AgentRuntime."""

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
        state_store: TaskStateStore | None = None,
        approval_gateway: ApprovalGateway | None = None,
        max_replans: int = 0,
        default_timeout: float | None = None,
    ):
        if skill_registry is not None and not isinstance(skill_registry, SkillRegistry):
            raise TypeError("skill_registry must be an instance of SkillRegistry or None.")
        if planner is not None and not isinstance(planner, TaskPlanner):
            raise TypeError("planner must be an instance of TaskPlanner or None.")
        if runtime is not None and not isinstance(runtime, AgentRuntime):
            raise TypeError("runtime must be an instance of AgentRuntime or None.")
        if workflow_executor is not None and not isinstance(workflow_executor, WorkflowExecutor):
            raise TypeError("workflow_executor must be an instance of WorkflowExecutor or None.")
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

        # Determine effective skill_registry
        if skill_registry is None:
            if runtime is not None:
                skill_registry = runtime.skill_registry
            elif planner is not None:
                skill_registry = planner.skill_registry
            elif workflow_executor is not None:
                skill_registry = workflow_executor.runtime.skill_registry
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

        # Resolve or create AgentRuntime
        if runtime is not None:
            self.runtime = runtime
        elif workflow_executor is not None:
            self.runtime = workflow_executor.runtime
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
        else:
            self.planner = TaskPlanner(
                skill_registry=self.skill_registry,
                model=self.model,
                model_router=self.model_router,
            )

        # Resolve or create WorkflowExecutor
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

    def execute_task(
        self,
        task: str | ExecutionPlan | AURARequest,
        task_id: str | None = None,
        task_requirements: TaskRequirements | None = None,
        timeout: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowResult:
        """Execute a task through the end-to-end agentic runtime pipeline."""
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
        task: str | ExecutionPlan | AURARequest,
        task_id: str | None = None,
        task_requirements: TaskRequirements | None = None,
        timeout: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkflowResult:
        """Alias for execute_task."""
        return self.execute_task(
            task=task,
            task_id=task_id,
            task_requirements=task_requirements,
            timeout=timeout,
            metadata=metadata,
        )
