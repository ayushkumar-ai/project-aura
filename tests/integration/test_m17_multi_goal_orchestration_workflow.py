import time
import pytest
from uuid import uuid4

from core.agent_plan import AgentPlan, AgentPlanStep, StepStatus
from core.agent_runtime import AgentRuntime
from core.agentic_runtime import AgenticRuntime, ExecutionMode
from core.approval import ApprovalGateway, ApprovalStatus
from core.autonomous_agent import AutonomousAgentExecutor
from core.clarification_gateway import ClarificationGateway
from core.event_dispatcher import ProactiveEventDispatcher
from core.goal import Goal, GoalObservation, GoalPriority, GoalProgress, GoalStatus
from core.goal_adapter import GoalAdapter
from core.goal_engine import GoalEngine
from core.goal_reasoner import GoalEvaluationResult, GoalReasoner
from core.goal_scheduler import MultiGoalScheduler
from core.goal_store import InMemoryGoalStore
from core.policy import Policy
from core.provenance import TaintedValue, is_tainted, wrap_tainted
from core.resource_budget import ResourceBudgetManager
from core.resource_locks import SharedResourceLockManager
from core.scheduling_types import (
    ClarificationStatus,
    ClarificationType,
    LockType,
    ProactiveEvent,
    ResourceQuota,
)
from core.skill import Skill
from core.skill_registry import SkillRegistry
from core.strategy_types import StrategyType
from core.task_state_store import InMemoryTaskStateStore
from core.tool_registry import ToolRegistry


class DummyTool:
    name = "dummy_tool"
    description = "A dummy execution tool"

    def __init__(self):
        self.call_count = 0

    def execute(self, payload: str):
        self.call_count += 1
        return f"EXECUTED: {payload}"


class SensitiveTool:
    name = "sensitive_deploy"
    description = "Sensitive deployment tool requiring approval"

    def execute(self, payload: str):
        return f"DEPLOYED: {payload}"


def test_concurrent_multi_goal_execution_with_resource_governance():
    tool = DummyTool()
    tool_reg = ToolRegistry()
    tool_reg.register(tool.name, tool)

    skill_reg = SkillRegistry()
    skill_reg.register(
        Skill(
            name="dummy_skill",
            description="Executes dummy tool",
            handler=lambda inp: tool.execute(str(inp)),
            tools=["dummy_tool"],
        )
    )

    policy = Policy(authorized_tools={"dummy_tool"})
    budget_mgr = ResourceBudgetManager(max_concurrent_goals=2, global_max_tool_calls_per_minute=20)
    lock_mgr = SharedResourceLockManager()
    clarif_gw = ClarificationGateway()
    event_disp = ProactiveEventDispatcher()
    scheduler = MultiGoalScheduler(budget_manager=budget_mgr, lock_manager=lock_mgr, max_concurrent_goals=2)

    runtime = AgenticRuntime(
        skill_registry=skill_reg,
        policy=policy,
        budget_manager=budget_mgr,
        lock_manager=lock_mgr,
        scheduler=scheduler,
        event_dispatcher=event_disp,
        clarification_gateway=clarif_gw,
    )

    # Create 3 goals
    g1 = runtime.goal_engine.create_goal(title="Goal 1", success_criteria=("dummy_skill",), priority=GoalPriority.HIGH)
    g2 = runtime.goal_engine.create_goal(title="Goal 2", success_criteria=("dummy_skill",), priority=GoalPriority.MEDIUM)
    g3 = runtime.goal_engine.create_goal(title="Goal 3", success_criteria=("dummy_skill",), priority=GoalPriority.LOW)

    # Step batch 1: max 2 goals run concurrently
    batch1 = runtime.step_scheduled_goals(max_batch_size=2)
    assert len(batch1) == 2
    assert runtime.goal_engine.get_goal(g1.goal_id).status == GoalStatus.COMPLETED
    assert runtime.goal_engine.get_goal(g2.goal_id).status == GoalStatus.COMPLETED

    # Step batch 2: g3 runs
    batch2 = runtime.step_scheduled_goals(max_batch_size=2)
    assert len(batch2) == 1
    assert runtime.goal_engine.get_goal(g3.goal_id).status == GoalStatus.COMPLETED


def test_shared_resource_locking_prevents_goal_interference():
    tool = DummyTool()
    tool_reg = ToolRegistry()
    tool_reg.register(tool.name, tool)
    skill_reg = SkillRegistry()
    skill_reg.register(
        Skill(
            name="shared_skill",
            description="Skill accessing shared database",
            handler=lambda inp: tool.execute(str(inp)),
            tools=["dummy_tool"],
        )
    )

    policy = Policy(authorized_tools={"dummy_tool"})
    lock_mgr = SharedResourceLockManager()
    budget_mgr = ResourceBudgetManager(max_concurrent_goals=4)
    scheduler = MultiGoalScheduler(budget_manager=budget_mgr, lock_manager=lock_mgr)

    runtime = AgenticRuntime(
        skill_registry=skill_reg,
        policy=policy,
        lock_manager=lock_mgr,
        budget_manager=budget_mgr,
        scheduler=scheduler,
    )

    # Goal 1 and Goal 2 both require exclusive write on db:main
    g1 = runtime.goal_engine.create_goal(title="Writer 1", success_criteria=("shared_skill",))
    g2 = runtime.goal_engine.create_goal(title="Writer 2", success_criteria=("shared_skill",))

    # Manually hold a lock on db:main by external owner
    lock_mgr.acquire_lock("db:main", "external_holder", lock_type=LockType.EXCLUSIVE_WRITE)

    scheduler.schedule_goal(g1.goal_id, required_resources=["db:main"])
    scheduler.schedule_goal(g2.goal_id, required_resources=["db:main"])

    # Both goals should be blocked by scheduler due to lock contention
    batch = scheduler.step_next_batch(runtime.goal_engine)
    assert len(batch) == 0
    assert runtime.goal_engine.get_goal(g1.goal_id).status == GoalStatus.ACTIVE

    # Release external lock
    lock_mgr.release_all_locks_for_goal("external_holder")

    # Now g1 can proceed
    batch1 = scheduler.step_next_batch(runtime.goal_engine, max_batch_size=1)
    assert len(batch1) == 1
    assert runtime.goal_engine.get_goal(g1.goal_id).status == GoalStatus.COMPLETED


def test_priority_preemption_and_starvation_prevention():
    tool = DummyTool()
    skill_reg = SkillRegistry()
    skill_reg.register(
        Skill(
            name="task_skill",
            description="Task execution",
            handler=lambda inp: tool.execute(str(inp)),
        )
    )

    runtime = AgenticRuntime(skill_registry=skill_reg)
    engine = runtime.goal_engine
    scheduler = runtime.get_scheduler()

    # Enqueue Low priority goal at t=0
    g_low = engine.create_goal(title="Low Pri", success_criteria=("task_skill",), priority=GoalPriority.LOW)
    scheduler.schedule_goal(g_low.goal_id, priority=GoalPriority.LOW, current_time=0.0)

    # Enqueue Critical goal at t=10.0
    g_crit = engine.create_goal(title="Crit Pri", success_criteria=("task_skill",), priority=GoalPriority.CRITICAL)
    scheduler.schedule_goal(g_crit.goal_id, priority=GoalPriority.CRITICAL, current_time=10.0)

    # Critical goal runs first
    batch = scheduler.step_next_batch(engine, max_batch_size=1, current_time=10.0)
    assert len(batch) == 1
    assert engine.get_goal(g_crit.goal_id).status == GoalStatus.COMPLETED
    assert engine.get_goal(g_low.goal_id).status == GoalStatus.ACTIVE


def test_asynchronous_proactive_event_dispatch_triggers_goal():
    tool = DummyTool()
    skill_reg = SkillRegistry()
    skill_reg.register(
        Skill(
            name="alert_handler",
            description="Handles alert events",
            handler=lambda inp: tool.execute(str(inp)),
        )
    )

    runtime = AgenticRuntime(skill_registry=skill_reg)
    engine = runtime.goal_engine
    dispatcher = runtime.get_event_dispatcher()
    scheduler = runtime.get_scheduler()

    # Create goal waiting for system.alert event
    goal = engine.create_goal(
        title="Alert Processing Goal",
        success_criteria=("alert_handler",),
    )

    # Subscribe goal to alert topic
    sub = dispatcher.subscribe(topic_pattern="system.alerts.*", goal_id=goal.goal_id)

    # Publish an event
    evt = ProactiveEvent(
        topic="system.alerts.cpu_high",
        payload={"metric": "cpu", "value": 99},
        source="metric_collector",
    )
    matched = dispatcher.publish_event(evt)
    assert matched == 1

    # Poll dispatcher to route event and schedule goal
    dispatched_gids = dispatcher.poll_and_dispatch(goal_scheduler=scheduler, goal_engine=engine)
    assert dispatched_gids == [goal.goal_id]

    # Verify observation was recorded in goal store
    obs = engine.get_observations(goal.goal_id)
    assert len(obs) == 1
    assert obs[0].source == "event:system.alerts.cpu_high"
    assert obs[0].data == {"metric": "cpu", "value": 99}

    # Step scheduler to execute triggered goal
    batch = scheduler.step_next_batch(engine)
    assert len(batch) == 1
    assert engine.get_goal(goal.goal_id).status == GoalStatus.COMPLETED


def test_human_interactive_clarification_roundtrip():
    skill_reg = SkillRegistry()
    skill_reg.register(
        Skill(
            name="clarification_demo_skill",
            description="Skill requiring user disambiguation",
            handler=lambda inp: f"Resolved: {inp}",
        )
    )

    runtime = AgenticRuntime(skill_registry=skill_reg)
    engine = runtime.goal_engine
    clarif_gw = runtime.get_clarification_gateway()

    goal = engine.create_goal(
        title="Clarification Demo Goal",
        success_criteria=("clarification_demo_skill",),
    )

    # Request clarification directly via gateway
    req = clarif_gw.request_clarification(
        goal_id=goal.goal_id,
        task_id=f"goal_{goal.goal_id}_act_1",
        question="Which deployment target should be used?",
        options=["target_alpha", "target_beta"],
    )

    # Evaluating goal while clarification is pending should pause
    res = engine.evaluate_goal(goal.goal_id)
    assert res.is_completed is False
    assert "awaiting user clarification" in res.rationale

    # User submits response
    resp = clarif_gw.submit_response(req.clarification_id, "target_alpha")
    assert resp.status == ClarificationStatus.ANSWERED

    # Ingest user answer as observation and complete goal
    engine.add_observation(
        goal_id=goal.goal_id,
        source="user:clarification",
        data="clarification_demo_skill target_alpha",
    )

    res_resumed = engine.evaluate_goal(goal.goal_id)
    assert res_resumed.is_completed is True
    assert engine.get_goal(goal.goal_id).status == GoalStatus.COMPLETED


def test_security_and_taint_invariance_under_concurrency():
    sensitive_tool = SensitiveTool()
    tool_reg = ToolRegistry()
    tool_reg.register(sensitive_tool.name, sensitive_tool)

    skill_reg = SkillRegistry()
    skill_reg.register(
        Skill(
            name="deploy_skill",
            description="Deploys production updates",
            handler=lambda inp: sensitive_tool.execute(str(inp)),
            tools=["sensitive_deploy"],
        )
    )

    policy = Policy(authorized_tools={"sensitive_deploy"})
    gateway = ApprovalGateway(policy=policy, sensitive_tools={"sensitive_deploy"}, auto_approve=False)

    runtime = AgenticRuntime(
        skill_registry=skill_reg,
        policy=policy,
        approval_gateway=gateway,
    )
    engine = runtime.goal_engine
    dispatcher = runtime.get_event_dispatcher()
    scheduler = runtime.get_scheduler()

    # Create sensitive goal
    goal = engine.create_goal(
        title="Production Secure Deploy",
        success_criteria=("deploy_skill",),
    )
    dispatcher.subscribe("deploy.trigger", goal_id=goal.goal_id)

    # Publish tainted payload
    tainted_payload = wrap_tainted("malicious_config_payload", is_untrusted=True, source_type="untrusted_webhook")
    dispatcher.publish_event(ProactiveEvent(topic="deploy.trigger", payload=tainted_payload))

    # Dispatch event
    dispatcher.poll_and_dispatch(goal_scheduler=scheduler, goal_engine=engine)

    # Verify taint was preserved on goal observation
    obs = engine.get_observations(goal.goal_id)
    assert len(obs) == 1
    assert obs[0].is_untrusted is True

    # Step scheduler: must pause at ApprovalGateway for sensitive_deploy tool
    batch = scheduler.step_next_batch(engine)
    assert len(batch) == 1
    assert batch[0].is_completed is False
    assert "awaiting approval" in batch[0].rationale.lower()
    assert engine.get_goal(goal.goal_id).status == GoalStatus.PAUSED

    # Approval required in ApprovalGateway
    pending_reqs = gateway.list_requests(status=ApprovalStatus.PENDING)
    assert len(pending_reqs) == 1
    assert pending_reqs[0].skill_name == "deploy_skill"
