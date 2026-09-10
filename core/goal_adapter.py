"""Goal Execution Plan Adapter & Self-Healing Engine (M16).

Dynamically synthesizes adapted execution plans in response to goal failures,
stagnation reports, and failure diagnoses. Employs MetaPolicyEngine strategy
selection, integrates empirical heuristic calibration, records strategy lineage,
and strictly enforces non-authorizing security boundaries.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Sequence

from app.config import settings
from core.agent_plan import (
    AgentPlan,
    AgentPlanStep,
    StepStatus,
)
from core.goal import Goal, GoalObservation, GoalStatus
from core.heuristic_calibrator import HeuristicCalibrator
from core.lifecycle_types import RuleStatus
from core.meta_policy import MetaPolicyEngine
from core.provenance import (
    TaintedValue,
    is_tainted,
    render_for_prompt,
    unwrap_tainted,
    wrap_tainted,
)
from core.reflection_types import (
    FailureIssueType,
    ReflectionAssessment,
    ReflectionRecord,
)
from core.strategy_lineage import StrategyLineageStore
from core.clarification_gateway import ClarificationGateway
from core.scheduling_types import ClarificationType
from core.strategy_types import (
    GoalStrategyRecord,
    StrategyAttempt,
    StrategyAttemptOutcome,
    StrategyType,
    strip_forbidden_metadata_keys,
)
from core.task_planner import ReplanContext, TaskPlanner

logger = logging.getLogger("aura.goal_adapter")


@dataclass(frozen=True)
class GoalAdaptationResult:
    """Outcome of adapting a goal's execution plan."""

    success: bool
    goal_id: str
    selected_strategy: StrategyType
    adapted_plan: AgentPlan | None = None
    adapted_team_binding: Any | None = None
    attempt_number: int = 1
    rationale: str = ""
    is_stagnant: bool = False
    should_abandon: bool = False
    heuristics_applied: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.success, bool):
            raise TypeError("success must be a boolean.")
        if not isinstance(self.goal_id, str) or not self.goal_id.strip():
            raise ValueError("goal_id must be a non-empty string.")
        object.__setattr__(self, "goal_id", self.goal_id.strip())

        if isinstance(self.selected_strategy, str):
            object.__setattr__(self, "selected_strategy", StrategyType(self.selected_strategy))
        elif not isinstance(self.selected_strategy, StrategyType):
            raise TypeError("selected_strategy must be a StrategyType instance.")

        if self.adapted_plan is not None and not isinstance(self.adapted_plan, AgentPlan):
            raise TypeError("adapted_plan must be an AgentPlan instance or None.")

        if self.adapted_team_binding is not None:
            from core.goal_team_binding import GoalTeamBinding
            if not isinstance(self.adapted_team_binding, GoalTeamBinding):
                raise TypeError("adapted_team_binding must be a GoalTeamBinding instance or None.")

        object.__setattr__(self, "attempt_number", max(1, int(self.attempt_number)))
        object.__setattr__(self, "rationale", str(self.rationale or "").strip())
        object.__setattr__(self, "is_stagnant", bool(self.is_stagnant))
        object.__setattr__(self, "should_abandon", bool(self.should_abandon))

        if isinstance(self.heuristics_applied, (list, tuple)):
            object.__setattr__(self, "heuristics_applied", tuple(str(h) for h in self.heuristics_applied))
        else:
            raise TypeError("heuristics_applied must be a tuple/list of strings.")

        object.__setattr__(self, "metadata", strip_forbidden_metadata_keys(self.metadata))


class GoalAdapter:
    """Adapts goal plans dynamically upon execution failure, stagnation, or environmental feedback."""

    def __init__(
        self,
        meta_policy: MetaPolicyEngine | None = None,
        lineage_store: StrategyLineageStore | None = None,
        planner: TaskPlanner | None = None,
        heuristic_calibrator: HeuristicCalibrator | None = None,
        max_strategy_retries: int | None = None,
        clarification_gateway: ClarificationGateway | None = None,
        role_registry: Any | None = None,
    ) -> None:
        self.clarification_gateway = clarification_gateway
        self.role_registry = role_registry
        self.lineage_store = lineage_store or StrategyLineageStore()
        self.heuristic_calibrator = heuristic_calibrator
        self.meta_policy = meta_policy or MetaPolicyEngine(
            lineage_store=self.lineage_store,
            calibrator=self.heuristic_calibrator,
        )
        if meta_policy is not None:
            if self.meta_policy.lineage_store is None:
                self.meta_policy.lineage_store = self.lineage_store
            if self.meta_policy.calibrator is None and self.heuristic_calibrator is not None:
                self.meta_policy.calibrator = self.heuristic_calibrator

        self.planner = planner
        self.max_strategy_retries = (
            max_strategy_retries
            if max_strategy_retries is not None
            else getattr(settings, "aura_max_strategy_retries", 3)
        )

    def _extract_failure_info(
        self,
        failed_plan: AgentPlan | None,
        error_message: str | None,
        reflection: ReflectionRecord | ReflectionAssessment | None,
    ) -> tuple[str, str | None, str | None]:
        """Extract sanitized failure diagnosis, category, and failed step id."""
        failed_step_id = None
        failure_category = None
        error_text = error_message or ""

        if reflection is not None:
            assessment = reflection.assessment if isinstance(reflection, ReflectionRecord) else reflection
            if assessment.failure_issue_type != FailureIssueType.NONE:
                failure_category = assessment.failure_issue_type.value
            if assessment.root_cause and not error_text:
                error_text = assessment.root_cause

        if failed_plan is not None:
            for step in failed_plan.steps:
                if step.status == StepStatus.FAILED:
                    failed_step_id = step.step_id
                    if not error_text and step.metadata.get("error"):
                        error_text = str(step.metadata.get("error"))
                    break

        if not error_text:
            error_text = "Step execution failed."

        return error_text, failure_category, failed_step_id

    def _get_promoted_heuristics(self) -> list[str]:
        """Collect active/promoted heuristic guidance, strictly excluding deprecated rules."""
        if not self.heuristic_calibrator:
            return []

        promoted = self.heuristic_calibrator.list_promoted_rules()
        guidance_list = []
        for r in promoted:
            if self.heuristic_calibrator.is_rule_usable(r.rule_id):
                guidance_list.append(f"{r.rule_id}: {r.trigger_condition}")
        return guidance_list

    def adapt_goal_plan(
        self,
        goal: Goal,
        failed_plan: AgentPlan | None = None,
        error_message: str | None = None,
        reflection: ReflectionRecord | ReflectionAssessment | None = None,
        observations: Sequence[GoalObservation] | None = None,
        has_new_observation_evidence: bool = False,
        available_skills: Sequence[str] | None = None,
        task_description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GoalAdaptationResult:
        """Adapt a goal execution plan using MetaPolicyEngine, heuristics, and lineage store."""
        if not isinstance(goal, Goal):
            raise TypeError("goal must be a Goal instance.")

        goal_id = goal.goal_id
        error_text, failure_category, failed_step_id = self._extract_failure_info(
            failed_plan=failed_plan,
            error_message=error_message,
            reflection=reflection,
        )

        # 1. Fetch current lineage and check retry thresholds
        record = self.lineage_store.get_record(goal_id)
        current_strategy = record.active_strategy if record else None
        failed_attempts = record.attempts if record else ()

        # Check if goal has exceeded global retry bounds
        total_failures = len([a for a in failed_attempts if a.outcome == StrategyAttemptOutcome.FAILURE])
        if total_failures >= (self.max_strategy_retries * len(StrategyType)):
            return GoalAdaptationResult(
                success=False,
                goal_id=goal_id,
                selected_strategy=current_strategy or StrategyType.DIRECT_SKILL,
                adapted_plan=None,
                attempt_number=len(failed_attempts) + 1,
                rationale=f"Goal '{goal_id}' exceeded maximum strategy retries across all modalities ({total_failures} failures).",
                should_abandon=True,
                metadata=dict(metadata or {}),
            )

        # 2. Select next strategy via MetaPolicyEngine
        decision = self.meta_policy.select_strategy(
            goal=goal,
            failure_category=failure_category,
            has_new_observation_evidence=has_new_observation_evidence,
            context={"error_message": error_text, "observations": observations},
        )
        selected_strategy = decision.selected_strategy

        # 3. Retrieve promoted heuristic guidance
        applied_heuristics = self._get_promoted_heuristics()

        # 4. Synthesize adapted plan
        # 4. Synthesize adapted plan and team binding if applicable
        task_str = task_description or (failed_plan.task_goal if failed_plan else goal.title)
        attempt_num = (failed_attempts[-1].attempt_number + 1) if failed_attempts else 1

        adapted_team_binding = None
        if selected_strategy in (StrategyType.MULTI_AGENT_TEAM_COLLABORATION, StrategyType.TEAM_CONSENSUS_DELIBERATION):
            from core.goal_team_binding import GoalTeamBinding, GoalTeamBindingStatus
            from core.team_types import TeamDefinition, TeamMember, TeamTopology
            if selected_strategy == StrategyType.TEAM_CONSENSUS_DELIBERATION:
                topology = TeamTopology.CONSENSUS_VOTING
            elif isinstance(goal.execution_topology, TeamTopology):
                topology = goal.execution_topology
            elif isinstance(goal.execution_topology, str):
                try:
                    topology = TeamTopology(goal.execution_topology)
                except Exception:
                    topology = TeamTopology.HIERARCHICAL
            else:
                topology = TeamTopology.HIERARCHICAL

            assigned_roles: list[str] = []
            if goal.assigned_role_id:
                assigned_roles.append(goal.assigned_role_id)
            if self.role_registry is not None and hasattr(self.role_registry, "list_roles"):
                try:
                    roles = self.role_registry.list_roles()
                    for r in roles:
                        if r.role_id not in assigned_roles:
                            assigned_roles.append(r.role_id)
                except Exception:
                    pass

            if not assigned_roles:
                assigned_roles = ["general_assistant"]

            members = []
            for i, r in enumerate(assigned_roles):
                is_lead = (r == goal.assigned_role_id) if goal.assigned_role_id else (i == 0)
                members.append(TeamMember(role_id=r, is_lead=is_lead))

            team_def = TeamDefinition(
                team_id=goal.assigned_team_id or f"team_{goal_id}",
                name=f"Team for {goal.title}",
                members=members,
                topology=topology,
            )
            adapted_team_binding = GoalTeamBinding(
                binding_id=f"binding_{goal_id}_{int(time.time())}",
                goal_id=goal_id,
                team_definition=team_def,
                task_description=f"Multi-agent adaptation for goal '{goal.title}'. Previous error: {error_text}",
                status=GoalTeamBindingStatus.BOUND,
            )

        adapted_plan = self._synthesize_adapted_plan(
            goal=goal,
            task=task_str,
            selected_strategy=selected_strategy,
            failed_plan=failed_plan,
            failed_step_id=failed_step_id or "step_1",
            error_message=error_text,
            applied_heuristics=applied_heuristics,
            available_skills=available_skills,
            attempt_number=attempt_num,
        )

        # 5. Record attempt in lineage store
        attempt = StrategyAttempt(
            strategy_id=f"strat-{uuid.uuid4().hex[:8]}",
            goal_id=goal_id,
            strategy_type=selected_strategy,
            attempt_number=attempt_num,
            plan_id=adapted_plan.plan_id if adapted_plan else None,
            outcome=StrategyAttemptOutcome.FAILURE,  # Marked as failure pending execution outcome
            failure_category=failure_category,
            rationale=decision.rationale,
            metadata={
                "heuristics_applied": applied_heuristics,
                "strategy_confidence": decision.confidence,
            },
        )
        self.lineage_store.record_attempt(goal_id, attempt)

        clean_meta = strip_forbidden_metadata_keys(metadata or {})
        clean_meta["meta_policy_confidence"] = decision.confidence

        return GoalAdaptationResult(
            success=True,
            goal_id=goal_id,
            selected_strategy=selected_strategy,
            adapted_plan=adapted_plan,
            adapted_team_binding=adapted_team_binding,
            attempt_number=attempt_num,
            rationale=decision.rationale,
            is_stagnant=False,
            should_abandon=False,
            heuristics_applied=tuple(applied_heuristics),
            metadata=clean_meta,
        )

    adapt_goal = adapt_goal_plan

    def _synthesize_adapted_plan(
        self,
        goal: Goal,
        task: str,
        selected_strategy: StrategyType,
        failed_plan: AgentPlan | None,
        failed_step_id: str,
        error_message: str,
        applied_heuristics: list[str],
        available_skills: Sequence[str] | None,
        attempt_number: int,
    ) -> AgentPlan:
        """Synthesize an AgentPlan tailored to the selected strategy modality."""
        plan_id = f"adapted-plan-{uuid.uuid4().hex[:8]}"

        # If planner is available and supports replanning, try using planner
        if self.planner is not None and hasattr(self.planner, "replan_agent") and failed_plan is not None:
            completed_steps = [s.step_id for s in failed_plan.steps if s.status == StepStatus.SUCCEEDED]
            replan_ctx = ReplanContext(
                task=f"{task} (Strategy: {selected_strategy.value})",
                failed_step_id=failed_step_id,
                error_message=error_message,
                completed_steps=tuple(completed_steps),
                step_outputs={},
                original_plan_id=failed_plan.plan_id,
                metadata={
                    "strategy_type": selected_strategy.value,
                    "promoted_heuristics": applied_heuristics,
                },
            )
            try:
                plan = self.planner.replan_agent(replan_ctx, plan_id=plan_id)
                # Attach goal lineage and strategy metadata
                meta = dict(plan.metadata)
                meta["goal_id"] = goal.goal_id
                meta["strategy_type"] = selected_strategy.value
                meta["attempt_number"] = attempt_number
                return AgentPlan(
                    plan_id=plan.plan_id,
                    task_goal=plan.task_goal,
                    steps=plan.steps,
                    metadata=meta,
                )
            except Exception as e:
                logger.warning("TaskPlanner replan_agent failed, falling back to deterministic synthesis: %s", e)

        # Deterministic Plan Synthesis based on Strategy Type
        steps: list[AgentPlanStep] = []

        if selected_strategy == StrategyType.DIRECT_SKILL:
            skill = "execute_goal_action"
            if available_skills:
                skill = available_skills[0]
            steps.append(
                AgentPlanStep(
                    step_id="step_1",
                    skill_name=skill,
                    objective=f"Directly execute action for goal: {goal.title}",
                    input_data={"goal_id": goal.goal_id, "retry_attempt": attempt_number},
                    metadata={"strategy": selected_strategy.value},
                )
            )

        elif selected_strategy == StrategyType.DECOMPOSED_HIERARCHICAL:
            steps.extend([
                AgentPlanStep(
                    step_id="step_1",
                    skill_name="validate_prerequisites",
                    objective=f"Validate preconditions and inputs for {goal.title}",
                    input_data={"goal_id": goal.goal_id},
                    metadata={"strategy": selected_strategy.value},
                ),
                AgentPlanStep(
                    step_id="step_2",
                    skill_name="execute_decomposed_action",
                    objective=f"Execute core action for {goal.title}",
                    dependencies=("step_1",),
                    input_data={"goal_id": goal.goal_id},
                    metadata={"strategy": selected_strategy.value},
                ),
                AgentPlanStep(
                    step_id="step_3",
                    skill_name="verify_goal_progress",
                    objective=f"Verify outputs and state progress for {goal.title}",
                    dependencies=("step_2",),
                    input_data={"goal_id": goal.goal_id},
                    metadata={"strategy": selected_strategy.value},
                ),
            ])

        elif selected_strategy == StrategyType.RESEARCH_ASSISTED_SYNTHESIS:
            steps.extend([
                AgentPlanStep(
                    step_id="step_1",
                    skill_name="research_context",
                    objective=f"Gather prerequisite knowledge and documentation for {goal.title}",
                    input_data={"query": goal.title, "error_context": error_message},
                    metadata={"strategy": selected_strategy.value},
                ),
                AgentPlanStep(
                    step_id="step_2",
                    skill_name="execute_informed_action",
                    objective=f"Execute informed action using gathered context for {goal.title}",
                    dependencies=("step_1",),
                    input_data={"goal_id": goal.goal_id},
                    metadata={"strategy": selected_strategy.value},
                ),
            ])

        elif selected_strategy == StrategyType.FALLBACK_TOOL_ROUTING:
            skill = "fallback_tool_executor"
            if available_skills and len(available_skills) > 1:
                skill = available_skills[1]
            steps.append(
                AgentPlanStep(
                    step_id="step_1",
                    skill_name=skill,
                    objective=f"Execute alternate tool pathway for {goal.title}",
                    input_data={"goal_id": goal.goal_id, "fallback_mode": True, "prior_error": error_message},
                    metadata={"strategy": selected_strategy.value, "routed_skill": skill},
                )
            )

        elif selected_strategy == StrategyType.HUMAN_INTERACTIVE_CLARIFICATION:
            q_text = f"Goal '{goal.title}' failed with error: {error_message}. How to proceed?"
            opts = ("retry_with_decomposition", "abort_goal", "provide_custom_input")
            if self.clarification_gateway is not None:
                self.clarification_gateway.request_clarification(
                    goal_id=goal.goal_id,
                    task_id=f"goal_{goal.goal_id}_clarification",
                    question=q_text,
                    options=list(opts),
                    clarification_type=ClarificationType.SINGLE_CHOICE,
                )
            steps.append(
                AgentPlanStep(
                    step_id="step_1",
                    skill_name="request_user_clarification",
                    objective=f"Request user clarification for {goal.title}",
                    input_data={"goal_id": goal.goal_id, "question": q_text, "options": list(opts)},
                    metadata={
                        "strategy": selected_strategy.value,
                        "interactive": True,
                        "requires_interactive_clarification": True,
                        "clarification_question": q_text,
                        "clarification_options": opts,
                    },
                )
            )

        elif selected_strategy == StrategyType.MULTI_AGENT_TEAM_COLLABORATION:
            steps.append(
                AgentPlanStep(
                    step_id="step_team_collab_1",
                    skill_name="team_orchestration_step",
                    objective=f"Execute multi-agent team collaboration for {goal.title}",
                    input_data={"goal_id": goal.goal_id, "strategy": selected_strategy.value},
                    metadata={"strategy": selected_strategy.value, "multi_agent": True},
                )
            )

        elif selected_strategy == StrategyType.TEAM_CONSENSUS_DELIBERATION:
            steps.append(
                AgentPlanStep(
                    step_id="step_team_consensus_1",
                    skill_name="team_consensus_step",
                    objective=f"Execute team consensus deliberation for {goal.title}",
                    input_data={"goal_id": goal.goal_id, "strategy": selected_strategy.value},
                    metadata={"strategy": selected_strategy.value, "consensus": True},
                )
            )

        return AgentPlan(
            plan_id=plan_id,
            task_goal=f"[{selected_strategy.value.upper()}] {task}",
            steps=tuple(steps),
            metadata={
                "goal_id": goal.goal_id,
                "strategy_type": selected_strategy.value,
                "attempt_number": attempt_number,
                "promoted_heuristics": applied_heuristics,
                "adapted": True,
            },
        )

    def record_adaptation_outcome(
        self,
        goal_id: str,
        plan_id: str,
        success: bool,
        failure_category: str | None = None,
        execution_cost: float = 0.0,
        rationale: str = "",
    ) -> StrategyAttempt | None:
        """Update outcome for an adapted plan execution attempt and calibrate heuristics."""
        record = self.lineage_store.get_record(goal_id)
        if not record or not record.attempts:
            return None

        # Find matching attempt or take the latest
        target_attempt = None
        for a in reversed(record.attempts):
            if a.plan_id == plan_id:
                target_attempt = a
                break
        if target_attempt is None:
            target_attempt = record.attempts[-1]

        outcome = StrategyAttemptOutcome.SUCCESS if success else StrategyAttemptOutcome.FAILURE
        updated_attempt = StrategyAttempt(
            strategy_id=target_attempt.strategy_id,
            goal_id=goal_id,
            strategy_type=target_attempt.strategy_type,
            attempt_number=target_attempt.attempt_number,
            plan_id=target_attempt.plan_id,
            subgoal_id=target_attempt.subgoal_id,
            outcome=outcome,
            failure_category=failure_category or target_attempt.failure_category,
            execution_cost=execution_cost or target_attempt.execution_cost,
            rationale=rationale or target_attempt.rationale,
            created_at=target_attempt.created_at,
            completed_at=time.time(),
            metadata=dict(target_attempt.metadata),
        )
        self.lineage_store.update_attempt(goal_id, updated_attempt)

        # If heuristics were used and we have a calibrator, record outcomes
        if self.heuristic_calibrator:
            heuristics = target_attempt.metadata.get("heuristics_applied", [])
            for h in heuristics:
                rule_id = str(h).split(":")[0].strip()
                self.heuristic_calibrator.record_outcome(
                    rule_id=rule_id,
                    success=success,
                    replanned=True,
                    execution_notes=f"Applied in strategy {target_attempt.strategy_type.value}",
                )

        return updated_attempt
