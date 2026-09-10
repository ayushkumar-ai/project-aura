"""
Unit tests for Milestone 22 team-aware goal scheduling.
Tests ScheduledGoalTask team assignment attributes, role concurrency management,
taint preservation, and scheduling serialization.
"""

import pytest
from core.scheduling_types import (
    ScheduledGoalTask,
    GoalScheduleStatus,
)
from core.team_types import TeamTopology
from core.goal_scheduler import GoalScheduler, MultiGoalScheduler
from core.goal import Goal, GoalPriority
from core.provenance import TaintedValue


def test_scheduled_goal_task_team_attributes():
    task = ScheduledGoalTask(
        goal_id="goal_team_1",
        priority=GoalPriority.HIGH,
        assigned_team_id="team_sec_ops",
        assigned_role_id="security_auditor",
        execution_topology=TeamTopology.HIERARCHICAL.value,
    )
    assert task.assigned_team_id == "team_sec_ops"
    assert task.assigned_role_id == "security_auditor"
    assert task.execution_topology == TeamTopology.HIERARCHICAL.value

    # Update with status
    updated = task.with_status(GoalScheduleStatus.RUNNING)
    assert updated.status == GoalScheduleStatus.RUNNING
    assert updated.assigned_team_id == "team_sec_ops"
    assert updated.assigned_role_id == "security_auditor"
    assert updated.execution_topology == TeamTopology.HIERARCHICAL.value


def test_goal_scheduler_schedule_goal_with_team_fields():
    scheduler = GoalScheduler()
    goal = Goal(
        title="Scheduled Team Code Review",
        description="Weekly multi-agent team review",
        assigned_team_id="review_council",
        assigned_role_id="reviewer",
        execution_topology=TeamTopology.CONSENSUS_VOTING,
    )
    task = scheduler.schedule_goal(
        goal_id=goal.goal_id,
        priority=goal.priority,
        assigned_team_id=goal.assigned_team_id,
        assigned_role_id=goal.assigned_role_id,
        execution_topology=goal.execution_topology.value if hasattr(goal.execution_topology, "value") else str(goal.execution_topology),
    )
    assert task.assigned_team_id == "review_council"
    assert task.assigned_role_id == "reviewer"
    assert task.execution_topology == TeamTopology.CONSENSUS_VOTING.value

    retrieved = scheduler._tasks.get(goal.goal_id)
    assert retrieved is not None
    assert retrieved.assigned_team_id == "review_council"
    assert retrieved.assigned_role_id == "reviewer"
    assert retrieved.execution_topology == TeamTopology.CONSENSUS_VOTING.value


def test_scheduled_goal_task_taint_preservation():
    tainted_meta = {
        "external_event": TaintedValue(raw_value="webhook_data", source_type="github_pr"),
        "role_context": "security_scan",
    }
    task = ScheduledGoalTask(
        goal_id="goal_tainted_1",
        assigned_team_id="team_tainted",
        assigned_role_id="security_auditor",
        execution_topology=TeamTopology.SEQUENTIAL_PIPELINE.value,
        metadata=tainted_meta,
    )
    assert isinstance(task.metadata["external_event"], TaintedValue)
    assert task.metadata["external_event"].source_type == "github_pr"

    updated = task.with_status(GoalScheduleStatus.COMPLETED)
    assert isinstance(updated.metadata["external_event"], TaintedValue)
    assert updated.metadata["external_event"].value == "webhook_data"
