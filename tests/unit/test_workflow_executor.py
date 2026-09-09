import pytest

from core.agent_runtime import AgentRequest, AgentResult, AgentRuntime
from core.capability_registry import (
    CapabilityRegistry,
    ModelCapability,
    ModelDescriptor,
)
from core.model_router import ModelRouter
from core.policy import Policy
from core.provider_registry import ProviderRegistry
from core.skill_registry import Skill, SkillRegistry
from core.task_planner import ExecutionPlan, PlanStep, TaskPlanner
from core.tool_registry import ToolRegistry
from core.workflow_executor import WorkflowExecutor, WorkflowResult
from interfaces.tool import ToolInterface
from interfaces.tool_executor import ToolExecutor
from providers.fake_model import FakeModelProvider


class MockTool(ToolInterface):
    def __init__(self, name: str = "calculator"):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def execute(self, input_data: str) -> str:
        return f"tool:{input_data}"


@pytest.fixture
def workflow_setup():
    cap_reg = CapabilityRegistry()
    prov_reg = ProviderRegistry()
    skill_reg = SkillRegistry()
    tool_reg = ToolRegistry()

    p_fake = FakeModelProvider()
    prov_reg.register("fake_prov", p_fake)

    m_desc = ModelDescriptor(
        model_id="fake_model",
        provider_id="fake_prov",
        capabilities={ModelCapability.REASONING, ModelCapability.CODING},
    )
    cap_reg.register_model(m_desc)

    model_router = ModelRouter(cap_reg, prov_reg)

    tool = MockTool("calculator")
    tool_reg.register("calculator", tool)
    policy = Policy(authorized_tools={"calculator"})
    tool_executor = ToolExecutor(registry=tool_reg, policy=policy)

    # Register Skills
    skill_reg.register(
        Skill(name="step_a_skill", handler=lambda inp: f"A({inp})")
    )
    skill_reg.register(
        Skill(name="step_b_skill", handler=lambda inp: f"B({inp})")
    )
    skill_reg.register(
        Skill(name="step_c_skill", handler=lambda inp: f"C({inp})")
    )

    runtime = AgentRuntime(
        skill_registry=skill_reg,
        model_router=model_router,
        tool_executor=tool_executor,
        policy=policy,
    )

    planner = TaskPlanner(skill_reg)
    executor = WorkflowExecutor(runtime=runtime, planner=planner)

    return {
        "skill_reg": skill_reg,
        "runtime": runtime,
        "planner": planner,
        "executor": executor,
        "tool_reg": tool_reg,
    }


def test_single_step_execution(workflow_setup):
    setup = workflow_setup
    planner = setup["planner"]
    executor = setup["executor"]

    plan = planner.create_plan([
        PlanStep(step_id="step_1", skill_name="step_a_skill", input_data="init")
    ])

    result = executor.execute(plan)

    assert result.success is True
    assert result.executed_steps == ["step_1"]
    assert result.final_output == "A(init)"
    assert "step_1" in result.step_results
    assert result.step_results["step_1"].output == "A(init)"


def test_multi_step_sequential_execution_and_data_flow(workflow_setup):
    setup = workflow_setup
    planner = setup["planner"]
    executor = setup["executor"]

    # Step 1: produces A(start)
    # Step 2: depends on step 1, consumes output -> B(A(start))
    # Step 3: depends on step 2, consumes output -> C(B(A(start)))
    plan = planner.create_plan([
        PlanStep(step_id="s1", skill_name="step_a_skill", input_data="start"),
        PlanStep(step_id="s2", skill_name="step_b_skill", dependencies=("s1",)),
        PlanStep(step_id="s3", skill_name="step_c_skill", dependencies=("s2",)),
    ])

    result = executor.execute(plan)

    assert result.success is True
    assert result.executed_steps == ["s1", "s2", "s3"]
    assert result.final_output == "C(B(A(start)))"
    assert result.step_results["s1"].output == "A(start)"
    assert result.step_results["s2"].output == "B(A(start))"
    assert result.step_results["s3"].output == "C(B(A(start)))"


def test_custom_data_flow_using_from_step(workflow_setup):
    setup = workflow_setup
    planner = setup["planner"]
    executor = setup["executor"]

    plan = planner.create_plan([
        PlanStep(step_id="s1", skill_name="step_a_skill", input_data="data"),
        PlanStep(
            step_id="s2",
            skill_name="step_c_skill",
            input_data={"$from_step": "s1"},
            dependencies=("s1",),
        ),
    ])

    result = executor.execute(plan)

    assert result.success is True
    assert result.final_output == "C(A(data))"


def test_failed_step_behavior_halts_dependent_steps(workflow_setup):
    setup = workflow_setup
    skill_reg = setup["skill_reg"]
    planner = setup["planner"]
    executor = setup["executor"]

    def failing_handler(inp):
        raise RuntimeError("Failure in Step B")

    skill_reg.register(Skill(name="failing_skill", handler=failing_handler))

    plan = planner.create_plan([
        PlanStep(step_id="s1", skill_name="step_a_skill", input_data="init"),
        PlanStep(step_id="s2", skill_name="failing_skill", dependencies=("s1",)),
        PlanStep(step_id="s3", skill_name="step_c_skill", dependencies=("s2",)),
    ])

    result = executor.execute(plan)

    assert result.success is False
    assert result.failed_step_id == "s2"
    assert result.executed_steps == ["s1"]
    assert "s3" not in result.step_results
    assert "Failure in Step B" in result.error


def test_tool_authorization_enforced_in_workflow(workflow_setup):
    setup = workflow_setup
    skill_reg = setup["skill_reg"]
    tool_reg = setup["tool_reg"]
    planner = setup["planner"]
    executor = setup["executor"]

    # Register an unauthorized tool
    tool_reg.register("bash", MockTool("bash"))

    def unauth_handler(inp, context):
        executor_tool = context["tool_executor"]
        return executor_tool.execute("bash", "rm -rf")

    skill_reg.register(Skill(name="unauth_skill", handler=unauth_handler))

    plan = planner.create_plan([
        PlanStep(step_id="s1", skill_name="unauth_skill")
    ])

    result = executor.execute(plan)

    assert result.success is False
    assert result.failed_step_id == "s1"
    assert "is not authorized" in result.error


def test_workflow_executor_alias_module_imports():
    from core.workflow import WorkflowExecutor as Exec
    from core.workflow import WorkflowResult as Res

    assert Exec is WorkflowExecutor
    assert Res is WorkflowResult
