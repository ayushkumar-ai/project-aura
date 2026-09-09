import time
import pytest

from core.agent_plan import AgentPlan, AgentPlanStep, StepStatus
from core.agent_runtime import AgentRequest, AgentResult, AgentRuntime
from core.approval import ApprovalGateway, ApprovalStatus
from core.autonomous_agent import AgentLoopConfig, AutonomousAgentExecutor
from core.goal import (
    Goal,
    GoalObservation,
    GoalPriority,
    GoalProgress,
    GoalStatus,
    GoalTrigger,
    TriggerType,
)
from core.goal_engine import GoalEngine, GoalEngineConfig
from core.goal_reasoner import GoalReasoner
from core.goal_store import InMemoryGoalStore
from core.policy import Policy
from core.provenance import TaintedValue, wrap_tainted, is_tainted
from core.skill_registry import Skill, SkillRegistry
from core.task_state_store import InMemoryTaskStateStore
from core.tool_registry import ToolRegistry
from tools.echo import EchoTool
from tools.calculator import CalculatorTool
from interfaces.tool import ToolInterface


class SensitiveActionTool(ToolInterface):
    @property
    def name(self) -> str:
        return "deploy_update"

    def execute(self, input_data: str) -> str:
        return "deployed_successfully"


def test_end_to_end_proactive_goal_workflow():
    tool_reg = ToolRegistry()
    echo_tool = EchoTool()
    calc_tool = CalculatorTool()
    tool_reg.register("echo", echo_tool)
    tool_reg.register("calculator", calc_tool)

    skill_reg = SkillRegistry()
    skill_reg.register(
        Skill(
            name="prepare_report",
            description="Prepares initial computation",
            handler=lambda inp: calc_tool.execute("25 * 4"),
            tools=["calculator"],
        )
    )
    skill_reg.register(
        Skill(
            name="publish_summary",
            description="Publishes verified summary",
            handler=lambda inp: echo_tool.execute("Summary: Revenue total is 100"),
            tools=["echo"],
        )
    )

    policy = Policy(authorized_tools={"echo", "calculator"})
    gateway = ApprovalGateway(policy=policy, auto_approve=True)
    runtime = AgentRuntime(skill_registry=skill_reg, policy=policy)
    state_store = InMemoryTaskStateStore()
    executor = AutonomousAgentExecutor(
        runtime=runtime,
        state_store=state_store,
        approval_gateway=gateway,
    )
    goal_store = InMemoryGoalStore()
    reasoner = GoalReasoner(skill_registry=skill_reg)

    engine = GoalEngine(
        goal_store=goal_store,
        reasoner=reasoner,
        executor=executor,
        runtime=runtime,
        state_store=state_store,
        approval_gateway=gateway,
    )

    # 1. Create long-running Goal with schedule trigger (interval 0.0 for immediate readiness)
    schedule_trig = GoalTrigger(
        trigger_id="trig_daily",
        trigger_type=TriggerType.SCHEDULE,
        expression="0.0",
        cooldown_seconds=0.0,
    )
    goal = engine.create_goal(
        title="Daily Revenue Reconciliation",
        description="Compute and publish daily revenue",
        success_criteria=("prepare_report", "publish_summary"),
        triggers=[schedule_trig],
        priority=GoalPriority.HIGH,
    )

    assert goal.status == GoalStatus.ACTIVE
    assert goal.progress.percentage == 0.0

    # 2. Ingest external tainted observation from web stream
    untrusted_stream = wrap_tainted("Daily logs stream batch #42", source_urls=("https://finance.internal/feed",))
    engine.add_observation(
        goal_id=goal.goal_id,
        source="finance_stream",
        data=untrusted_stream,
    )

    # 3. Fire first proactive evaluation cycle (triggers prepare_report action)
    eval_1 = engine.evaluate_goal(goal.goal_id, trigger_id="trig_daily")
    assert eval_1.is_completed is False
    assert eval_1.new_progress.percentage == 0.5
    assert "prepare_report" in eval_1.new_progress.satisfied_criteria

    # 4. Fire second proactive evaluation cycle (triggers publish_summary action)
    eval_2 = engine.evaluate_goal(goal.goal_id, trigger_id="trig_daily")
    assert eval_2.is_completed is True
    assert eval_2.new_progress.percentage == 1.0
    assert "publish_summary" in eval_2.new_progress.satisfied_criteria

    # 5. Check completed goal in GoalStore
    final_goal = engine.get_goal(goal.goal_id)
    assert final_goal.status == GoalStatus.COMPLETED
    assert final_goal.action_count == 2
    assert final_goal.evaluation_count == 2


def test_proactive_goal_sensitive_approval_gating():
    sensitive_tool = SensitiveActionTool()
    tool_reg = ToolRegistry()
    tool_reg.register(sensitive_tool.name, sensitive_tool)

    skill_reg = SkillRegistry()
    skill_reg.register(
        Skill(
            name="deploy_skill",
            description="Deploys production updates",
            handler=lambda inp: sensitive_tool.execute(str(inp)),
            tools=["deploy_update"],
        )
    )

    policy = Policy(authorized_tools={"deploy_update"})
    gateway = ApprovalGateway(policy=policy, sensitive_tools={"deploy_update"}, auto_approve=False)
    runtime = AgentRuntime(skill_registry=skill_reg, policy=policy)
    state_store = InMemoryTaskStateStore()
    executor = AutonomousAgentExecutor(
        runtime=runtime,
        state_store=state_store,
        approval_gateway=gateway,
    )
    goal_store = InMemoryGoalStore()
    reasoner = GoalReasoner(skill_registry=skill_reg)

    engine = GoalEngine(
        goal_store=goal_store,
        reasoner=reasoner,
        executor=executor,
        runtime=runtime,
        state_store=state_store,
        approval_gateway=gateway,
    )

    goal = engine.create_goal(
        title="Production Deployment Goal",
        success_criteria=("deploy_skill",),
    )

    # First evaluation: action is proposed and starts executing, but hits ApprovalGateway requiring approval
    res = engine.evaluate_goal(goal.goal_id)
    assert res.is_completed is False

    g_paused = engine.get_goal(goal.goal_id)
    assert g_paused.status == GoalStatus.PAUSED
    assert "Action paused awaiting approval" in res.rationale

    # Find pending approval request in gateway and approve it
    pending_reqs = gateway.list_requests(status=ApprovalStatus.PENDING)
    assert len(pending_reqs) == 1
    gateway.approve(pending_reqs[0].approval_id)

    # Resume goal and re-evaluate
    engine.resume_goal(goal.goal_id)
    res_completed = engine.evaluate_goal(goal.goal_id)

    assert res_completed.is_completed is True
    assert res_completed.new_progress.percentage == 1.0
    assert engine.get_goal(goal.goal_id).status == GoalStatus.COMPLETED
