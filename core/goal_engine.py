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
)
from core.goal_reasoner import GoalEvaluationResult, GoalReasoner
from core.goal_store import GoalStore, InMemoryGoalStore
from core.policy import Policy
from core.provenance import TaintedValue, is_tainted, wrap_tainted
from core.skill_registry import SkillRegistry
from core.task_state_store import TaskStateStore

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


class GoalEngine:
    """Proactive Goal Engine managing goal lifecycles, triggers, evaluation, and safe action execution."""

    def __init__(
        self,
        goal_store: GoalStore | None = None,
        reasoner: GoalReasoner | None = None,
        executor: AutonomousAgentExecutor | None = None,
        runtime: AgentRuntime | None = None,
        state_store: TaskStateStore | None = None,
        approval_gateway: ApprovalGateway | None = None,
        config: GoalEngineConfig | None = None,
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

        self.goal_store = goal_store if goal_store is not None else InMemoryGoalStore()
        self.state_store = state_store
        self.approval_gateway = approval_gateway

        self.config = config if config is not None else GoalEngineConfig(
            max_active_goals=getattr(settings, "aura_max_active_goals", 10),
            max_evaluations_per_goal=getattr(settings, "aura_max_evaluations_per_goal", 50),
            max_actions_per_goal=getattr(settings, "aura_max_actions_per_goal", 20),
            evaluation_cooldown_seconds=getattr(settings, "aura_goal_cooldown_seconds", 5.0),
            goal_timeout_seconds=getattr(settings, "aura_goal_timeout_seconds", 3600.0),
        )

        self.runtime = runtime
        if self.runtime is None and executor is not None:
            self.runtime = executor.runtime

        self.reasoner = reasoner if reasoner is not None else GoalReasoner(
            skill_registry=self.runtime.skill_registry if self.runtime else None,
        )

        self.executor = executor if executor is not None else (
            AutonomousAgentExecutor(
                runtime=self.runtime,
                state_store=self.state_store,
                approval_gateway=self.approval_gateway,
            )
            if self.runtime is not None
            else None
        )

        # In-memory goal observations cache: goal_id -> list[GoalObservation]
        self._observations: dict[str, list[GoalObservation]] = {}

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
    ) -> Goal:
        """Create and store a new Goal entity."""
        active_goals = self.goal_store.list_goals(status=GoalStatus.ACTIVE)
        if len(active_goals) >= self.config.max_active_goals:
            raise ValueError(
                f"Active goals limit reached ({self.config.max_active_goals}). "
                "Complete, pause, or cancel existing goals before creating new ones."
            )

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
        )

        stored_goal = self.goal_store.create(goal)
        self._observations[stored_goal.goal_id] = []
        return stored_goal

    def get_goal(self, goal_id: str) -> Goal:
        """Retrieve a Goal by ID."""
        return self.goal_store.get(goal_id)

    def list_goals(
        self,
        status: GoalStatus | None = None,
        priority: GoalPriority | None = None,
    ) -> list[Goal]:
        """List all goals matching filters."""
        return self.goal_store.list_goals(status=status, priority=priority)

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
        """Ingest a contextual observation for a goal."""
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

        if goal_id not in self._observations:
            self._observations[goal_id] = []
        self._observations[goal_id].append(obs)
        return obs

    def get_observations(self, goal_id: str) -> list[GoalObservation]:
        """Retrieve all observations associated with a goal."""
        if not self.goal_store.exists(goal_id):
            raise KeyError(f"Goal '{goal_id}' not found.")
        return list(self._observations.get(goal_id, []))

    def evaluate_goal(
        self,
        goal_id: str,
        trigger_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> GoalEvaluationResult:
        """Evaluate a goal's progress and execute proactive actions if needed."""
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

        # 3. Trigger check (if specific trigger passed)
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

        # 4. Transition to EVALUATING
        eval_goal = goal.with_status(GoalStatus.EVALUATING)
        if matched_trigger:
            eval_goal = eval_goal.with_updated_trigger(matched_trigger.trigger_id)
        self.goal_store.update(eval_goal)

        # 5. Invoke GoalReasoner
        obs_list = self.get_observations(goal_id)
        eval_res = self.reasoner.evaluate(eval_goal, observations=obs_list)

        # 6. Process evaluation outcome
        if eval_res.is_completed:
            completed_goal = eval_goal.with_progress(
                progress=eval_res.new_progress,
                status=GoalStatus.COMPLETED,
                evaluation_count=eval_goal.evaluation_count + 1,
            )
            self.goal_store.update(completed_goal)
            return eval_res

        # 7. If action needed, execute proposed plan via AutonomousAgentExecutor
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

            task_id = f"goal_{goal_id}_act_{executing_goal.action_count + 1}"
            agent_res = self.executor.execute_plan(
                plan=eval_res.proposed_plan,
                task_id=task_id,
                task_description=eval_res.proposed_plan.task_goal,
            )

            # Record action outcome as observation
            out_val = agent_res.final_output if agent_res.success else agent_res.error
            is_untrusted_out = is_tainted(out_val) or any(o.is_untrusted for o in agent_res.trace.observations)
            crit_name = eval_res.proposed_plan.metadata.get("criterion", "") if eval_res.proposed_plan else ""
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

            # Handle execution pauses (e.g. approval required)
            if agent_res.is_paused:
                paused_goal = executing_goal.with_progress(
                    progress=eval_res.new_progress,
                    status=GoalStatus.PAUSED,
                    evaluation_count=executing_goal.evaluation_count + 1,
                    action_count=executing_goal.action_count + 1,
                )
                self.goal_store.update(paused_goal)
                return GoalEvaluationResult(
                    is_completed=False,
                    action_needed=False,
                    new_progress=eval_res.new_progress,
                    rationale=f"Action paused awaiting approval: {agent_res.error}",
                )

            # Re-evaluate progress after action
            updated_obs = self.get_observations(goal_id)
            post_eval = self.reasoner.evaluate(executing_goal, observations=updated_obs)

            final_status = (
                GoalStatus.COMPLETED
                if post_eval.is_completed
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
