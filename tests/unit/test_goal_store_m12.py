import tempfile
import pytest
from pathlib import Path
from core.goal import (
    Goal,
    GoalObservation,
    GoalPriority,
    GoalStatus,
)
from core.goal_store import InMemoryGoalStore, FileGoalStore
from core.provenance import wrap_tainted, is_tainted, unwrap_tainted


def test_in_memory_goal_store_observation_lifecycle():
    store = InMemoryGoalStore(max_observations_per_goal=5)
    goal = store.create(Goal(goal_id="g1", title="Test Goal"))

    assert store.get_observations("g1") == []

    obs1 = store.add_observation("g1", GoalObservation(goal_id="g1", source="tool:echo", data="obs 1"))
    obs2 = store.add_observation("g1", GoalObservation(goal_id="g1", source="tool:echo", data="obs 2"))

    all_obs = store.get_observations("g1")
    assert len(all_obs) == 2
    assert all_obs[0].data == "obs 1"
    assert all_obs[1].data == "obs 2"

    # Deepcopy isolation check
    all_obs[0].metadata["tamper"] = True
    assert "tamper" not in store.get_observations("g1")[0].metadata

    # Clear observations
    store.clear_observations("g1")
    assert store.get_observations("g1") == []


def test_in_memory_goal_store_observation_ring_buffer():
    store = InMemoryGoalStore(max_observations_per_goal=3)
    goal = store.create(Goal(goal_id="g1", title="Test Goal"))

    for i in range(5):
        store.add_observation("g1", GoalObservation(goal_id="g1", source="agent", data=f"data_{i}"))

    obs = store.get_observations("g1")
    assert len(obs) == 3
    # Kept oldest pruned, newest preserved
    assert [o.data for o in obs] == ["data_2", "data_3", "data_4"]


def test_in_memory_goal_store_hierarchy_queries():
    store = InMemoryGoalStore()
    root = store.create(Goal(goal_id="root", title="Root", depth=0))
    sub1 = store.create(Goal(goal_id="sub1", title="Sub 1", parent_goal_id="root", depth=1))
    sub2 = store.create(Goal(goal_id="sub2", title="Sub 2", parent_goal_id="root", depth=1))
    other_root = store.create(Goal(goal_id="root2", title="Other Root", depth=0))

    roots = store.get_root_goals()
    assert len(roots) == 2
    assert {r.goal_id for r in roots} == {"root", "root2"}

    subgoals = store.get_subgoals("root")
    assert len(subgoals) == 2
    assert {s.goal_id for s in subgoals} == {"sub1", "sub2"}


def test_file_goal_store_lifecycle_and_persistence():
    with tempfile.TemporaryDirectory() as tmp_dir:
        store1 = FileGoalStore(storage_dir=tmp_dir, max_observations_per_goal=4)

        tainted_data = wrap_tainted("untrusted content", is_untrusted=True, source_urls=("https://evil.com",))
        g1 = Goal(
            goal_id="fg1",
            title="File Goal 1",
            parent_goal_id=None,
            subgoal_ids=("sub_f1",),
            depth=0,
            executed_task_ids=("task_f1",),
        )
        store1.create(g1)

        sub_g1 = Goal(
            goal_id="sub_f1",
            title="Sub File Goal",
            parent_goal_id="fg1",
            depth=1,
        )
        store1.create(sub_g1)

        obs = GoalObservation(
            goal_id="fg1",
            source="external_crawl",
            data=tainted_data,
            is_untrusted=True,
            metadata={"tag": "crawl_result"},
        )
        store1.add_observation("fg1", obs)

        # Retrieve and check
        loaded_g1 = store1.get("fg1")
        assert loaded_g1.title == "File Goal 1"
        assert loaded_g1.executed_task_ids == ("task_f1",)

        loaded_obs = store1.get_observations("fg1")
        assert len(loaded_obs) == 1
        assert is_tainted(loaded_obs[0].data)
        assert unwrap_tainted(loaded_obs[0].data) == "untrusted content"

        # Re-instantiate from disk (simulating service restart)
        store2 = FileGoalStore(storage_dir=tmp_dir, max_observations_per_goal=4)
        assert store2.exists("fg1")
        assert store2.exists("sub_f1")

        restarted_g1 = store2.get("fg1")
        assert restarted_g1.subgoal_ids == ("sub_f1",)
        assert restarted_g1.executed_task_ids == ("task_f1",)

        restarted_obs = store2.get_observations("fg1")
        assert len(restarted_obs) == 1
        assert is_tainted(restarted_obs[0].data)
        assert unwrap_tainted(restarted_obs[0].data) == "untrusted content"

        # Check subgoals query on disk
        subs = store2.get_subgoals("fg1")
        assert len(subs) == 1
        assert subs[0].goal_id == "sub_f1"

        roots = store2.get_root_goals()
        assert len(roots) == 1
        assert roots[0].goal_id == "fg1"

        # Clear observations on disk
        store2.clear_observations("fg1")
        assert store2.get_observations("fg1") == []
