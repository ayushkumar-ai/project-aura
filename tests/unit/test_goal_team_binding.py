import pytest
from core.goal import GoalProgress
from core.team_types import TeamDefinition, TeamMember, TeamTopology, TeamExecutionResult
from core.goal_team_binding import (
    GoalTeamBinding,
    GoalTeamBindingStatus,
    GoalTeamExecutionResult,
    sanitize_binding_metadata,
)


def test_goal_team_binding_creation_and_attributes():
    team = TeamDefinition(
        team_id="eng_team",
        name="Engineering Team",
        topology=TeamTopology.HIERARCHICAL,
        members=[TeamMember(role_id="architect"), TeamMember(role_id="coder")],
    )

    binding = GoalTeamBinding(
        goal_id="goal_100",
        team_definition=team,
        task_description="Implement microservices architecture",
        target_criteria=("Criterion 1", "Criterion 2"),
        metadata={"priority": "high", "is_authorized": True, "bypass_policy": True},
    )

    assert binding.goal_id == "goal_100"
    assert binding.team_definition.team_id == "eng_team"
    assert binding.status == GoalTeamBindingStatus.CREATED
    assert len(binding.target_criteria) == 2
    # Check security metadata sanitization
    assert "is_authorized" not in binding.metadata
    assert "bypass_policy" not in binding.metadata
    assert binding.metadata.get("priority") == "high"

    # Status update
    in_prog = binding.with_status(GoalTeamBindingStatus.IN_PROGRESS)
    assert in_prog.status == GoalTeamBindingStatus.IN_PROGRESS
    assert in_prog.binding_id == binding.binding_id

    # Serialization
    d = binding.to_dict()
    assert d["goal_id"] == "goal_100"
    assert d["team_definition"]["team_id"] == "eng_team"


def test_goal_team_binding_validation_errors():
    team = TeamDefinition(
        team_id="eng_team",
        name="Engineering Team",
        members=[TeamMember(role_id="coder")],
    )

    with pytest.raises(ValueError):
        GoalTeamBinding(goal_id="", team_definition=team, task_description="Valid")

    with pytest.raises(ValueError):
        GoalTeamBinding(goal_id="goal_1", team_definition=team, task_description="")

    with pytest.raises(TypeError):
        GoalTeamBinding(goal_id="goal_1", team_definition="not_a_team", task_description="Valid")


def test_goal_team_execution_result_conversion():
    team = TeamDefinition(
        team_id="eng_team",
        name="Engineering Team",
        members=[TeamMember(role_id="coder")],
    )
    binding = GoalTeamBinding(
        goal_id="goal_100",
        team_definition=team,
        task_description="Build service",
        is_untrusted=True,
    )

    team_res = TeamExecutionResult(
        team_id="eng_team",
        task="Build service",
        topology=TeamTopology.HIERARCHICAL,
        success=True,
        final_output="Architecture implemented successfully.",
        subtask_results={"coder": "Code generated"},
        consensus_score=0.95,
        messages_exchanged=4,
        total_latency_seconds=1.2,
    )

    result = GoalTeamExecutionResult.from_team_result(binding, team_res)
    assert result.goal_id == "goal_100"
    assert result.team_id == "eng_team"
    assert result.success is True
    assert result.is_untrusted is True
    assert result.consensus_score == 0.95

    # Convert to GoalObservation
    obs = result.to_goal_observation()
    assert obs.goal_id == "goal_100"
    assert obs.source == "multi_agent_team"
    assert obs.is_untrusted is True
    assert obs.data["success"] is True

    # Convert to GoalProgress
    init_progress = GoalProgress(
        percentage=0.0,
        current_stage="planning",
        remaining_criteria=("Criterion 1", "Criterion 2"),
    )
    updated_progress = result.to_goal_progress(
        current_progress=init_progress,
        satisfied_criteria=["Criterion 1"],
        remaining_criteria=["Criterion 2"],
    )
    assert updated_progress.percentage == 0.5
    assert "Criterion 1" in updated_progress.satisfied_criteria
    assert "Criterion 2" in updated_progress.remaining_criteria
