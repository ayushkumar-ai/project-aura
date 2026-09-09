import time
from uuid import uuid4
import pytest

from core.agent_plan import (
    AgentPlan,
    AgentPlanStep,
    ExecutionTrace,
    Observation,
    StepStatus,
)
from core.agent_runtime import AgentRequest, AgentResult, AgentRuntime
from core.approval import ApprovalDecisionType, ApprovalGateway, ApprovalRequest, ApprovalStatus
from core.autonomous_agent import (
    AgentLoopConfig,
    AutonomousAgentExecutor,
    AutonomousAgentResult,
    is_recoverable_failure,
)
from core.capability_registry import CapabilityRegistry, ModelCapability, ModelDescriptor
from core.model_router import ModelRouter, TaskRequirements
from core.policy import Policy, PolicyDecision
from core.provenance import TaintedValue, wrap_tainted, is_tainted
from core.provider_registry import ProviderRegistry
from core.skill_registry import Skill, SkillRegistry
from core.task_planner import TaskPlanner, ReplanContext
from core.task_state_store import InMemoryTaskStateStore
from core.tool_registry import ToolRegistry
from interfaces.tool import ToolInterface
from interfaces.tool_executor import ToolExecutor
from providers.fake_model import FakeModelProvider


class FlakyState:
    def __init__(self, fail_times=1):
        self.fail_times = fail_times
        self.call_count = 0

    def run(self, inp):
        self.call_count += 1
        if self.call_count <= self.fail_times:
            raise RuntimeError(f"Transient failure #{self.call_count}")
        return f"success after {self.call_count} attempts: {inp}"


def test_agent_loop_config_validation():
    cfg = AgentLoopConfig(max_plan_steps=10, max_execution_iterations=20)
    assert cfg.max_plan_steps == 10
    assert cfg.max_execution_iterations == 20

    with pytest.raises(ValueError):
        AgentLoopConfig(max_plan_steps=0)

    with pytest.raises(ValueError):
        AgentLoopConfig(max_retries_per_step=-1)


def test_linear_plan_execution():
    skill_reg = SkillRegistry()
    skill_reg.register(
        Skill(
            name="echo_skill",
            description="Echo skill",
            handler=lambda inp: f"echoed: {inp.get('message') if isinstance(inp, dict) else inp}",
        )
    )
    runtime = AgentRuntime(skill_registry=skill_reg)

    step1 = AgentPlanStep(
        step_id="step_1",
        skill_name="echo_skill",
        objective="First echo",
        input_data={"message": "hello"},
    )
    step2 = AgentPlanStep(
        step_id="step_2",
        skill_name="echo_skill",
        objective="Second echo",
        input_data={"message": {"$from_step": "step_1"}},
        dependencies=("step_1",),
    )
    plan = AgentPlan(plan_id="plan_linear", task_goal="Linear test", steps=(step1, step2))

    executor = AutonomousAgentExecutor(runtime=runtime)
    res = executor.execute_plan(plan)

    assert res.success is True
    assert res.is_paused is False
    assert res.plan.is_completed() is True
    assert len(res.trace.observations) == 2
    assert res.final_output == "echoed: echoed: hello"


def test_taint_propagation_across_steps():
    skill_reg = SkillRegistry()
    skill_reg.register(
        Skill(
            name="echo_skill",
            description="Echo skill",
            handler=lambda inp: (
                wrap_tainted(f"echoed: {inp.get('message').raw_value}", source_urls=inp.get('message').source_urls)
                if isinstance(inp, dict) and isinstance(inp.get("message"), TaintedValue)
                else (
                    wrap_tainted(f"echoed: {inp.raw_value}", source_urls=inp.source_urls)
                    if isinstance(inp, TaintedValue)
                    else f"echoed: {inp}"
                )
            ),
        )
    )
    runtime = AgentRuntime(skill_registry=skill_reg)

    tainted_val = wrap_tainted("untrusted_payload", source_urls=("https://external.org",))

    step1 = AgentPlanStep(
        step_id="step_1",
        skill_name="echo_skill",
        input_data={"message": tainted_val},
    )
    step2 = AgentPlanStep(
        step_id="step_2",
        skill_name="echo_skill",
        input_data={"message": {"$from_step": "step_1"}},
        dependencies=("step_1",),
    )
    plan = AgentPlan(plan_id="plan_taint", task_goal="Taint propagation", steps=(step1, step2))

    executor = AutonomousAgentExecutor(runtime=runtime)
    res = executor.execute_plan(plan)

    assert res.success is True
    assert len(res.trace.observations) == 2
    obs1 = res.trace.observations[0]
    obs2 = res.trace.observations[1]

    assert is_tainted(obs1.output) is True
    assert is_tainted(obs2.output) is True
    assert is_tainted(res.final_output) is True
    assert "https://external.org" in res.final_output.source_urls


def test_flaky_step_retry_recovery():
    flaky = FlakyState(fail_times=1)
    skill_reg = SkillRegistry()
    skill_reg.register(
        Skill(
            name="flaky_skill",
            description="Flaky skill",
            handler=flaky.run,
        )
    )
    runtime = AgentRuntime(skill_registry=skill_reg)

    step = AgentPlanStep(
        step_id="step_flaky",
        skill_name="flaky_skill",
        input_data="start",
        max_retries=2,
    )
    plan = AgentPlan(plan_id="plan_retry", task_goal="Retry test", steps=(step,))

    executor = AutonomousAgentExecutor(runtime=runtime)
    res = executor.execute_plan(plan)

    assert res.success is True
    assert res.plan.steps[0].retry_count == 1
    assert "success after 2 attempts" in str(res.final_output)
    assert len(res.trace.observations) == 2


def test_security_non_recoverable_fast_fail():
    skill_reg = SkillRegistry()
    skill_reg.register(
        Skill(
            name="echo_skill",
            handler=lambda inp: inp,
        )
    )
    runtime = AgentRuntime(skill_registry=skill_reg)

    policy = Policy(authorized_tools={"safe_tool"})
    gateway = ApprovalGateway(policy=policy)

    step = AgentPlanStep(
        step_id="step_denied",
        skill_name="echo_skill",
        metadata={"tools": ["dangerous_tool"]},
    )
    plan = AgentPlan(plan_id="plan_denied", task_goal="Denied test", steps=(step,))

    executor = AutonomousAgentExecutor(runtime=runtime, approval_gateway=gateway)
    res = executor.execute_plan(plan)

    assert res.success is False
    assert res.metadata.get("denied") is True
    assert "denied by Policy" in res.error
    assert res.plan.steps[0].retry_count == 0


def test_approval_pause_and_resume():
    skill_reg = SkillRegistry()
    skill_reg.register(
        Skill(
            name="sensitive_skill",
            description="Sensitive skill",
            handler=lambda inp: "sensitive executed",
            tools=["sensitive_action"],
        )
    )
    runtime = AgentRuntime(skill_registry=skill_reg)

    state_store = InMemoryTaskStateStore()
    gateway = ApprovalGateway(sensitive_tools={"sensitive_action"})

    step = AgentPlanStep(
        step_id="step_sensitive",
        skill_name="sensitive_skill",
        input_data={},
    )
    plan = AgentPlan(plan_id="plan_sensitive", task_goal="Sensitive test", steps=(step,))

    executor = AutonomousAgentExecutor(
        runtime=runtime,
        state_store=state_store,
        approval_gateway=gateway,
    )

    task_id = "task_sensitive_1"
    res1 = executor.execute_plan(plan, task_id=task_id)

    assert res1.success is False
    assert res1.is_paused is True
    assert res1.approval_request is not None
    assert res1.approval_request.step_id == "step_sensitive"
    assert res1.plan.steps[0].status == StepStatus.PAUSED

    # Now grant approval in approval gateway
    gateway.approve(res1.approval_request.approval_id)

    # Resume task
    res2 = executor.resume(task_id=task_id)
    assert res2.success is True
    assert res2.is_paused is False
    assert res2.plan.is_completed() is True
    assert res2.final_output == "sensitive executed"


def test_resource_bounds_max_execution_iterations():
    skill_reg = SkillRegistry()
    skill_reg.register(
        Skill(
            name="echo_skill",
            handler=lambda inp: inp,
        )
    )
    runtime = AgentRuntime(skill_registry=skill_reg)

    steps = [
        AgentPlanStep(step_id=f"step_{i}", skill_name="echo_skill", input_data=f"msg {i}")
        for i in range(5)
    ]
    plan = AgentPlan(plan_id="plan_limit", task_goal="Limit test", steps=tuple(steps))

    # Configure iteration limit to 2
    cfg = AgentLoopConfig(max_execution_iterations=2)
    executor = AutonomousAgentExecutor(runtime=runtime, config=cfg)
    res = executor.execute_plan(plan)

    assert res.success is False
    assert "max_execution_iterations" in res.error



def test_resource_bounds_max_total_execution_time():
    def slow_handler(inp):
        time.sleep(0.1)
        return "slow done"

    skill_reg = SkillRegistry()
    skill_reg.register(Skill(name="slow_skill", handler=slow_handler))
    runtime = AgentRuntime(skill_registry=skill_reg)

    steps = [
        AgentPlanStep(step_id=f"s_{i}", skill_name="slow_skill")
        for i in range(5)
    ]
    plan = AgentPlan(plan_id="plan_slow", steps=tuple(steps))

    # Set very small max_total_execution_time
    cfg = AgentLoopConfig(max_total_execution_time=0.15)
    executor = AutonomousAgentExecutor(runtime=runtime, config=cfg)

    res = executor.execute_plan(plan)
    assert res.success is False
    assert "max_total_execution_time limit" in res.error


def test_autonomous_replanning_flow():
    # Setup skills: step 1 succeeds, step 2 fails permanently, planner replans with step 2_alt
    call_counts = {}

    def dynamic_handler(inp, context=None):
        req_id = context.get("step_id", "unknown") if context else "unknown"
        call_counts[req_id] = call_counts.get(req_id, 0) + 1
        if "failing" in req_id:
            raise RuntimeError("Permanent failure in primary path")
        return f"success for {req_id}"

    skill_reg = SkillRegistry()
    skill_reg.register(Skill(name="step_1_skill", handler=lambda inp: "out_1"))
    skill_reg.register(Skill(name="failing_skill", handler=lambda inp: (_ for _ in ()).throw(RuntimeError("step 2 permanent failure"))))
    skill_reg.register(Skill(name="alt_skill", handler=lambda inp: f"alt_out({inp})"))

    class MockReplanner(TaskPlanner):
        def replan_agent(self, context: ReplanContext, **kwargs) -> AgentPlan:
            # Generate replacement step
            step_alt = AgentPlanStep(
                step_id="step_alt",
                skill_name="alt_skill",
                input_data={"$from_step": "step_1"},
                dependencies=("step_1",),
            )
            return AgentPlan(
                plan_id="plan_replanned_v2",
                task_goal=context.task,
                steps=(step_alt,),
            )

    runtime = AgentRuntime(skill_registry=skill_reg)
    planner = MockReplanner(skill_registry=skill_reg)

    step1 = AgentPlanStep(step_id="step_1", skill_name="step_1_skill")
    step2 = AgentPlanStep(step_id="step_failing", skill_name="failing_skill", dependencies=("step_1",), max_retries=0)
    plan = AgentPlan(plan_id="plan_init", task_goal="Replanning test", steps=(step1, step2))

    cfg = AgentLoopConfig(max_retries_per_step=0, max_replan_depth=2)
    executor = AutonomousAgentExecutor(runtime=runtime, planner=planner, config=cfg)

    res = executor.execute_plan(plan)
    assert res.success is True
    assert res.plan.is_completed() is True
    assert len(res.trace.replan_history) == 1
    assert res.trace.replan_history[0]["failed_step_id"] == "step_failing"
    assert res.final_output == "alt_out(out_1)"
