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
    proposed_team_binding: Any | None = None
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
        if self.proposed_team_binding is not None:
            from core.goal_team_binding import GoalTeamBinding
            if not isinstance(self.proposed_team_binding, GoalTeamBinding):
                raise TypeError("proposed_team_binding must be an instance of GoalTeamBinding or None.")
        if not isinstance(self.rationale, str):
            raise TypeError("rationale must be a string.")

    @property
    def success(self) -> bool:
        return self.is_completed

    @property
    def status(self) -> GoalStatus:
        return GoalStatus.COMPLETED if self.is_completed else GoalStatus.IN_PROGRESS


class GoalReasoner:
    """Proactive reasoning engine for assessing goal progress and formulating bounded actions."""

    def __init__(
        self,
        skill_registry: SkillRegistry | None = None,
        model: ModelInterface | None = None,
        model_router: ModelRouter | None = None,
        memory_manager: Any | None = None,
        heuristic_calibrator: Any | None = None,
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
        self.memory_manager = memory_manager
        self.heuristic_calibrator = heuristic_calibrator

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
            # If observation is explicitly marked as a failed action outcome, skip matching its criterion
            if obs.metadata and obs.metadata.get("success") is False:
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
                try:
                    obs_texts.append(json.dumps(val, default=str).lower())
                except Exception:
                    obs_texts.append(str(val).lower())
            elif val is not None:
                obs_texts.append(str(val).lower())

        combined_obs_text = " ".join(obs_texts)

        # Check semantic memory facts if available
        if self.memory_manager is not None and hasattr(self.memory_manager, "search_facts"):
            try:
                mem_facts = self.memory_manager.search_facts(query=f"{goal.title} {goal.description}", top_k=10)
                for f in mem_facts:
                    f_text = f"{f.subject} {f.predicate} {f.object_value}".lower()
                    combined_obs_text += " " + f_text
            except Exception as ex:
                logger.warning("Error querying memory in GoalReasoner: %s", ex)

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
            if goal.assigned_team_id or (self.skill_registry is not None and self.skill_registry.list_skills()):
                if len(observations) == 0 and goal.action_count == 0:
                    remaining = [goal.title or "Execute goal"]
                    total_crit = 1
                    percentage = 0.0
                    is_complete = False
                else:
                    percentage = 1.0
                    is_complete = True
            else:
                percentage = 1.0
                is_complete = True
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

        # 2. If not complete, determine if an action plan or team binding should be formulated
        proposed_plan: AgentPlan | None = None
        proposed_team_binding: Any | None = None
        action_needed = False

        if goal.assigned_team_id:
            from core.goal_team_binding import GoalTeamBinding, GoalTeamBindingStatus
            from core.team_types import TeamDefinition, TeamMember, TeamTopology
            if isinstance(goal.execution_topology, TeamTopology):
                topology = goal.execution_topology
            elif isinstance(goal.execution_topology, str):
                try:
                    topology = TeamTopology(goal.execution_topology)
                except Exception:
                    topology = TeamTopology.HIERARCHICAL
            else:
                topology = TeamTopology.HIERARCHICAL

            next_crit = remaining[0] if remaining else "Advance team goal"
            
            if goal.metadata and "team_members" in goal.metadata and isinstance(goal.metadata["team_members"], list):
                members = [
                    TeamMember(role_id=m["role_id"], is_lead=m.get("is_lead", False))
                    for m in goal.metadata["team_members"]
                    if isinstance(m, dict) and "role_id" in m
                ]
            elif goal.assigned_role_id:
                members = [TeamMember(role_id=goal.assigned_role_id, is_lead=True)]
            elif hasattr(self, "role_registry") and self.role_registry is not None and self.role_registry.list_roles():
                registered = self.role_registry.list_roles()
                members = [TeamMember(role_id=r.role_id, is_lead=(i == 0)) for i, r in enumerate(registered)]
            else:
                members = [TeamMember(role_id="general_assistant", is_lead=True)]

            team_name = goal.metadata.get("team_name") if goal.metadata else None
            team_def = TeamDefinition(
                team_id=goal.assigned_team_id,
                name=team_name or f"Team for {goal.title}",
                members=members,
                topology=topology,
            )
            proposed_team_binding = GoalTeamBinding(
                binding_id=f"binding_{goal.goal_id}_{int(time.time())}",
                goal_id=goal.goal_id,
                team_definition=team_def,
                task_description=f"Execute goal '{goal.title}': {next_crit}. Context: {goal.description}",
                target_criteria=(next_crit,) if next_crit else (),
                status=GoalTeamBindingStatus.BOUND,
            )
            action_needed = True
        elif self.skill_registry is not None and self.skill_registry.list_skills():
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
            proposed_team_binding=proposed_team_binding,
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
            input_data={
                "criterion": criterion,
                "goal_id": goal.goal_id,
                "parent_goal_id": goal.parent_goal_id,
                "title": goal.title,
            },
            task_requirements=task_requirements,
        )

        return AgentPlan(
            plan_id=str(uuid4()),
            task_goal=f"Goal Action: {criterion}",
            steps=(step,),
            status=StepStatus.PENDING,
            metadata={
                "goal_id": goal.goal_id,
                "parent_goal_id": goal.parent_goal_id,
                "criterion": criterion,
            },
        )
