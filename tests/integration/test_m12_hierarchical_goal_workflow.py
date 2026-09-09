import tempfile
import pytest
from pathlib import Path
from uuid import UUID, uuid4

from core.agent_plan import AgentPlan, AgentPlanStep, StepStatus
from core.agent_runtime import AgentRuntime
from core.agentic_runtime import AgenticRuntime, ExecutionMode
from core.approval import ApprovalGateway, ApprovalStatus
from core.autonomous_agent import AutonomousAgentExecutor, AutonomousAgentResult
from core.goal import (
    Goal,
    GoalObservation,
    GoalPriority,
    GoalProgress,
    GoalStatus,
)
from core.goal_engine import GoalEngine, GoalEngineConfig
from core.goal_reasoner import GoalEvaluationResult, GoalReasoner
from core.goal_store import FileGoalStore, InMemoryGoalStore
from core.models import AURAResponse
from core.policy import Policy
from core.provenance import wrap_tainted, is_tainted, unwrap_tainted
from core.skill_registry import Skill, SkillRegistry
from core.task_state_store import InMemoryTaskStateStore
from interfaces.model import ModelInterface
from tools.calculator import CalculatorTool
from tools.echo import EchoTool


def test_m12_end_to_end_hierarchical_goal_workflow():
    skill_reg = SkillRegistry()
    skill_reg.register(
        Skill(
            name="test_runner",
            description="Runs automated tests",
            handler=lambda inp: f"tests_passed: {inp}",
        )
    )
    skill_reg.register(
        Skill(
            name="health_checker",
            description="Checks service health",
            handler=lambda inp: f"health_ok: {inp}",
        )
    )

    policy = Policy()
    approval_gateway = ApprovalGateway(policy=policy, skill_registry=skill_reg)
    state_store = InMemoryTaskStateStore()
    goal_store = InMemoryGoalStore()

    runtime = AgenticRuntime(
        skill_registry=skill_reg,
        policy=policy,
        approval_gateway=approval_gateway,
        state_store=state_store,
        goal_store=goal_store,
    )

    # 1. Create Root Goal
    root_goal = runtime.goal_engine.create_goal(
        title="Deploy and Validate Service",
        success_criteria=("deploy_verified",),
        depth=0,
    )
    root_id = root_goal.goal_id

    # 2. Create Subgoal 1 (Run Tests)
    sub1 = runtime.goal_engine.create_subgoal(
        parent_goal_id=root_id,
        title="Execute Unit Tests",
        success_criteria=("test_runner",),
    )

    # 3. Create Subgoal 2 (Check Health) depending on Subgoal 1
    sub2 = runtime.goal_engine.create_subgoal(
        parent_goal_id=root_id,
        title="Verify Health Endpoint",
        success_criteria=("health_checker",),
        depends_on_goal_ids=(sub1.goal_id,),
    )

    # Reload root goal and check hierarchy
    root_reloaded = goal_store.get(root_id)
    assert sub1.goal_id in root_reloaded.subgoal_ids
    assert sub2.goal_id in root_reloaded.subgoal_ids
    assert sub1.depth == 1
    assert sub2.depth == 1

    # 4. Try evaluating Sub2 before Sub1: should be blocked by prerequisite
    sub2_eval = runtime.goal_engine.evaluate_goal(sub2.goal_id)
    assert not sub2_eval.is_completed
    assert "Waiting for prerequisite goal" in sub2_eval.rationale

    # 5. Try evaluating Root: should be waiting for subgoals
    runtime.goal_engine.add_observation(root_id, source="env", data="deploy_verified")
    root_eval = runtime.goal_engine.evaluate_goal(root_id)
    assert not root_eval.is_completed
    assert "awaiting completion of child sub-goals" in root_eval.rationale

    # 6. Evaluate Sub1: action executes via AutonomousAgentExecutor and records task lineage
    sub1_eval = runtime.goal_engine.evaluate_goal(sub1.goal_id)
    assert sub1_eval.is_completed or sub1_eval.action_needed
    sub1_after = goal_store.get(sub1.goal_id)
    assert len(sub1_after.executed_task_ids) >= 1
    task_id_1 = sub1_after.executed_task_ids[0]
    assert state_store.exists(task_id_1)
    assert state_store.get(task_id_1).goal_id == sub1.goal_id
    assert state_store.get(task_id_1).parent_goal_id == root_id

    # 7. Evaluate Sub2 now that Sub1 completed
    sub2_eval2 = runtime.goal_engine.evaluate_goal(sub2.goal_id)
    assert sub2_eval2.is_completed or sub2_eval2.action_needed
    sub2_after = goal_store.get(sub2.goal_id)
    assert len(sub2_after.executed_task_ids) >= 1

    # 8. Evaluate Root goal now that all subgoals completed
    final_root_eval = runtime.goal_engine.evaluate_goal(root_id)
    assert final_root_eval.is_completed
    assert goal_store.get(root_id).status == GoalStatus.COMPLETED


def test_m12_sensitive_action_approval_in_hierarchical_workflow():
    skill_reg = SkillRegistry()
    skill_reg.register(
        Skill(
            name="db_migration",
            description="Execute database migrations",
            handler=lambda inp: f"migrated: {inp}",
        )
    )

    policy = Policy()
    approval_gateway = ApprovalGateway(
        policy=policy,
        skill_registry=skill_reg,
        sensitive_skills={"db_migration"},
    )
    state_store = InMemoryTaskStateStore()
    goal_store = InMemoryGoalStore()

    runtime = AgenticRuntime(
        skill_registry=skill_reg,
        policy=policy,
        approval_gateway=approval_gateway,
        state_store=state_store,
        goal_store=goal_store,
    )

    root = runtime.goal_engine.create_goal(title="System Upgrade")
    sub = runtime.goal_engine.create_subgoal(
        parent_goal_id=root.goal_id,
        title="Run DB Migration",
        success_criteria=("db_migration",),
    )

    # Evaluating sub triggers sensitive action which is intercepted by ApprovalGateway
    eval_res = runtime.goal_engine.evaluate_goal(sub.goal_id)
    assert not eval_res.is_completed
    assert "awaiting approval" in eval_res.rationale.lower()

    sub_goal = goal_store.get(sub.goal_id)
    assert sub_goal.status == GoalStatus.PAUSED


def test_m12_file_store_persistence_across_restarts():
    with tempfile.TemporaryDirectory() as tmp_dir:
        skill_reg = SkillRegistry()
        skill_reg.register(
            Skill(
                name="echo_skill",
                description="echo input",
                handler=lambda inp: f"echo: {inp}",
            )
        )
        state_store = InMemoryTaskStateStore()
        file_store1 = FileGoalStore(storage_dir=tmp_dir)

        runtime1 = AgenticRuntime(
            skill_registry=skill_reg,
            state_store=state_store,
            goal_store=file_store1,
        )

        tainted_obs = wrap_tainted("untrusted signal", is_untrusted=True, source_urls=("https://untrusted.org",))

        root = runtime1.goal_engine.create_goal(title="Persistent Root Goal")
        sub = runtime1.goal_engine.create_subgoal(parent_goal_id=root.goal_id, title="Persistent Sub Goal")
        runtime1.goal_engine.add_observation(sub.goal_id, source="crawl", data=tainted_obs)

        # Simulate full process restart
        file_store2 = FileGoalStore(storage_dir=tmp_dir)
        runtime2 = AgenticRuntime(
            skill_registry=skill_reg,
            state_store=state_store,
            goal_store=file_store2,
        )

        reloaded_root = runtime2.goal_engine.get_goal(root.goal_id)
        assert sub.goal_id in reloaded_root.subgoal_ids

        reloaded_sub = runtime2.goal_engine.get_goal(sub.goal_id)
        assert reloaded_sub.parent_goal_id == root.goal_id

        obs_list = runtime2.goal_engine.get_observations(sub.goal_id)
        assert len(obs_list) == 1
        assert is_tainted(obs_list[0].data)
        assert unwrap_tainted(obs_list[0].data) == "untrusted signal"
