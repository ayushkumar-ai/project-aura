import pytest
from core.goal import Goal, GoalObservation, GoalPriority, GoalProgress, GoalStatus
from core.goal_reasoner import GoalReasoner, GoalEvaluationResult
from core.skill_registry import Skill, SkillRegistry
from core.provenance import wrap_tainted


def test_goal_reasoner_deterministic_evaluation():
    skill_reg = SkillRegistry()
    skill_reg.register(Skill(name="disk_check_skill", description="Checks disk capacity"))
    skill_reg.register(Skill(name="backup_skill", description="Creates backups"))

    reasoner = GoalReasoner(skill_registry=skill_reg)

    goal = Goal(
        goal_id="g_eval",
        title="System Maintenance",
        success_criteria=("disk_check_skill", "backup_skill"),
        status=GoalStatus.ACTIVE,
    )

    # Evaluation with no observations -> Action needed on disk_check_skill
    res1 = reasoner.evaluate(goal, observations=())
    assert res1.is_completed is False
    assert res1.action_needed is True
    assert res1.proposed_plan is not None
    assert len(res1.proposed_plan.steps) == 1
    assert res1.proposed_plan.steps[0].skill_name == "disk_check_skill"

    # Now provide observation satisfying first criterion
    obs1 = GoalObservation(
        goal_id="g_eval",
        source="action:disk_check",
        data="disk_check_skill completed successfully with 80% free space",
    )
    res2 = reasoner.evaluate(goal, observations=(obs1,))
    assert res2.is_completed is False
    assert res2.new_progress.percentage == 0.5
    assert "disk_check_skill" in res2.new_progress.satisfied_criteria
    assert res2.action_needed is True
    assert res2.proposed_plan.steps[0].skill_name == "backup_skill"

    # Now provide observation satisfying second criterion
    obs2 = GoalObservation(
        goal_id="g_eval",
        source="action:backup",
        data="backup_skill completed successfully",
    )
    res3 = reasoner.evaluate(goal, observations=(obs1, obs2))
    assert res3.is_completed is True
    assert res3.action_needed is False
    assert res3.new_progress.percentage == 1.0
    assert res3.proposed_plan is None
