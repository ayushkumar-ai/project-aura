import copy
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from app.config import settings
from core.agent_plan import AgentPlan, AgentPlanStep, StepStatus
from core.agent_runtime import AgentRuntime
from core.approval import ApprovalGateway
from core.autonomous_agent import (
    AgentLoopConfig,
    AutonomousAgentExecutor,
    AutonomousAgentResult,
)
from core.goal import (
    Goal,
    GoalObservation,
    GoalPriority,
    GoalProgress,
    GoalStatus,
    GoalTrigger,
    TriggerType,
    detect_dependency_cycles,
    get_topological_evaluation_order,
    validate_goal_hierarchy,
)
from core.goal_reasoner import GoalEvaluationResult, GoalReasoner
from core.goal_store import GoalStore, InMemoryGoalStore
from core.policy import Policy
from core.provenance import TaintedValue, is_tainted, wrap_tainted
from core.skill_registry import SkillRegistry
from core.task_state import TaskState, TaskStatus
from core.task_state_store import TaskStateStore
from core.meta_policy import MetaPolicyEngine
from core.strategy_lineage import StrategyLineageStore
from core.goal_adapter import GoalAdapter
from core.goal_stagnation import GoalStagnationMonitor
from core.strategy_types import StrategyAttemptOutcome, StrategyType
from core.resource_budget import ResourceBudgetManager
from core.resource_locks import SharedResourceLockManager
from core.goal_scheduler import MultiGoalScheduler
from core.event_dispatcher import ProactiveEventDispatcher
from core.clarification_gateway import ClarificationGateway
from core.scheduling_types import LockType, ClarificationStatus

logger = logging.getLogger("aura.goal_engine")


@dataclass(frozen=True)
class GoalEngineConfig:
    """Configuration parameters and resource bounds for the GoalEngine."""

    max_active_goals: int = 10
    max_evaluations_per_goal: int = 50
    max_actions_per_goal: int = 20
    evaluation_cooldown_seconds: float = 5.0
    goal_timeout_seconds: float = 3600.0
    auto_pause_on_approval: bool = True
    max_subgoal_depth: int = 3
    max_subgoals_per_parent: int = 5
    max_goal_dependencies: int = 10
    max_observations_per_goal: int = 50
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.max_active_goals, int) or self.max_active_goals <= 0:
            raise ValueError("max_active_goals must be a positive integer.")
        if not isinstance(self.max_evaluations_per_goal, int) or self.max_evaluations_per_goal <= 0:
            raise ValueError("max_evaluations_per_goal must be a positive integer.")
        if not isinstance(self.max_actions_per_goal, int) or self.max_actions_per_goal <= 0:
            raise ValueError("max_actions_per_goal must be a positive integer.")
        if not isinstance(self.evaluation_cooldown_seconds, (int, float)) or self.evaluation_cooldown_seconds < 0:
            raise ValueError("evaluation_cooldown_seconds must be non-negative.")
        if not isinstance(self.goal_timeout_seconds, (int, float)) or self.goal_timeout_seconds <= 0:
            raise ValueError("goal_timeout_seconds must be a positive number.")
        if not isinstance(self.auto_pause_on_approval, bool):
            raise TypeError("auto_pause_on_approval must be a boolean.")
        if not isinstance(self.max_subgoal_depth, int) or self.max_subgoal_depth < 0:
            raise ValueError("max_subgoal_depth must be a non-negative integer.")
        if not isinstance(self.max_subgoals_per_parent, int) or self.max_subgoals_per_parent <= 0:
            raise ValueError("max_subgoals_per_parent must be a positive integer.")
        if not isinstance(self.max_goal_dependencies, int) or self.max_goal_dependencies <= 0:
            raise ValueError("max_goal_dependencies must be a positive integer.")
        if not isinstance(self.max_observations_per_goal, int) or self.max_observations_per_goal <= 0:
            raise ValueError("max_observations_per_goal must be a positive integer.")


class GoalEngine:
    """Proactive Goal Engine managing goal lifecycles, hierarchical subgoals, triggers, and safe actions."""

    def __init__(
        self,
        goal_store: GoalStore | None = None,
        reasoner: GoalReasoner | None = None,
        executor: AutonomousAgentExecutor | None = None,
        runtime: AgentRuntime | None = None,
        state_store: TaskStateStore | None = None,
        approval_gateway: ApprovalGateway | None = None,
        config: GoalEngineConfig | None = None,
        memory_manager: Any | None = None,
        heuristic_calibrator: Any | None = None,
        meta_policy: MetaPolicyEngine | None = None,
        strategy_lineage: StrategyLineageStore | None = None,
        goal_adapter: GoalAdapter | None = None,
        stagnation_monitor: GoalStagnationMonitor | None = None,
        budget_manager: ResourceBudgetManager | None = None,
        lock_manager: SharedResourceLockManager | None = None,
        scheduler: MultiGoalScheduler | None = None,
        event_dispatcher: ProactiveEventDispatcher | None = None,
        clarification_gateway: ClarificationGateway | None = None,
    ):
        if goal_store is not None and not isinstance(goal_store, GoalStore):
            raise TypeError("goal_store must be an instance of GoalStore or None.")
        if reasoner is not None and not isinstance(reasoner, GoalReasoner):
            raise TypeError("reasoner must be an instance of GoalReasoner or None.")
        if executor is not None and not isinstance(executor, AutonomousAgentExecutor):
            raise TypeError("executor must be an instance of AutonomousAgentExecutor or None.")
        if runtime is not None and not isinstance(runtime, AgentRuntime):
            raise TypeError("runtime must be an instance of AgentRuntime or None.")
        if state_store is not None and not isinstance(state_store, TaskStateStore):
            raise TypeError("state_store must be an instance of TaskStateStore or None.")
        if approval_gateway is not None and not isinstance(approval_gateway, ApprovalGateway):
            raise TypeError("approval_gateway must be an instance of ApprovalGateway or None.")
        if config is not None and not isinstance(config, GoalEngineConfig):
            raise TypeError("config must be an instance of GoalEngineConfig or None.")

        self.memory_manager = memory_manager
        self.heuristic_calibrator = heuristic_calibrator
        self.strategy_lineage = strategy_lineage if strategy_lineage is not None else StrategyLineageStore()
        self.meta_policy = (
            meta_policy
            if meta_policy is not None
            else MetaPolicyEngine(
                lineage_store=self.strategy_lineage,
                calibrator=self.heuristic_calibrator,
            )
        )
        self.goal_adapter = (
            goal_adapter
            if goal_adapter is not None
            else GoalAdapter(
                meta_policy=self.meta_policy,
                lineage_store=self.strategy_lineage,
                heuristic_calibrator=self.heuristic_calibrator,
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
        self.budget_manager = budget_manager
        self.lock_manager = lock_manager
        self.clarification_gateway = clarification_gateway
        self.event_dispatcher = event_dispatcher
        self.scheduler = (
            scheduler
            if scheduler is not None
            else MultiGoalScheduler(
                budget_manager=self.budget_manager,
                lock_manager=self.lock_manager,
            )
        )

        self.goal_store = goal_store if goal_store is not None else InMemoryGoalStore()
        self.state_store = state_store
        self.approval_gateway = approval_gateway

        self.config = config if config is not None else GoalEngineConfig(
            max_active_goals=getattr(settings, "aura_max_active_goals", 10),
            max_evaluations_per_goal=getattr(settings, "aura_max_evaluations_per_goal", 50),
            max_actions_per_goal=getattr(settings, "aura_max_actions_per_goal", 20),
            evaluation_cooldown_seconds=getattr(settings, "aura_goal_cooldown_seconds", 5.0),
            goal_timeout_seconds=getattr(settings, "aura_goal_timeout_seconds", 3600.0),
            max_subgoal_depth=getattr(settings, "aura_max_subgoal_depth", 3),
            max_subgoals_per_parent=getattr(settings, "aura_max_subgoals_per_parent", 5),
            max_goal_dependencies=getattr(settings, "aura_max_goal_dependencies", 10),
            max_observations_per_goal=getattr(settings, "aura_max_goal_observations", 50),
        )

        self.runtime = runtime
        if self.runtime is None and executor is not None:
            self.runtime = executor.runtime

        self.reasoner = reasoner if reasoner is not None else GoalReasoner(
            skill_registry=self.runtime.skill_registry if self.runtime else None,
            memory_manager=self.memory_manager,
            heuristic_calibrator=self.heuristic_calibrator,
        )
        if self.reasoner.memory_manager is None and self.memory_manager is not None:
            self.reasoner.memory_manager = self.memory_manager
        if getattr(self.reasoner, "heuristic_calibrator", None) is None and self.heuristic_calibrator is not None:
            self.reasoner.heuristic_calibrator = self.heuristic_calibrator

        self.executor = executor if executor is not None else (
            AutonomousAgentExecutor(
                runtime=self.runtime,
                state_store=self.state_store,
                approval_gateway=self.approval_gateway,
                memory_manager=self.memory_manager,
            )
            if self.runtime is not None
            else None
        )
        if self.executor is not None and self.executor.memory_manager is None and self.memory_manager is not None:
            self.executor.memory_manager = self.memory_manager

    def create_goal(
        self,
        title: str,
        description: str = "",
        success_criteria: list[str] | tuple[str, ...] = (),
        constraints: list[str] | tuple[str, ...] = (),
        priority: GoalPriority = GoalPriority.MEDIUM,
        triggers: list[GoalTrigger] | tuple[GoalTrigger, ...] = (),
        expires_at: float | None = None,
        auto_activate: bool = True,
        metadata: dict[str, Any] | None = None,
        parent_goal_id: str | None = None,
        subgoal_ids: list[str] | tuple[str, ...] = (),
        depends_on_goal_ids: list[str] | tuple[str, ...] = (),
        depth: int = 0,
    ) -> Goal:
        """Create and store a new Goal entity with hierarchical sub-goal and dependency support."""
        active_goals = self.goal_store.list_goals(status=GoalStatus.ACTIVE)
        if len(active_goals) >= self.config.max_active_goals:
            raise ValueError(
                f"Active goals limit reached ({self.config.max_active_goals}). "
                "Complete, pause, or cancel existing goals before creating new ones."
            )

        effective_depth = depth
        clean_parent_id = parent_goal_id.strip() if parent_goal_id else None

        # Validate parent goal if specified
        if clean_parent_id:
            if not self.goal_store.exists(clean_parent_id):
                raise KeyError(f"Parent goal '{clean_parent_id}' not found.")
            parent_goal = self.goal_store.get(clean_parent_id)
            if parent_goal.status.is_terminal():
                raise ValueError(f"Cannot add sub-goal to terminal parent goal '{clean_parent_id}'.")
            effective_depth = parent_goal.depth + 1
            if effective_depth > self.config.max_subgoal_depth:
                raise ValueError(
                    f"Sub-goal depth ({effective_depth}) exceeds configured maximum ({self.config.max_subgoal_depth})."
                )
            if len(parent_goal.subgoal_ids) >= self.config.max_subgoals_per_parent:
                raise ValueError(
                    f"Parent goal '{clean_parent_id}' reached max sub-goals limit ({self.config.max_subgoals_per_parent})."
                )

        # Validate dependencies
        clean_deps = [str(d).strip() for d in depends_on_goal_ids if str(d).strip()]
        if len(clean_deps) > self.config.max_goal_dependencies:
            raise ValueError(
                f"Goal dependency count ({len(clean_deps)}) exceeds configured maximum ({self.config.max_goal_dependencies})."
            )

        for dep_id in clean_deps:
            if not self.goal_store.exists(dep_id):
                raise KeyError(f"Dependency goal '{dep_id}' not found.")

        status = GoalStatus.ACTIVE if auto_activate else GoalStatus.CREATED
        goal = Goal(
            title=title,
            description=description,
            success_criteria=tuple(success_criteria),
            constraints=tuple(constraints),
            priority=priority,
            status=status,
            triggers=tuple(triggers),
            progress=GoalProgress(remaining_criteria=tuple(success_criteria)),
            expires_at=expires_at,
            metadata=dict(metadata or {}),
            parent_goal_id=clean_parent_id,
            subgoal_ids=tuple(str(s).strip() for s in subgoal_ids if str(s).strip()),
            depends_on_goal_ids=tuple(clean_deps),
            depth=effective_depth,
        )

        # Check for dependency cycle
        all_stored = {g.goal_id: g for g in self.goal_store.list_goals()}
        all_stored[goal.goal_id] = goal
        cycles = detect_dependency_cycles(all_stored)
        if cycles:
            cycle_strs = [" -> ".join(c) for c in cycles]
            raise ValueError(f"Dependency cycle detected when creating goal '{title}': {', '.join(cycle_strs)}")

        stored_goal = self.goal_store.create(goal)

        # Update parent goal's subgoal_ids
        if clean_parent_id:
            parent_goal = self.goal_store.get(clean_parent_id)
            updated_parent = parent_goal.with_subgoal(stored_goal.goal_id)
            self.goal_store.update(updated_parent)

        return stored_goal

    def create_subgoal(
        self,
        parent_goal_id: str,
        title: str,
        description: str = "",
        success_criteria: list[str] | tuple[str, ...] = (),
        constraints: list[str] | tuple[str, ...] = (),
        priority: GoalPriority = GoalPriority.MEDIUM,
        triggers: list[GoalTrigger] | tuple[GoalTrigger, ...] = (),
        expires_at: float | None = None,
        auto_activate: bool = True,
        metadata: dict[str, Any] | None = None,
        depends_on_goal_ids: list[str] | tuple[str, ...] = (),
    ) -> Goal:
        """Convenience method to create a hierarchical sub-goal linked to a parent."""
        return self.create_goal(
            title=title,
            description=description,
            success_criteria=success_criteria,
            constraints=constraints,
            priority=priority,
            triggers=triggers,
            expires_at=expires_at,
            auto_activate=auto_activate,
            metadata=metadata,
            parent_goal_id=parent_goal_id,
            depends_on_goal_ids=depends_on_goal_ids,
        )

    def get_goal(self, goal_id: str) -> Goal:
        """Retrieve a Goal by ID."""
        return self.goal_store.get(goal_id)

    def list_goals(
        self,
        status: GoalStatus | None = None,
        priority: GoalPriority | None = None,
        parent_goal_id: str | None = None,
    ) -> list[Goal]:
        """List all goals matching filters."""
        return self.goal_store.list_goals(
            status=status,
            priority=priority,
            parent_goal_id=parent_goal_id,
        )

    def pause_goal(self, goal_id: str) -> Goal:
        """Pause an active goal."""
        goal = self.get_goal(goal_id)
        if goal.status.is_terminal():
            raise ValueError(f"Cannot pause terminal goal '{goal_id}' with status '{goal.status}'.")

        updated = goal.with_status(GoalStatus.PAUSED)
        self.goal_store.update(updated)
        return updated

    def resume_goal(self, goal_id: str) -> Goal:
        """Resume a paused goal."""
        goal = self.get_goal(goal_id)
        if goal.status != GoalStatus.PAUSED and goal.status != GoalStatus.CREATED:
            raise ValueError(f"Cannot resume goal '{goal_id}' with status '{goal.status}'.")

        if goal.is_expired():
            updated = goal.with_status(GoalStatus.EXPIRED)
            self.goal_store.update(updated)
            return updated

        updated = goal.with_status(GoalStatus.ACTIVE)
        self.goal_store.update(updated)
        return updated

    def cancel_goal(self, goal_id: str, reason: str | None = None) -> Goal:
        """Cancel a goal."""
        goal = self.get_goal(goal_id)
        if goal.status.is_terminal():
            return goal

        meta = dict(goal.metadata)
        if reason:
            meta["cancel_reason"] = reason

        updated = Goal(
            goal_id=goal.goal_id,
            title=goal.title,
            description=goal.description,
            success_criteria=goal.success_criteria,
            constraints=goal.constraints,
            priority=goal.priority,
            status=GoalStatus.CANCELLED,
            triggers=goal.triggers,
            progress=goal.progress,
            evaluation_count=goal.evaluation_count,
            action_count=goal.action_count,
            created_at=goal.created_at,
            updated_at=time.time(),
            expires_at=goal.expires_at,
            metadata=meta,
            parent_goal_id=goal.parent_goal_id,
            subgoal_ids=goal.subgoal_ids,
            depends_on_goal_ids=goal.depends_on_goal_ids,
            depth=goal.depth,
            executed_task_ids=goal.executed_task_ids,
        )
        self.goal_store.update(updated)
        return updated

    def add_observation(
        self,
        goal_id: str,
        source: str,
        data: Any,
        is_untrusted: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> GoalObservation:
        """Ingest and persist a contextual observation for a goal."""
        goal = self.get_goal(goal_id)
        if goal.status.is_terminal():
            raise ValueError(f"Cannot add observation to terminal goal '{goal_id}'.")

        obs = GoalObservation(
            goal_id=goal_id,
            source=source,
            data=data,
            is_untrusted=is_untrusted,
            metadata=dict(metadata or {}),
        )

        return self.goal_store.add_observation(goal_id, obs)

    def get_observations(self, goal_id: str) -> list[GoalObservation]:
        """Retrieve all observations associated with a goal from persistent store."""
        return self.goal_store.get_observations(goal_id)

    def clear_observations(self, goal_id: str) -> None:
        """Clear all observations for a goal in persistent store."""
        self.goal_store.clear_observations(goal_id)

    def evaluate_goal(
        self,
        goal_id: str,
        trigger_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> GoalEvaluationResult:
        """Evaluate a goal's progress, dependencies, sub-goals, and execute actions if needed."""
        goal = self.get_goal(goal_id)

        # 1. Terminal / Inactive / Expiry checks
        if goal.status.is_terminal():
            return GoalEvaluationResult(
                is_completed=(goal.status == GoalStatus.COMPLETED),
                action_needed=False,
                new_progress=goal.progress,
                rationale=f"Goal is in terminal state '{goal.status}'.",
            )

        if goal.status == GoalStatus.PAUSED:
            return GoalEvaluationResult(
                is_completed=False,
                action_needed=False,
                new_progress=goal.progress,
                rationale="Goal is paused.",
            )

        if goal.is_expired():
            expired_goal = goal.with_status(GoalStatus.EXPIRED)
            self.goal_store.update(expired_goal)
            return GoalEvaluationResult(
                is_completed=False,
                action_needed=False,
                new_progress=goal.progress,
                rationale="Goal has expired.",
            )

        # 2. Evaluation bounds check
        if goal.evaluation_count >= self.config.max_evaluations_per_goal:
            failed_goal = goal.with_status(GoalStatus.FAILED)
            self.goal_store.update(failed_goal)
            return GoalEvaluationResult(
                is_completed=False,
                action_needed=False,
                new_progress=goal.progress,
                rationale=f"Max evaluations limit reached ({self.config.max_evaluations_per_goal}).",
            )

        # 3. Check Prerequisite Goal Dependencies (M12)
        if goal.depends_on_goal_ids:
            for dep_id in goal.depends_on_goal_ids:
                if not self.goal_store.exists(dep_id):
                    logger.warning("Goal '%s' depends on missing goal '%s'.", goal_id, dep_id)
                    blocked_goal = goal.with_status(GoalStatus.BLOCKED)
                    self.goal_store.update(blocked_goal)
                    return GoalEvaluationResult(
                        is_completed=False,
                        action_needed=False,
                        new_progress=goal.progress,
                        rationale=f"Dependency goal '{dep_id}' does not exist.",
                    )

                dep_goal = self.goal_store.get(dep_id)
                if dep_goal.status in (GoalStatus.FAILED, GoalStatus.CANCELLED, GoalStatus.EXPIRED):
                    logger.warning("Goal '%s' blocked by failed dependency '%s' (%s).", goal_id, dep_id, dep_goal.status)
                    blocked_goal = goal.with_status(GoalStatus.BLOCKED)
                    self.goal_store.update(blocked_goal)
                    return GoalEvaluationResult(
                        is_completed=False,
                        action_needed=False,
                        new_progress=goal.progress,
                        rationale=f"Prerequisite goal '{dep_id}' failed or was cancelled (status: {dep_goal.status}).",
                    )

                if dep_goal.status != GoalStatus.COMPLETED:
                    # Prerequisite not yet complete - cannot take action
                    logger.info("Goal '%s' waiting for prerequisite '%s' (status: %s).", goal_id, dep_id, dep_goal.status)
                    return GoalEvaluationResult(
                        is_completed=False,
                        action_needed=False,
                        new_progress=goal.progress,
                        rationale=f"Waiting for prerequisite goal '{dep_id}' to complete (current status: {dep_goal.status}).",
                    )

        # 4. Trigger check (if specific trigger passed)
        matched_trigger = None
        if trigger_id:
            for t in goal.triggers:
                if t.trigger_id == trigger_id:
                    matched_trigger = t
                    break
            if matched_trigger is not None and not matched_trigger.is_ready(context=context):
                return GoalEvaluationResult(
                    is_completed=False,
                    action_needed=False,
                    new_progress=goal.progress,
                    rationale=f"Trigger '{trigger_id}' is on cooldown or condition not met.",
                )

        # 4.5 Check for pending interactive clarification
        if self.clarification_gateway is not None:
            pending_clarifications = self.clarification_gateway.get_pending_requests(goal_id=goal_id)
            if pending_clarifications:
                return GoalEvaluationResult(
                    is_completed=False,
                    action_needed=False,
                    new_progress=goal.progress,
                    rationale=f"Goal paused awaiting user clarification: {pending_clarifications[0].question}",
                )

        # 5. Transition to EVALUATING
        eval_goal = goal.with_status(GoalStatus.EVALUATING)
        if matched_trigger:
            eval_goal = eval_goal.with_updated_trigger(matched_trigger.trigger_id)
        self.goal_store.update(eval_goal)

        # 6. Invoke GoalReasoner with persistent observations
        obs_list = self.get_observations(goal_id)

        # Check if there is a paused task awaiting continuation
        resuming_plan = None
        if self.state_store is not None and eval_goal.executed_task_ids:
            last_tid = eval_goal.executed_task_ids[-1]
            if self.state_store.exists(last_tid):
                last_state = self.state_store.get(last_tid)
                if last_state.status == TaskStatus.PAUSED and last_state.plan:
                    resuming_plan = last_state.plan

        if resuming_plan is not None:
            eval_res = GoalEvaluationResult(
                is_completed=False,
                action_needed=True,
                new_progress=eval_goal.progress,
                proposed_plan=resuming_plan,
                rationale=f"Resuming paused task for goal '{goal_id}'.",
            )
        else:
            eval_res = self.reasoner.evaluate(eval_goal, observations=obs_list)

        # Stagnation Monitoring & Convergence Evaluation (M16)
        stag_report = self.stagnation_monitor.check_stagnation(
            eval_goal,
            current_progress_percentage=eval_res.new_progress.percentage,
        )
        if stag_report.should_abandon_goal:
            logger.warning("Goal '%s' irrecoverably stagnant, marking FAILED.", goal_id)
            abandoned_goal = eval_goal.with_progress(
                progress=eval_res.new_progress,
                status=GoalStatus.FAILED,
                evaluation_count=eval_goal.evaluation_count + 1,
            )
            self.goal_store.update(abandoned_goal)
            return GoalEvaluationResult(
                is_completed=False,
                action_needed=False,
                new_progress=eval_res.new_progress,
                rationale=stag_report.diagnostic_summary,
            )

        # 7. Check Sub-Goal Completion Constraint (M12)
        # Parent goals cannot complete until all sub-goals are completed
        subgoals_pending = False
        subgoal_failure_msg = None
        if eval_goal.subgoal_ids:
            for sub_id in eval_goal.subgoal_ids:
                if self.goal_store.exists(sub_id):
                    sub_goal = self.goal_store.get(sub_id)
                    if sub_goal.status in (GoalStatus.FAILED, GoalStatus.CANCELLED):
                        subgoal_failure_msg = f"Sub-goal '{sub_id}' failed with status '{sub_goal.status}'."
                    elif sub_goal.status != GoalStatus.COMPLETED:
                        subgoals_pending = True

        if subgoal_failure_msg:
            blocked_goal = eval_goal.with_progress(
                progress=eval_res.new_progress,
                status=GoalStatus.BLOCKED,
                evaluation_count=eval_goal.evaluation_count + 1,
            )
            self.goal_store.update(blocked_goal)
            return GoalEvaluationResult(
                is_completed=False,
                action_needed=False,
                new_progress=eval_res.new_progress,
                rationale=subgoal_failure_msg,
            )

        # 8. Process evaluation outcome
        if eval_res.is_completed:
            if subgoals_pending:
                # Direct criteria are met, but subgoals are still in progress
                waiting_goal = eval_goal.with_progress(
                    progress=eval_res.new_progress,
                    status=GoalStatus.ACTIVE,
                    evaluation_count=eval_goal.evaluation_count + 1,
                )
                self.goal_store.update(waiting_goal)
                return GoalEvaluationResult(
                    is_completed=False,
                    action_needed=False,
                    new_progress=eval_res.new_progress,
                    rationale="Direct criteria satisfied; awaiting completion of child sub-goals.",
                )

            completed_goal = eval_goal.with_progress(
                progress=eval_res.new_progress,
                status=GoalStatus.COMPLETED,
                evaluation_count=eval_goal.evaluation_count + 1,
            )
            self.goal_store.update(completed_goal)
            return eval_res

        # 9. If action needed, execute proposed plan via AutonomousAgentExecutor with lineage tracking
        if eval_res.action_needed and eval_res.proposed_plan is not None:
            if eval_goal.action_count >= self.config.max_actions_per_goal:
                logger.warning("Goal '%s' exceeded max_actions_per_goal limit.", goal_id)
                failed_goal = eval_goal.with_progress(
                    progress=eval_res.new_progress,
                    status=GoalStatus.FAILED,
                    evaluation_count=eval_goal.evaluation_count + 1,
                )
                self.goal_store.update(failed_goal)
                return GoalEvaluationResult(
                    is_completed=False,
                    action_needed=False,
                    new_progress=eval_res.new_progress,
                    rationale=f"Max actions limit reached ({self.config.max_actions_per_goal}).",
                )

            if self.executor is None:
                # No executor available to run actions
                updated_goal = eval_goal.with_progress(
                    progress=eval_res.new_progress,
                    status=GoalStatus.ACTION_REQUIRED,
                    evaluation_count=eval_goal.evaluation_count + 1,
                )
                self.goal_store.update(updated_goal)
                return eval_res

            # Transition to EXECUTING
            executing_goal = eval_goal.with_status(GoalStatus.EXECUTING)
            self.goal_store.update(executing_goal)

            # Check if resuming an existing paused task
            if (
                self.state_store is not None
                and eval_goal.executed_task_ids
                and self.state_store.exists(eval_goal.executed_task_ids[-1])
                and self.state_store.get(eval_goal.executed_task_ids[-1]).status == TaskStatus.PAUSED
            ):
                task_id = eval_goal.executed_task_ids[-1]
            else:
                task_id = f"goal_{goal_id}_act_{executing_goal.action_count + 1}"

            # Check if plan requires interactive clarification (M16/M17)
            plan_meta = dict(eval_res.proposed_plan.metadata)
            first_step = eval_res.proposed_plan.steps[0] if eval_res.proposed_plan.steps else None
            is_clarification_step = (
                plan_meta.get("requires_interactive_clarification") is True
                or (first_step and first_step.skill_name == "request_user_clarification")
            )
            if is_clarification_step and self.clarification_gateway is not None:
                q_text = plan_meta.get("clarification_question") or (first_step.objective if first_step else "Clarification needed.")
                opts = plan_meta.get("clarification_options", ("retry", "abort", "custom"))
                clarif_req = self.clarification_gateway.request_clarification(
                    goal_id=goal_id,
                    task_id=task_id,
                    question=q_text,
                    options=list(opts),
                )
                paused_goal = executing_goal.with_progress(
                    progress=eval_res.new_progress,
                    status=GoalStatus.PAUSED,
                    evaluation_count=executing_goal.evaluation_count + 1,
                    action_count=executing_goal.action_count,
                )
                self.goal_store.update(paused_goal)
                return GoalEvaluationResult(
                    is_completed=False,
                    action_needed=False,
                    new_progress=eval_res.new_progress,
                    rationale=f"Action paused awaiting interactive clarification: {clarif_req.question}",
                )

            # Check Resource Budget Quota (M17)
            if self.budget_manager is not None:
                alloc_res = self.budget_manager.acquire_quota(goal_id=goal_id)
                if not alloc_res.is_granted:
                    logger.warning("Goal '%s' execution throttled by resource budget: %s", goal_id, alloc_res.reason)
                    throttled_goal = executing_goal.with_progress(
                        progress=eval_res.new_progress,
                        status=GoalStatus.ACTIVE,
                        evaluation_count=executing_goal.evaluation_count + 1,
                    )
                    self.goal_store.update(throttled_goal)
                    return GoalEvaluationResult(
                        is_completed=False,
                        action_needed=False,
                        new_progress=eval_res.new_progress,
                        rationale=f"Execution throttled by resource budget: {alloc_res.reason}",
                    )

            # Check Resource Locks (M17)
            required_resources = plan_meta.get("required_resources", ())
            if self.lock_manager is not None and required_resources:
                lock_res = self.lock_manager.acquire_locks_batch(
                    resource_uris=required_resources,
                    goal_id=goal_id,
                    lock_type=LockType.EXCLUSIVE_WRITE,
                )
                if not lock_res.success:
                    logger.warning("Goal '%s' execution paused due to lock contention: %s", goal_id, lock_res.reason)
                    if self.budget_manager is not None:
                        self.budget_manager.release_quota(goal_id=goal_id)
                    lock_paused_goal = executing_goal.with_progress(
                        progress=eval_res.new_progress,
                        status=GoalStatus.ACTIVE,
                        evaluation_count=executing_goal.evaluation_count + 1,
                    )
                    self.goal_store.update(lock_paused_goal)
                    return GoalEvaluationResult(
                        is_completed=False,
                        action_needed=False,
                        new_progress=eval_res.new_progress,
                        rationale=f"Execution deferred due to lock contention: {lock_res.reason}",
                    )

            # Attach goal lineage metadata to plan
            plan_meta["goal_id"] = goal_id
            if goal.parent_goal_id:
                plan_meta["parent_goal_id"] = goal.parent_goal_id

            plan_with_lineage = AgentPlan(
                plan_id=eval_res.proposed_plan.plan_id,
                task_goal=eval_res.proposed_plan.task_goal,
                steps=eval_res.proposed_plan.steps,
                status=eval_res.proposed_plan.status,
                created_at=eval_res.proposed_plan.created_at,
                updated_at=eval_res.proposed_plan.updated_at,
                metadata=plan_meta,
            )

            # Record task ID in Goal lineage (M12)
            executing_goal = executing_goal.with_executed_task(task_id)
            self.goal_store.update(executing_goal)

            agent_res = self.executor.execute_plan(
                plan=plan_with_lineage,
                task_id=task_id,
                task_description=plan_with_lineage.task_goal,
            )

            # Handle execution pauses (e.g. approval required)
            if agent_res.is_paused:
                paused_goal = executing_goal.with_progress(
                    progress=eval_res.new_progress,
                    status=GoalStatus.PAUSED,
                    evaluation_count=executing_goal.evaluation_count + 1,
                    action_count=executing_goal.action_count,
                )
                self.goal_store.update(paused_goal)
                return GoalEvaluationResult(
                    is_completed=False,
                    action_needed=False,
                    new_progress=eval_res.new_progress,
                    rationale=f"Action paused awaiting approval: {agent_res.error}",
                )

            # Release locks and record resource consumption (M17)
            if self.lock_manager is not None and required_resources:
                self.lock_manager.release_all_locks_for_goal(goal_id)
            if self.budget_manager is not None:
                self.budget_manager.release_quota(goal_id=goal_id, actual_tool_calls=1)

            # Record action outcome as persistent observation
            out_val = agent_res.final_output if agent_res.success else agent_res.error
            is_untrusted_out = is_tainted(out_val) or any(o.is_untrusted for o in agent_res.trace.observations)
            crit_name = plan_meta.get("criterion", "")
            self.add_observation(
                goal_id=goal_id,
                source=f"action:{crit_name}:{task_id}" if crit_name else f"action:{task_id}",
                data=out_val,
                is_untrusted=is_untrusted_out,
                metadata={
                    "criterion": crit_name,
                    "task_id": task_id,
                    "success": agent_res.success,
                },
            )

            # Record Strategy Lineage / Heuristic Outcome (M16)
            self.goal_adapter.record_adaptation_outcome(
                goal_id=goal_id,
                plan_id=plan_with_lineage.plan_id,
                success=agent_res.success,
                failure_category=str(agent_res.error) if not agent_res.success else None,
                rationale=f"Executed task {task_id}",
            )

            # Trigger Goal Adaptation upon failure (M16)
            if not agent_res.success:
                fail_obs = self.get_observations(goal_id)
                adapt_res = self.goal_adapter.adapt_goal_plan(
                    goal=executing_goal,
                    failed_plan=plan_with_lineage,
                    error_message=str(agent_res.error or "Action execution failed"),
                    observations=fail_obs,
                )
                if adapt_res.should_abandon:
                    abandoned_goal = executing_goal.with_progress(
                        progress=eval_res.new_progress,
                        status=GoalStatus.FAILED,
                        evaluation_count=executing_goal.evaluation_count + 1,
                        action_count=executing_goal.action_count + 1,
                    )
                    self.goal_store.update(abandoned_goal)
                    return GoalEvaluationResult(
                        is_completed=False,
                        action_needed=False,
                        new_progress=eval_res.new_progress,
                        rationale=adapt_res.rationale,
                    )

            # Re-evaluate progress after action
            updated_obs = self.get_observations(goal_id)
            post_eval = self.reasoner.evaluate(executing_goal, observations=updated_obs)

            final_status = (
                GoalStatus.COMPLETED
                if post_eval.is_completed and not subgoals_pending
                else GoalStatus.PROGRESS_UPDATED
            )

            final_goal = executing_goal.with_progress(
                progress=post_eval.new_progress,
                status=final_status,
                evaluation_count=executing_goal.evaluation_count + 1,
                action_count=executing_goal.action_count + 1,
            )
            self.goal_store.update(final_goal)
            return post_eval

        # No action needed, return to ACTIVE with updated progress
        active_goal = eval_goal.with_progress(
            progress=eval_res.new_progress,
            status=GoalStatus.ACTIVE,
            evaluation_count=eval_goal.evaluation_count + 1,
        )
        self.goal_store.update(active_goal)
        return eval_res

    def evaluate_all_active_goals(self) -> list[GoalEvaluationResult]:
        """Evaluate all active goals in topological dependency order."""
        active_goals = self.list_goals(status=GoalStatus.ACTIVE)
        if not active_goals:
            return []

        # Order topologically: subgoals before parents, dependencies before dependents
        ordered = get_topological_evaluation_order(active_goals)
        results: list[GoalEvaluationResult] = []
        for g in ordered:
            # Re-fetch state in case earlier evaluations altered this goal
            if self.goal_store.exists(g.goal_id):
                curr = self.goal_store.get(g.goal_id)
                if curr.status == GoalStatus.ACTIVE:
                    res = self.evaluate_goal(curr.goal_id)
                    results.append(res)

        return results

    def step_batch_scheduled(self, max_batch_size: int = 4) -> list[GoalEvaluationResult]:
        """Evaluate the next batch of queued goals via MultiGoalScheduler."""
        if self.scheduler is None:
            self.scheduler = MultiGoalScheduler(
                budget_manager=self.budget_manager,
                lock_manager=self.lock_manager,
            )
        # Enqueue active goals to scheduler
        for g in self.list_goals(status=GoalStatus.ACTIVE):
            self.scheduler.schedule_goal(g.goal_id, priority=g.priority)
        return self.scheduler.step_next_batch(self, max_batch_size=max_batch_size)
