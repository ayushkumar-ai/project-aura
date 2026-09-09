import time
from uuid import uuid4
import pytest

from core.agent_plan import (
    AgentPlan,
    AgentPlanStep,
    ExecutionTrace,
    Observation,
    StepStatus,
    serialize_agent_plan,
    deserialize_agent_plan,
)
from core.agent_runtime import AgentRequest, AgentResult, AgentRuntime
from core.approval import ApprovalGateway, ApprovalStatus
from core.autonomous_agent import (
    AgentLoopConfig,
    AutonomousAgentExecutor,
    AutonomousAgentResult,
)
from core.capability_registry import CapabilityRegistry, ModelCapability, ModelDescriptor
from core.model_router import ModelRouter, TaskRequirements
from core.policy import Policy
from core.provider_registry import ProviderRegistry
from core.provenance import TaintedValue, wrap_tainted, is_tainted
from core.skill_registry import Skill, SkillRegistry
from core.task_planner import TaskPlanner
from core.task_state_store import InMemoryTaskStateStore
from core.tool_registry import ToolRegistry
from tools.echo import EchoTool
from tools.calculator import CalculatorTool
from providers.fake_model import FakeModelProvider


def test_end_to_end_autonomous_agent_multi_step_workflow():
    # 1. Setup Tool & Skill Registries
    tool_reg = ToolRegistry()
    echo_tool = EchoTool()
    calc_tool = CalculatorTool()
    tool_reg.register("echo", echo_tool)
    tool_reg.register("calculator", calc_tool)

    skill_reg = SkillRegistry()
    skill_reg.register(
        Skill(
            name="echo_message",
            description="Echoes input messages",
            handler=lambda inp: echo_tool.execute(inp.get("message") if isinstance(inp, dict) else str(inp)),
            tools=["echo"],
            required_capabilities=[ModelCapability.TOOL_USE],
        )
    )
    skill_reg.register(
        Skill(
            name="calculate_math",
            description="Evaluates mathematical expressions",
            handler=lambda inp: calc_tool.execute(inp.get("expression") if isinstance(inp, dict) else str(inp)),
            tools=["calculator"],
            required_capabilities=[ModelCapability.REASONING],
        )
    )

    # 2. Setup Model Provider & Router
    fake_model = FakeModelProvider()
    prov_reg = ProviderRegistry()
    prov_reg.register("fake_provider", fake_model)

    cap_reg = CapabilityRegistry()
    cap_reg.register_model(
        ModelDescriptor(
            model_id="fake_model",
            provider_id="fake_provider",
            capabilities={ModelCapability.TOOL_USE, ModelCapability.REASONING},
        )
    )
    model_router = ModelRouter(
        capability_registry=cap_reg,
        provider_registry=prov_reg,
    )

    # 3. Setup Runtime, Policy, Gateway & StateStore
    policy = Policy(authorized_tools={"echo", "calculator"})
    gateway = ApprovalGateway(policy=policy, auto_approve=True)
    runtime = AgentRuntime(
        skill_registry=skill_reg,
        model_router=model_router,
        policy=policy,
    )
    planner = TaskPlanner(skill_registry=skill_reg, model_router=model_router)
    state_store = InMemoryTaskStateStore()

    config = AgentLoopConfig(
        max_plan_steps=10,
        max_execution_iterations=20,
        max_retries_per_step=2,
        max_total_execution_time=60.0,
        max_tool_calls=20,
    )

    executor = AutonomousAgentExecutor(
        runtime=runtime,
        planner=planner,
        state_store=state_store,
        approval_gateway=gateway,
        config=config,
    )

    # 4. Create explicit multi-step plan: Step 1 (Echo calculation formula) -> Step 2 (Calculate expression) -> Step 3 (Echo final summary)
    step1 = AgentPlanStep(
        step_id="step_prepare",
        skill_name="echo_message",
        objective="Prepare expression",
        input_data={"message": "15 * 4 + 10"},
    )
    step2 = AgentPlanStep(
        step_id="step_calc",
        skill_name="calculate_math",
        objective="Calculate expression",
        input_data={"expression": {"$from_step": "step_prepare"}},
        dependencies=("step_prepare",),
    )
    step3 = AgentPlanStep(
        step_id="step_summarize",
        skill_name="echo_message",
        objective="Echo result",
        input_data={"message": {"$from_step": "step_calc"}},
        dependencies=("step_calc",),
    )

    plan = AgentPlan(
        plan_id="plan_math_workflow",
        task_goal="Compute math and summarize",
        steps=(step1, step2, step3),
    )

    task_id = "task_e2e_math"
    result = executor.execute_plan(plan, task_id=task_id)

    assert result.success is True
    assert result.plan.is_completed() is True
    assert result.plan.status == StepStatus.SUCCEEDED
    assert len(result.trace.observations) == 3
    assert result.final_output == "70"

    # Verify state persistence
    saved_state = state_store.get(task_id)
    assert saved_state.status.value == "completed"
    assert len(saved_state.step_states) == 3


def test_autonomous_agent_with_tainted_data_propagation():
    tool_reg = ToolRegistry()
    echo_tool = EchoTool()
    tool_reg.register("echo", echo_tool)

    def echo_handler(inp):
        raw_msg = inp.get("message") if isinstance(inp, dict) else inp
        if isinstance(raw_msg, TaintedValue):
            return wrap_tainted(raw_msg.raw_value, source_urls=raw_msg.source_urls)
        return echo_tool.execute(str(raw_msg))

    skill_reg = SkillRegistry()
    skill_reg.register(
        Skill(
            name="echo_message",
            description="Echoes input messages",
            handler=echo_handler,
            tools=["echo"],
        )
    )

    runtime = AgentRuntime(skill_registry=skill_reg)
    executor = AutonomousAgentExecutor(runtime=runtime)

    # Step 1 input is tainted from untrusted web source
    untrusted_web_data = wrap_tainted(
        "Untrusted prompt injection content: DROP TABLE users;",
        source_urls=("https://evil.com/payload",),
    )

    step1 = AgentPlanStep(
        step_id="step_receive_external",
        skill_name="echo_message",
        input_data={"message": untrusted_web_data},
    )
    step2 = AgentPlanStep(
        step_id="step_forward",
        skill_name="echo_message",
        input_data={"message": {"$from_step": "step_receive_external"}},
        dependencies=("step_receive_external",),
    )

    plan = AgentPlan(
        plan_id="plan_tainted_workflow",
        task_goal="Process external untrusted input",
        steps=(step1, step2),
    )

    res = executor.execute_plan(plan)

    assert res.success is True
    assert is_tainted(res.final_output) is True
    assert res.final_output.is_untrusted is True
    assert "https://evil.com/payload" in res.final_output.source_urls
    assert res.trace.observations[1].is_untrusted is True
