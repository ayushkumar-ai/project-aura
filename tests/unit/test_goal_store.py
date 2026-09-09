import pytest
from core.goal import Goal, GoalPriority, GoalStatus
from core.goal_store import InMemoryGoalStore


def test_in_memory_goal_store_crud():
    store = InMemoryGoalStore()

    g1 = Goal(goal_id="g1", title="Goal 1", priority=GoalPriority.LOW, status=GoalStatus.ACTIVE)
    g2 = Goal(goal_id="g2", title="Goal 2", priority=GoalPriority.HIGH, status=GoalStatus.PAUSED)

    # Create
    store.create(g1)
    store.create(g2)

    assert store.exists("g1") is True
    assert store.exists("g2") is True
    assert store.exists("g_unknown") is False

    # Duplicate creation error
    with pytest.raises(ValueError):
        store.create(g1)

    # Get & deepcopy isolation
    retrieved = store.get("g1")
    assert retrieved.title == "Goal 1"

    # Update
    updated_g1 = g1.with_status(GoalStatus.COMPLETED)
    store.update(updated_g1)
    assert store.get("g1").status == GoalStatus.COMPLETED

    # Query with filters
    active_goals = store.list_goals(status=GoalStatus.ACTIVE)
    assert len(active_goals) == 0

    paused_goals = store.list_goals(status=GoalStatus.PAUSED)
    assert len(paused_goals) == 1
    assert paused_goals[0].goal_id == "g2"

    high_goals = store.list_goals(priority=GoalPriority.HIGH)
    assert len(high_goals) == 1
    assert high_goals[0].goal_id == "g2"

    # Delete
    store.delete("g1")
    assert store.exists("g1") is False
    with pytest.raises(KeyError):
        store.get("g1")
