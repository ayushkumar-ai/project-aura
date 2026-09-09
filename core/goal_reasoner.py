import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from core.agent_plan import AgentPlan, AgentPlanStep, StepStatus
from core.capability_registry import ModelCapability
from core.goal import Goal, GoalObservation, GoalProgress, GoalStatus
from core.model_router import ModelRouter, TaskRequirements
from core.provenance import TaintedValue, is_tainted, render_for_prompt
from core.skill_registry import SkillRegistry
from interfaces.model import ModelInterface

logger = logging.getLogger("aura.goal_reasoner")


@dataclass(frozen=True)
class GoalEvaluationResult:
    """Outcome of evaluating a Goal against its current state and observations."""

    is_completed: bool
    action_needed: bool
    new_progress: GoalProgress
    proposed_plan: AgentPlan | None = None
    rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.is_completed, bool):
            raise TypeError("is_completed must be a boolean.")
        if not isinstance(self.action_needed, bool):
            raise TypeError("action_needed must be a boolean.")
        if not isinstance(self.new_progress, GoalProgress):
            raise TypeError("new_progress must be an instance of GoalProgress.")
        if self.proposed_plan is not None and not isinstance(self.proposed_plan, AgentPlan):
            raise TypeError("proposed_plan must be an instance of AgentPlan or None.")
        if not isinstance(self.rationale, str):
            raise TypeError("rationale must be a string.")


class GoalReasoner:
    """Proactive reasoning engine for assessing goal progress and formulating bounded actions."""

    def __init__(
        self,
        skill_registry: SkillRegistry | None = None,
        model: ModelInterface | None = None,
        model_router: ModelRouter | None = None,
    ):
        if skill_registry is not None and not isinstance(skill_registry, SkillRegistry):
            raise TypeError("skill_registry must be an instance of SkillRegistry or None.")
        if model is not None and not isinstance(model, ModelInterface):
            raise TypeError("model must be an instance of ModelInterface or None.")
        if model_router is not None and not isinstance(model_router, ModelRouter):
            raise TypeError("model_router must be an instance of ModelRouter or None.")

        self.skill_registry = skill_registry
        self.model = model
        self.model_router = model_router

    def evaluate(
        self,
        goal: Goal,
        observations: list[GoalObservation] | tuple[GoalObservation, ...] = (),
        task_requirements: TaskRequirements | None = None,
    ) -> GoalEvaluationResult:
        """Evaluate goal progress against success criteria and observations."""
        if not isinstance(goal, Goal):
            raise TypeError("goal must be a Goal instance.")

        if not isinstance(observations, (list, tuple)):
            raise TypeError("observations must be a sequence of GoalObservation instances.")

        # 1. Deterministic Rule-Based Assessment of Success Criteria
        satisfied: list[str] = list(goal.progress.satisfied_criteria)
        remaining: list[str] = []

        # Check observations against remaining criteria
        obs_texts = []
        for obs in observations:
            if not isinstance(obs, GoalObservation):
                continue
            if obs.source:
                obs_texts.append(obs.source.lower())
            if obs.metadata:
                for k, v in obs.metadata.items():
                    obs_texts.append(f"{k}:{v}".lower())
            val = obs.data
            if isinstance(val, TaintedValue) or is_tainted(val):
                obs_texts.append(render_for_prompt(val, wrap_untrusted=True).lower())
            elif isinstance(val, (dict, list)):
                obs_texts.append(json.dumps(val).lower())
            elif val is not None:
                obs_texts.append(str(val).lower())

        combined_obs_text = " ".join(obs_texts)

        for crit in goal.success_criteria:
            if crit in satisfied:
                continue

            # Deterministic check: if criterion key words or text found in observations
            crit_clean = crit.strip().lower()
            if crit_clean and crit_clean in combined_obs_text:
                satisfied.append(crit)
            elif "criteria:" in combined_obs_text and crit_clean in combined_obs_text:
                satisfied.append(crit)
            else:
                remaining.append(crit)

        total_crit = len(goal.success_criteria)
        if total_crit == 0:
            percentage = 1.0
        else:
            percentage = len(satisfied) / float(total_crit)

        is_complete = percentage >= 1.0 or len(remaining) == 0

        new_prog = GoalProgress(
            percentage=percentage,
            current_stage="completed" if is_complete else ("in_progress" if percentage > 0 else "initial"),
            satisfied_criteria=tuple(satisfied),
            remaining_criteria=tuple(remaining),
            confidence=1.0,
            summary=(
                f"Goal completed ({len(satisfied)}/{total_crit} criteria satisfied)"
                if is_complete
                else f"In progress ({len(satisfied)}/{total_crit} criteria satisfied)"
            ),
            last_evaluated_at=time.time(),
        )

        if is_complete:
            return GoalEvaluationResult(
                is_completed=True,
                action_needed=False,
                new_progress=new_prog,
                proposed_plan=None,
                rationale="All success criteria have been satisfied.",
            )

        # 2. If not complete, determine if an action plan should be formulated
        # If we have registered skills, formulate an action plan for the next remaining criterion
        proposed_plan: AgentPlan | None = None
        action_needed = False

        if self.skill_registry is not None and self.skill_registry.list_skills():
            next_crit = remaining[0] if remaining else "Advance goal"
            proposed_plan = self._propose_plan_for_criterion(
                goal=goal,
                criterion=next_crit,
                task_requirements=task_requirements,
            )
            if proposed_plan is not None and proposed_plan.steps:
                action_needed = True

        return GoalEvaluationResult(
            is_completed=False,
            action_needed=action_needed,
            new_progress=new_prog,
            proposed_plan=proposed_plan,
            rationale=(
                f"Action required to address remaining criteria: {', '.join(remaining[:2])}"
                if action_needed
                else "Awaiting external trigger or condition."
            ),
        )

    def _propose_plan_for_criterion(
        self,
        goal: Goal,
        criterion: str,
        task_requirements: TaskRequirements | None = None,
    ) -> AgentPlan | None:
        """Formulate a bounded AgentPlan to address a specific goal criterion."""
        if not self.skill_registry:
            return None

        available_skills = self.skill_registry.list_skills()
        if not available_skills:
            return None

        # Try to match skill with criterion keywords, or pick first suitable skill
        matched_skill = None
        for s in available_skills:
            if s.name.lower() in criterion.lower() or any(w in s.description.lower() for w in criterion.lower().split()):
                matched_skill = s
                break

        if matched_skill is None:
            matched_skill = available_skills[0]

        step = AgentPlanStep(
            step_id=f"step_goal_{matched_skill.name}",
            skill_name=matched_skill.name,
            objective=f"Address criterion: {criterion}",
            input_data={"criterion": criterion, "goal_id": goal.goal_id, "title": goal.title},
            task_requirements=task_requirements,
        )

        return AgentPlan(
            plan_id=str(uuid4()),
            task_goal=f"Goal Action: {criterion}",
            steps=(step,),
            status=StepStatus.PENDING,
            metadata={
                "goal_id": goal.goal_id,
                "criterion": criterion,
            },
        )
