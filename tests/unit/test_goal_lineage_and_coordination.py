import pytest
from core.agent_runtime import AgentRuntime
from core.autonomous_agent import AutonomousAgentExecutor
from core.goal import (
    Goal,
    GoalObservation,
    GoalPriority,
    GoalProgress,
    GoalStatus,
)
from core.goal_engine import GoalEngine, GoalEngineConfig
from core.goal_reasoner import GoalReasoner
from core.goal_store import InMemoryGoalStore
from core.skill_registry import Skill, SkillRegistry
from core.task_state_store import InMemoryTaskStateStore


def _create_test_engine():
    skill_reg = SkillRegistry()
    skill_reg.register(
        Skill(
            name="echo_skill",
            description="echo text for goal criteria",
            handler=lambda inp: f"echo_done: {inp}",
        )
    )
    runtime = AgentRuntime(skill_registry=skill_reg)
    state_store = InMemoryTaskStateStore()
    goal_store = InMemoryGoalStore()
    executor = AutonomousAgentExecutor(
        runtime=runtime,
        state_store=state_store,
    )
    engine = GoalEngine(
        goal_store=goal_store,
        runtime=runtime,
        executor=executor,
        state_store=state_store,
    )
    return engine, goal_store, state_store


def test_goal_subgoal_creation_and_linking():
    engine, goal_store, _ = _create_test_engine()

    parent = engine.create_goal(title="Parent Goal", depth=0)
    sub1 = engine.create_subgoal(parent_goal_id=parent.goal_id, title="Sub Goal 1")

    # Parent goal's subgoal_ids should contain sub1.goal_id
    reloaded_parent = goal_store.get(parent.goal_id)
    assert sub1.goal_id in reloaded_parent.subgoal_ids
    assert sub1.depth == 1
    assert sub1.parent_goal_id == parent.goal_id


def test_goal_blocked_by_incomplete_dependency():
    engine, goal_store, _ = _create_test_engine()

    prereq = engine.create_goal(title="Prerequisite Step", success_criteria=("Step done",))
    dependent = engine.create_goal(
        title="Dependent Step",
        success_criteria=("Final step",),
        depends_on_goal_ids=(prereq.goal_id,),
    )

    # When prereq is not completed, evaluating dependent must be blocked
    res = engine.evaluate_goal(dependent.goal_id)
    assert not res.is_completed
    assert not res.action_needed
    assert "Waiting for prerequisite goal" in res.rationale


def test_goal_blocked_by_failed_dependency():
    engine, goal_store, _ = _create_test_engine()

    prereq = engine.create_goal(title="Prerequisite Step")
    # Mark prereq as cancelled/failed
    engine.cancel_goal(prereq.goal_id, reason="Network partition")

    dependent = engine.create_goal(
        title="Dependent Step",
        depends_on_goal_ids=(prereq.goal_id,),
    )

    res = engine.evaluate_goal(dependent.goal_id)
    assert not res.is_completed
    assert not res.action_needed
    assert "failed or was cancelled" in res.rationale

    reloaded_dep = goal_store.get(dependent.goal_id)
    assert reloaded_dep.status == GoalStatus.BLOCKED


def test_parent_goal_waits_for_subgoals_even_if_direct_criteria_met():
    engine, goal_store, _ = _create_test_engine()

    parent = engine.create_goal(
        title="Parent Goal",
        success_criteria=("direct_done",),
    )
    sub = engine.create_subgoal(
        parent_goal_id=parent.goal_id,
        title="Child Subgoal",
        success_criteria=("child_done",),
    )

    # Ingest observation that satisfies parent's direct criterion
    engine.add_observation(parent.goal_id, source="user", data="direct_done")

    # Evaluate parent: direct criterion is satisfied, but sub is not completed
    res = engine.evaluate_goal(parent.goal_id)
    assert not res.is_completed
    assert "awaiting completion of child sub-goals" in res.rationale

    reloaded_parent = goal_store.get(parent.goal_id)
    assert reloaded_parent.status == GoalStatus.ACTIVE


def test_parent_goal_completes_when_subgoals_and_criteria_complete():
    engine, goal_store, _ = _create_test_engine()

    parent = engine.create_goal(
        title="Parent Goal",
        success_criteria=("direct_done",),
    )
    sub = engine.create_subgoal(
        parent_goal_id=parent.goal_id,
        title="Child Subgoal",
        success_criteria=("child_done",),
    )

    # Ingest observations for both
    engine.add_observation(parent.goal_id, source="user", data="direct_done")
    engine.add_observation(sub.goal_id, source="user", data="child_done")

    # Evaluate child first
    sub_res = engine.evaluate_goal(sub.goal_id)
    assert sub_res.is_completed

    # Now evaluate parent
    parent_res = engine.evaluate_goal(parent.goal_id)
    assert parent_res.is_completed
    assert goal_store.get(parent.goal_id).status == GoalStatus.COMPLETED


def test_goal_to_task_lineage_and_state_store():
    engine, goal_store, state_store = _create_test_engine()

    goal = engine.create_goal(
        title="Goal with Action",
        success_criteria=("echo_skill",),
    )

    # Evaluation formulation and execution of plan
    eval_res = engine.evaluate_goal(goal.goal_id)
    assert eval_res.action_needed or eval_res.is_completed

    # Verify executed_task_ids in Goal
    reloaded_goal = goal_store.get(goal.goal_id)
    assert len(reloaded_goal.executed_task_ids) >= 1
    task_id = reloaded_goal.executed_task_ids[0]
    assert task_id == f"goal_{goal.goal_id}_act_1"

    # Verify TaskState in TaskStateStore has goal_id
    assert state_store.exists(task_id)
    task_state = state_store.get(task_id)
    assert task_state.goal_id == goal.goal_id
    assert task_state.task_id == task_id


def test_evaluate_all_active_goals_topological():
    engine, goal_store, _ = _create_test_engine()

    g_dep1 = engine.create_goal(title="Base Step", success_criteria=("base_done",))
    g_dep2 = engine.create_goal(
        title="Middle Step",
        success_criteria=("mid_done",),
        depends_on_goal_ids=(g_dep1.goal_id,),
    )
    g_root = engine.create_goal(
        title="Top Step",
        success_criteria=("top_done",),
        depends_on_goal_ids=(g_dep2.goal_id,),
    )

    # Provide observations for all
    engine.add_observation(g_dep1.goal_id, source="test", data="base_done")
    engine.add_observation(g_dep2.goal_id, source="test", data="mid_done")
    engine.add_observation(g_root.goal_id, source="test", data="top_done")

    # Evaluate all in topological order
    results = engine.evaluate_all_active_goals()
    assert len(results) == 3
    assert all(r.is_completed for r in results)

    assert goal_store.get(g_dep1.goal_id).status == GoalStatus.COMPLETED
    assert goal_store.get(g_dep2.goal_id).status == GoalStatus.COMPLETED
    assert goal_store.get(g_root.goal_id).status == GoalStatus.COMPLETED
