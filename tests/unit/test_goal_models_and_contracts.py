import time
from uuid import uuid4
import pytest

from core.goal import (
    Goal,
    GoalObservation,
    GoalPriority,
    GoalProgress,
    GoalStatus,
    GoalTrigger,
    TriggerType,
    deserialize_goal,
    deserialize_goal_observation,
    deserialize_goal_progress,
    deserialize_goal_trigger,
    serialize_goal,
    serialize_goal_observation,
    serialize_goal_progress,
    serialize_goal_trigger,
)
from core.provenance import TaintedValue, wrap_tainted, is_tainted


def test_goal_status_enum_and_helpers():
    assert GoalStatus.CREATED == "created"
    assert GoalStatus.ACTIVE == "active"
    assert GoalStatus.EVALUATING == "evaluating"
    assert GoalStatus.ACTION_REQUIRED == "action_required"
    assert GoalStatus.EXECUTING == "executing"
    assert GoalStatus.PROGRESS_UPDATED == "progress_updated"
    assert GoalStatus.COMPLETED == "completed"
    assert GoalStatus.PAUSED == "paused"
    assert GoalStatus.CANCELLED == "cancelled"
    assert GoalStatus.EXPIRED == "expired"
    assert GoalStatus.FAILED == "failed"
    assert GoalStatus.BLOCKED == "blocked"

    assert GoalStatus.COMPLETED.is_terminal() is True
    assert GoalStatus.CANCELLED.is_terminal() is True
    assert GoalStatus.EXPIRED.is_terminal() is True
    assert GoalStatus.FAILED.is_terminal() is True
    assert GoalStatus.ACTIVE.is_terminal() is False
    assert GoalStatus.PAUSED.is_terminal() is False

    assert GoalStatus.ACTIVE.is_active() is True
    assert GoalStatus.EVALUATING.is_active() is True
    assert GoalStatus.EXECUTING.is_active() is True
    assert GoalStatus.COMPLETED.is_active() is False
    assert GoalStatus.PAUSED.is_paused() is True


def test_goal_trigger_validation_and_ready_logic():
    trig = GoalTrigger(
        trigger_id="trig_1",
        trigger_type=TriggerType.SCHEDULE,
        expression="10.0",
        cooldown_seconds=5.0,
    )
    assert trig.trigger_id == "trig_1"
    assert trig.trigger_type == TriggerType.SCHEDULE
    assert trig.expression == "10.0"

    # Initially ready
    now = 100.0
    assert trig.is_ready(current_time=now) is True

    # After firing
    fired = trig.fire(current_time=now)
    assert fired.last_fired_at == 100.0

    # Cooldown check: 2 seconds later is NOT ready (cooldown is 5.0s)
    assert fired.is_ready(current_time=102.0) is False

    # Schedule interval check: 12 seconds later is ready (interval 10s passed)
    assert fired.is_ready(current_time=112.0) is True


def test_goal_observation_and_taint_propagation():
    tainted = wrap_tainted("Sensitive external stream", source_urls=("https://data.feed/1",))
    obs = GoalObservation(
        goal_id="g1",
        source="sensor_stream",
        data=tainted,
    )
    assert obs.goal_id == "g1"
    assert obs.is_untrusted is True
    assert is_tainted(obs.data) is True

    # Rejection of callable data
    with pytest.raises(ValueError):
        GoalObservation(goal_id="g1", source="test", data=lambda: "bad")


def test_goal_progress_clamping_and_completion():
    prog = GoalProgress(
        percentage=1.5,  # Should clamp to 1.0
        current_stage="final",
        satisfied_criteria=("crit_1", "crit_2"),
        remaining_criteria=(),
        confidence=2.0,  # Should clamp to 1.0
    )
    assert prog.percentage == 1.0
    assert prog.confidence == 1.0
    assert prog.is_complete() is True

    prog_incomplete = GoalProgress(
        percentage=0.5,
        satisfied_criteria=("crit_1",),
        remaining_criteria=("crit_2",),
    )
    assert prog_incomplete.is_complete() is False


def test_goal_immutability_and_state_updates():
    trig = GoalTrigger(trigger_id="t1", trigger_type=TriggerType.MANUAL)
    goal = Goal(
        goal_id="goal_100",
        title="Automate Monitoring",
        description="Monitor system health",
        success_criteria=("check_disk", "check_cpu"),
        priority=GoalPriority.HIGH,
        triggers=(trig,),
        expires_at=500.0,
    )

    assert goal.goal_id == "goal_100"
    assert goal.status == GoalStatus.CREATED
    assert goal.is_expired(current_time=400.0) is False
    assert goal.is_expired(current_time=600.0) is True

    # Update status
    active_g = goal.with_status(GoalStatus.ACTIVE)
    assert active_g.status == GoalStatus.ACTIVE
    assert goal.status == GoalStatus.CREATED

    # Update progress
    new_prog = GoalProgress(percentage=1.0, satisfied_criteria=("check_disk", "check_cpu"), remaining_criteria=())
    completed_g = active_g.with_progress(new_prog)
    assert completed_g.status == GoalStatus.COMPLETED


def test_metadata_sanitization_in_goal_and_triggers():
    meta = {
        "tag": "prod",
        "approved": True,
        "is_approved": True,
        "auto_approve": True,
        "authorized": True,
        "func": lambda: 1,
    }
    goal = Goal(
        title="Safe Goal",
        metadata=meta,
    )
    assert "approved" not in goal.metadata
    assert "is_approved" not in goal.metadata
    assert "auto_approve" not in goal.metadata
    assert "authorized" not in goal.metadata
    assert "func" not in goal.metadata
    assert goal.metadata["tag"] == "prod"


def test_serialization_and_deserialization_roundtrip():
    tainted_data = wrap_tainted("untrusted signal", source_urls=("https://sensor.local/temp",))
    obs = GoalObservation(
        goal_id="g_serial",
        source="sensor",
        data=tainted_data,
    )
    trig = GoalTrigger(
        trigger_id="trig_serial",
        trigger_type=TriggerType.EVENT,
        expression="alarm_fired",
        cooldown_seconds=10.0,
    )
    prog = GoalProgress(
        percentage=0.5,
        current_stage="step_1_done",
        satisfied_criteria=("crit_1",),
        remaining_criteria=("crit_2",),
    )
    goal = Goal(
        goal_id="goal_serial",
        title="Serialization Test",
        description="Verify roundtrip",
        success_criteria=("crit_1", "crit_2"),
        priority=GoalPriority.CRITICAL,
        status=GoalStatus.ACTIVE,
        triggers=(trig,),
        progress=prog,
        evaluation_count=3,
        action_count=1,
    )

    # 1. Trigger
    s_trig = serialize_goal_trigger(trig)
    d_trig = deserialize_goal_trigger(s_trig)
    assert d_trig.trigger_id == trig.trigger_id
    assert d_trig.trigger_type == TriggerType.EVENT

    # 2. Observation
    s_obs = serialize_goal_observation(obs)
    d_obs = deserialize_goal_observation(s_obs)
    assert d_obs.goal_id == obs.goal_id
    assert is_tainted(d_obs.data) is True
    assert d_obs.data.raw_value == "untrusted signal"

    # 3. Progress
    s_prog = serialize_goal_progress(prog)
    d_prog = deserialize_goal_progress(s_prog)
    assert d_prog.percentage == 0.5
    assert d_prog.satisfied_criteria == ("crit_1",)

    # 4. Goal
    s_goal = serialize_goal(goal)
    d_goal = deserialize_goal(s_goal)
    assert d_goal.goal_id == goal.goal_id
    assert d_goal.priority == GoalPriority.CRITICAL
    assert d_goal.evaluation_count == 3
    assert len(d_goal.triggers) == 1
    assert d_goal.triggers[0].trigger_type == TriggerType.EVENT
