import logging
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.config import settings
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
from core.task_state_store import InMemoryTaskStateStore, TaskStateStore
from core.file_task_state_store import FileTaskStateStore
from core.workflow_executor import WorkflowExecutor, WorkflowResult
from core.memory_manager import MemoryManager
from core.meta_policy import MetaPolicyEngine
from core.strategy_lineage import StrategyLineageStore
from core.goal_adapter import GoalAdapter
from core.goal_stagnation import GoalStagnationMonitor
from core.resource_budget import ResourceBudgetManager
from core.resource_locks import SharedResourceLockManager
from core.goal_scheduler import MultiGoalScheduler
from core.event_dispatcher import ProactiveEventDispatcher
from core.clarification_gateway import ClarificationGateway
from core.scheduling_types import ProactiveEvent
from core.daemon_types import (
    CheckpointMetadata,
    DaemonStatus,
    SupervisorConfig,
    SupervisorTelemetry,
)
from core.runtime_checkpoint import RuntimeCheckpointManager
from core.runtime_supervisor import AutonomousSupervisor
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
        memory_manager: MemoryManager | None = None,
        reflector: Any | None = None,
        consolidator: Any | None = None,
        calibrator: Any | None = None,
        meta_policy: MetaPolicyEngine | None = None,
        strategy_lineage: StrategyLineageStore | None = None,
        goal_adapter: GoalAdapter | None = None,
        stagnation_monitor: GoalStagnationMonitor | None = None,
        budget_manager: ResourceBudgetManager | None = None,
        lock_manager: SharedResourceLockManager | None = None,
        scheduler: MultiGoalScheduler | None = None,
        event_dispatcher: ProactiveEventDispatcher | None = None,
        clarification_gateway: ClarificationGateway | None = None,
        checkpoint_manager: RuntimeCheckpointManager | None = None,
        supervisor: AutonomousSupervisor | None = None,
        supervisor_config: SupervisorConfig | None = None,
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
        if memory_manager is not None and not isinstance(memory_manager, MemoryManager):
            raise TypeError("memory_manager must be an instance of MemoryManager or None.")
        if checkpoint_manager is not None and not isinstance(checkpoint_manager, RuntimeCheckpointManager):
            raise TypeError("checkpoint_manager must be an instance of RuntimeCheckpointManager or None.")
        if supervisor is not None and not isinstance(supervisor, AutonomousSupervisor):
            raise TypeError("supervisor must be an instance of AutonomousSupervisor or None.")
        if supervisor_config is not None and not isinstance(supervisor_config, SupervisorConfig):
            raise TypeError("supervisor_config must be an instance of SupervisorConfig or None.")
        if not isinstance(max_replans, int) or max_replans < 0:
            raise ValueError("max_replans must be a non-negative integer.")
        if isinstance(default_mode, str):
            default_mode = ExecutionMode(default_mode)
        elif not isinstance(default_mode, ExecutionMode):
            raise TypeError("default_mode must be an instance of ExecutionMode.")

        self.memory_manager = memory_manager if memory_manager is not None else MemoryManager()

        from core.agent_reflection import AgentReflector
        from core.memory_consolidation import MemoryConsolidator
        from core.heuristic_calibrator import HeuristicCalibrator

        self.reflector = reflector if reflector is not None else AgentReflector()
        self.calibrator = calibrator if calibrator is not None else HeuristicCalibrator()
        self.consolidator = (
            consolidator
            if consolidator is not None
            else MemoryConsolidator(
                memory_store=getattr(self.memory_manager, "store", None),
                memory_manager=self.memory_manager,
            )
        )
        self.strategy_lineage = strategy_lineage if strategy_lineage is not None else StrategyLineageStore()
        self.meta_policy = (
            meta_policy
            if meta_policy is not None
            else MetaPolicyEngine(
                lineage_store=self.strategy_lineage,
                calibrator=self.calibrator,
            )
        )
        self.stagnation_monitor = (
            stagnation_monitor
            if stagnation_monitor is not None
            else GoalStagnationMonitor(
                lineage_store=self.strategy_lineage,
                meta_policy=self.meta_policy,
            )
        )
        self.budget_manager = budget_manager if budget_manager is not None else ResourceBudgetManager()
        self.lock_manager = lock_manager if lock_manager is not None else SharedResourceLockManager()
        self.clarification_gateway = clarification_gateway if clarification_gateway is not None else ClarificationGateway()
        self.event_dispatcher = event_dispatcher if event_dispatcher is not None else ProactiveEventDispatcher()
        self.scheduler = (
            scheduler
            if scheduler is not None
            else MultiGoalScheduler(
                budget_manager=self.budget_manager,
                lock_manager=self.lock_manager,
            )
        )

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

        # Determine effective state_store (M10/M18)
        if state_store is not None:
            self.state_store = state_store
        else:
            task_dir = getattr(settings, "aura_task_state_storage_dir", "")
            if task_dir:
                self.state_store = FileTaskStateStore(task_dir)
            else:
                self.state_store = InMemoryTaskStateStore()

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
            if self.planner.memory_manager is None:
                self.planner.memory_manager = self.memory_manager
        elif workflow_executor is not None and workflow_executor.planner is not None:
            self.planner = workflow_executor.planner
            if self.planner.memory_manager is None:
                self.planner.memory_manager = self.memory_manager
        elif autonomous_executor is not None:
            self.planner = autonomous_executor.planner
            if self.planner.memory_manager is None:
                self.planner.memory_manager = self.memory_manager
        else:
            self.planner = TaskPlanner(
                skill_registry=self.skill_registry,
                model=self.model,
                model_router=self.model_router,
                memory_manager=self.memory_manager,
                heuristic_calibrator=self.calibrator,
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
            if self.autonomous_executor.memory_manager is None:
                self.autonomous_executor.memory_manager = self.memory_manager
            if getattr(self.autonomous_executor, "reflector", None) is None:
                self.autonomous_executor.reflector = self.reflector
            if getattr(self.autonomous_executor, "consolidator", None) is None:
                self.autonomous_executor.consolidator = self.consolidator
            if getattr(self.autonomous_executor, "calibrator", None) is None:
                self.autonomous_executor.calibrator = self.calibrator
        else:
            self.autonomous_executor = AutonomousAgentExecutor(
                runtime=self.runtime,
                planner=self.planner,
                state_store=self.state_store,
                approval_gateway=self.approval_gateway,
                memory_manager=self.memory_manager,
                reflector=self.reflector,
                consolidator=self.consolidator,
                calibrator=self.calibrator,
            )

        # Resolve or create GoalStore & GoalEngine (M11 / M12)
        if goal_store is not None:
            self.goal_store = goal_store
        elif goal_engine is not None:
            self.goal_store = goal_engine.goal_store
        else:
            self.goal_store = InMemoryGoalStore()

        self.goal_adapter = (
            goal_adapter
            if goal_adapter is not None
            else GoalAdapter(
                meta_policy=self.meta_policy,
                lineage_store=self.strategy_lineage,
                planner=self.planner,
                heuristic_calibrator=self.calibrator,
                clarification_gateway=self.clarification_gateway,
            )
        )

        if goal_engine is not None:
            self.goal_engine = goal_engine
            if self.goal_engine.memory_manager is None:
                self.goal_engine.memory_manager = self.memory_manager
            if getattr(self.goal_engine, "heuristic_calibrator", None) is None and self.calibrator is not None:
                self.goal_engine.heuristic_calibrator = self.calibrator
            if getattr(self.goal_engine, "meta_policy", None) is None:
                self.goal_engine.meta_policy = self.meta_policy
            if getattr(self.goal_engine, "strategy_lineage", None) is None:
                self.goal_engine.strategy_lineage = self.strategy_lineage
            if getattr(self.goal_engine, "goal_adapter", None) is None:
                self.goal_engine.goal_adapter = self.goal_adapter
            if getattr(self.goal_engine, "stagnation_monitor", None) is None:
                self.goal_engine.stagnation_monitor = self.stagnation_monitor
        else:
            self.goal_engine = GoalEngine(
                goal_store=self.goal_store,
                runtime=self.runtime,
                executor=self.autonomous_executor,
                state_store=self.state_store,
                approval_gateway=self.approval_gateway,
                memory_manager=self.memory_manager,
                heuristic_calibrator=self.calibrator,
                meta_policy=self.meta_policy,
                strategy_lineage=self.strategy_lineage,
                goal_adapter=self.goal_adapter,
                stagnation_monitor=self.stagnation_monitor,
                budget_manager=self.budget_manager,
                lock_manager=self.lock_manager,
                scheduler=self.scheduler,
                event_dispatcher=self.event_dispatcher,
                clarification_gateway=self.clarification_gateway,
            )

        # Runtime Checkpoint Manager & Supervisor Daemon (M18)
        ckpt_dir = getattr(settings, "aura_checkpoint_dir", ".aura_checkpoints")
        ckpt_ret = getattr(settings, "aura_checkpoint_retention_count", 5)
        self.checkpoint_manager = (
            checkpoint_manager
            if checkpoint_manager is not None
            else RuntimeCheckpointManager(
                checkpoint_dir=ckpt_dir,
                retention_count=ckpt_ret,
                scheduler=self.scheduler,
                budget_manager=self.budget_manager,
                lock_manager=self.lock_manager,
                clarification_gateway=self.clarification_gateway,
                event_dispatcher=self.event_dispatcher,
            )
        )
        self.supervisor_config = (
            supervisor_config
            if supervisor_config is not None
            else SupervisorConfig(
                checkpoint_dir=ckpt_dir,
                checkpoint_retention_count=ckpt_ret,
                shutdown_timeout_seconds=getattr(settings, "aura_daemon_shutdown_timeout_seconds", 5.0),
                heartbeat_interval_seconds=getattr(settings, "aura_daemon_heartbeat_interval_seconds", 1.0),
                scheduler_interval_seconds=getattr(settings, "aura_daemon_scheduler_interval_seconds", 2.0),
                event_interval_seconds=getattr(settings, "aura_daemon_event_interval_seconds", 1.0),
                lock_prune_interval_seconds=getattr(settings, "aura_daemon_lock_prune_interval_seconds", 10.0),
                clarification_interval_seconds=getattr(settings, "aura_daemon_clarification_interval_seconds", 10.0),
                memory_interval_seconds=getattr(settings, "aura_daemon_memory_interval_seconds", 300.0),
                checkpoint_interval_seconds=getattr(settings, "aura_daemon_checkpoint_interval_seconds", 30.0),
            )
        )
        self.supervisor = (
            supervisor
            if supervisor is not None
            else AutonomousSupervisor(
                runtime=self,
                config=self.supervisor_config,
                checkpoint_manager=self.checkpoint_manager,
            )
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

    # ---------------------------------------------------------
    # Memory Lifecycle & Calibration Helpers (M15)
    # ---------------------------------------------------------
    def run_memory_lifecycle_pass(
        self,
        current_time: float | None = None,
    ) -> dict[str, Any]:
        """Execute a memory maintenance pass across all memory tiers."""
        return self.memory_manager.run_lifecycle_pass(current_time=current_time)

    def compact_memory(
        self,
        tier: Any = None,
        namespace: str | None = None,
        current_time: float | None = None,
    ) -> Any:
        """Execute compaction pass on a specific memory tier or namespace."""
        from core.memory_types import MemoryTier
        effective_tier = tier if tier is not None else MemoryTier.SEMANTIC
        return self.memory_manager.compact_memory(
            tier=effective_tier,
            namespace=namespace,
            current_time=current_time,
        )

    def get_heuristic_calibrator(self) -> Any:
        """Return the runtime's heuristic calibrator instance."""
        return self.calibrator

    # ---------------------------------------------------------
    # Scheduling, Resource Governance & Event Dispatch (M17)
    # ---------------------------------------------------------
    def get_budget_manager(self) -> ResourceBudgetManager:
        """Return the runtime's resource budget manager."""
        return self.budget_manager

    def get_lock_manager(self) -> SharedResourceLockManager:
        """Return the runtime's shared resource lock manager."""
        return self.lock_manager

    def get_scheduler(self) -> MultiGoalScheduler:
        """Return the runtime's multi-goal priority scheduler."""
        return self.scheduler

    def get_event_dispatcher(self) -> ProactiveEventDispatcher:
        """Return the runtime's proactive event dispatcher."""
        return self.event_dispatcher

    def get_clarification_gateway(self) -> ClarificationGateway:
        """Return the runtime's interactive clarification gateway."""
        return self.clarification_gateway

    def publish_event(self, event: ProactiveEvent) -> int:
        """Publish a proactive event to trigger subscribed goals."""
        return self.event_dispatcher.publish_event(event)

    def step_scheduled_goals(self, max_batch_size: int = 4) -> list[GoalEvaluationResult]:
        """Step the multi-goal scheduler to evaluate the next batch of queued goals."""
        # Enqueue any active goals from the store into the scheduler
        for g in self.goal_engine.list_goals(status=GoalStatus.ACTIVE):
            self.scheduler.schedule_goal(g.goal_id, priority=g.priority)
        return self.scheduler.step_next_batch(self.goal_engine, max_batch_size=max_batch_size)

    # ---------------------------------------------------------
    # Runtime Supervision & Checkpoint Management (M18)
    # ---------------------------------------------------------
    def get_checkpoint_manager(self) -> RuntimeCheckpointManager:
        """Return the runtime's session checkpoint manager."""
        return self.checkpoint_manager

    def get_supervisor(self) -> AutonomousSupervisor:
        """Return the runtime's autonomous supervisor daemon instance."""
        return self.supervisor

    def start_daemon(self, auto_recover: bool | None = None) -> bool:
        """Start the background supervisor daemon."""
        return self.supervisor.start(auto_recover=auto_recover)

    def stop_daemon(self, timeout: float | None = None) -> bool:
        """Gracefully stop the background supervisor daemon."""
        return self.supervisor.stop(timeout=timeout)

    def is_daemon_running(self) -> bool:
        """Check if the supervisor daemon is actively running."""
        return self.supervisor.is_running()

    @property
    def daemon_status(self) -> DaemonStatus:
        """Return the current lifecycle status of the supervisor daemon."""
        return self.supervisor.status

    def get_supervisor_telemetry(self) -> SupervisorTelemetry:
        """Return an aggregated telemetry and health diagnostics snapshot."""
        return self.supervisor.get_telemetry()

    def create_checkpoint(
        self,
        checkpoint_id: str | None = None,
        is_clean_shutdown: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> CheckpointMetadata:
        """Create and persist an atomic runtime session checkpoint."""
        return self.checkpoint_manager.save_checkpoint(
            checkpoint_id=checkpoint_id,
            is_clean_shutdown=is_clean_shutdown,
            metadata=metadata,
        )

    def restore_checkpoint(
        self,
        checkpoint_path: str | Path | None = None,
    ) -> CheckpointMetadata | None:
        """Restore runtime state from a specific checkpoint or the latest valid checkpoint."""
        if checkpoint_path is not None:
            return self.checkpoint_manager.restore_from_file(checkpoint_path)
        return self.checkpoint_manager.restore_latest_checkpoint()
