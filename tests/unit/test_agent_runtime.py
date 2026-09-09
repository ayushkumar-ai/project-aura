import time
import pytest
from uuid import uuid4

from core.agent_runtime import AgentRequest, AgentResult, AgentRuntime
from core.capability_registry import (
    CapabilityRegistry,
    ModelCapability,
    ModelDescriptor,
)
from core.model_router import ModelRouter, TaskRequirements
from core.policy import Policy, PolicyDecision
from core.provider_registry import ProviderRegistry
from core.skill_registry import Skill, SkillRegistry
from core.tool_registry import ToolRegistry
from interfaces.model import ModelInterface
from interfaces.tool import ToolInterface
from interfaces.tool_executor import ToolExecutor
from providers.fake_model import FakeModelProvider


class MockTool(ToolInterface):
    def __init__(self, name: str = "mock_calc"):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def execute(self, input_data: str) -> str:
        return f"tool_out:{input_data}"


@pytest.fixture
def setup_environment():
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

    return {
        "cap_reg": cap_reg,
        "prov_reg": prov_reg,
        "skill_reg": skill_reg,
        "tool_reg": tool_reg,
        "model_router": model_router,
        "tool_executor": tool_executor,
        "policy": policy,
        "p_fake": p_fake,
    }


def test_agent_request_and_result_dataclasses():
    req_id = uuid4()
    req = AgentRequest(
        skill_name="test_skill",
        input_data={"query": "hello"},
        request_id=req_id,
        metadata={"key": "val"},
    )
    assert req.skill_name == "test_skill"
    assert req.input_data == {"query": "hello"}
    assert req.request_id == req_id
    assert req.metadata == {"key": "val"}

    res = AgentResult(
        success=True,
        skill_name="test_skill",
        output="result",
        selected_model_id="gpt-4",
        selected_provider_id="openai",
        request_id=req_id,
    )
    assert res.success is True
    assert res.output == "result"
    assert res.selected_model_id == "gpt-4"
    assert res.selected_provider_id == "openai"


def test_agent_request_validation():
    with pytest.raises(ValueError, match="skill_name must be a non-empty string"):
        AgentRequest(skill_name="")

    with pytest.raises(ValueError, match="skill_name must be a non-empty string"):
        AgentRequest(skill_name="   ")

    with pytest.raises(TypeError, match="request_id must be an instance of UUID"):
        AgentRequest(skill_name="skill", request_id="not_a_uuid")

    with pytest.raises(TypeError, match="task_requirements"):
        AgentRequest(skill_name="skill", task_requirements="not_task_req")

    with pytest.raises(TypeError, match="metadata must be a dict"):
        AgentRequest(skill_name="skill", metadata="not_a_dict")


def test_agent_runtime_init_validation(setup_environment):
    env = setup_environment
    runtime = AgentRuntime(
        skill_registry=env["skill_reg"],
        model_router=env["model_router"],
        tool_executor=env["tool_executor"],
        policy=env["policy"],
    )
    assert runtime.skill_registry is env["skill_reg"]
    assert runtime.model_router is env["model_router"]

    with pytest.raises(TypeError, match="skill_registry"):
        AgentRuntime(skill_registry="invalid")

    with pytest.raises(TypeError, match="model_router"):
        AgentRuntime(skill_registry=env["skill_reg"], model_router="invalid")

    with pytest.raises(TypeError, match="tool_executor"):
        AgentRuntime(skill_registry=env["skill_reg"], tool_executor="invalid")

    with pytest.raises(TypeError, match="policy"):
        AgentRuntime(skill_registry=env["skill_reg"], policy="invalid")


def test_valid_skill_execution_simple_handler(setup_environment):
    env = setup_environment
    skill = Skill(
        name="echo_skill",
        handler=lambda inp: f"Echo: {inp}",
    )
    env["skill_reg"].register(skill)

    runtime = AgentRuntime(
        skill_registry=env["skill_reg"],
        model_router=env["model_router"],
    )
    result = runtime.execute(AgentRequest(skill_name="echo_skill", input_data="Hello World"))

    assert result.success is True
    assert result.skill_name == "echo_skill"
    assert result.output == "Echo: Hello World"
    assert result.error is None


def test_skill_execution_by_string_name(setup_environment):
    env = setup_environment
    skill = Skill(
        name="greet",
        handler=lambda inp: "Hello!",
    )
    env["skill_reg"].register(skill)

    runtime = AgentRuntime(skill_registry=env["skill_reg"])
    result = runtime.run("greet")

    assert result.success is True
    assert result.output == "Hello!"


def test_unknown_skill(setup_environment):
    env = setup_environment
    runtime = AgentRuntime(skill_registry=env["skill_reg"])
    result = runtime.execute("unknown_skill")

    assert result.success is False
    assert "Unknown skill: unknown_skill" in result.error


def test_skill_with_required_capabilities_routes_model(setup_environment):
    env = setup_environment
    skill = Skill(
        name="reasoning_skill",
        required_capabilities={ModelCapability.REASONING},
        handler=lambda inp, context: f"Model: {context['model_id']} Output: {inp}",
    )
    env["skill_reg"].register(skill)

    runtime = AgentRuntime(
        skill_registry=env["skill_reg"],
        model_router=env["model_router"],
    )
    result = runtime.execute(AgentRequest(skill_name="reasoning_skill", input_data="test"))

    assert result.success is True
    assert result.selected_model_id == "fake_model"
    assert result.selected_provider_id == "fake_prov"
    assert result.output == "Model: fake_model Output: test"


def test_missing_handler_behavior(setup_environment):
    env = setup_environment
    skill = Skill(name="no_handler_skill", handler=None)
    env["skill_reg"].register(skill)

    runtime = AgentRuntime(skill_registry=env["skill_reg"])
    result = runtime.execute("no_handler_skill")

    assert result.success is False
    assert "has no execution handler" in result.error


def test_handler_failure_behavior(setup_environment):
    env = setup_environment

    def failing_handler(inp):
        raise ValueError("Something broke inside handler")

    skill = Skill(name="broken_skill", handler=failing_handler)
    env["skill_reg"].register(skill)

    runtime = AgentRuntime(skill_registry=env["skill_reg"])
    result = runtime.execute("broken_skill")

    assert result.success is False
    assert "Handler execution failed: Something broke inside handler" in result.error


def test_input_schema_validation_success_and_failure(setup_environment):
    env = setup_environment
    skill = Skill(
        name="validated_skill",
        input_schema={"type": "object", "required": ["query", "limit"]},
        handler=lambda inp: f"Query: {inp['query']}",
    )
    env["skill_reg"].register(skill)

    runtime = AgentRuntime(skill_registry=env["skill_reg"])

    # Valid input
    res_valid = runtime.execute(
        AgentRequest(skill_name="validated_skill", input_data={"query": "search", "limit": 10})
    )
    assert res_valid.success is True
    assert res_valid.output == "Query: search"

    # Missing field
    res_missing = runtime.execute(
        AgentRequest(skill_name="validated_skill", input_data={"query": "search"})
    )
    assert res_missing.success is False
    assert "Missing required input field: 'limit'" in res_missing.error

    # Wrong type
    res_wrong_type = runtime.execute(
        AgentRequest(skill_name="validated_skill", input_data="not_a_dict")
    )
    assert res_wrong_type.success is False
    assert "Input data must be a dictionary" in res_wrong_type.error


def test_model_routing_failure_unsupported_capability(setup_environment):
    env = setup_environment
    # Skill requires AUDIO, but fake_model only has REASONING and CODING
    skill = Skill(
        name="audio_skill",
        required_capabilities={ModelCapability.AUDIO},
        handler=lambda inp: "audio",
    )
    env["skill_reg"].register(skill)

    runtime = AgentRuntime(
        skill_registry=env["skill_reg"],
        model_router=env["model_router"],
    )
    result = runtime.execute("audio_skill")

    assert result.success is False
    assert "Model routing failed" in result.error


def test_tool_execution_through_tool_executor(setup_environment):
    env = setup_environment

    def handler_with_tool(inp, context):
        executor = context["tool_executor"]
        return executor.execute("calculator", inp)

    skill = Skill(
        name="math_skill",
        tools=("calculator",),
        handler=handler_with_tool,
    )
    env["skill_reg"].register(skill)

    runtime = AgentRuntime(
        skill_registry=env["skill_reg"],
        tool_executor=env["tool_executor"],
    )
    result = runtime.execute(AgentRequest(skill_name="math_skill", input_data="2+2"))

    assert result.success is True
    assert result.output == "tool_out:2+2"


def test_declared_tools_do_not_bypass_policy_authorization(setup_environment):
    env = setup_environment

    # Register an unauthorized tool in tool registry
    unauth_tool = MockTool("unauthorized_bash")
    env["tool_reg"].register("unauthorized_bash", unauth_tool)

    def handler_calling_unauth_tool(inp, context):
        executor = context["tool_executor"]
        return executor.execute("unauthorized_bash", inp)

    # Skill declares it, but Policy only authorizes calculator
    skill = Skill(
        name="hack_skill",
        tools=("unauthorized_bash",),
        handler=handler_calling_unauth_tool,
    )
    env["skill_reg"].register(skill)

    runtime = AgentRuntime(
        skill_registry=env["skill_reg"],
        tool_executor=env["tool_executor"],
    )
    result = runtime.execute(AgentRequest(skill_name="hack_skill", input_data="ls"))

    assert result.success is False
    assert "is not authorized" in result.error


def test_runtime_timeout_handling(setup_environment):
    env = setup_environment

    def slow_handler(inp):
        time.sleep(0.5)
        return "done"

    skill = Skill(name="slow_skill", handler=slow_handler)
    env["skill_reg"].register(skill)

    runtime = AgentRuntime(
        skill_registry=env["skill_reg"],
        default_timeout=0.05,
    )
    result = runtime.execute("slow_skill")

    assert result.success is False
    assert "timed out after" in result.error


def test_agent_module_alias_imports():
    from core.agent import AgentRequest as Req
    from core.agent import AgentResult as Res
    from core.agent import AgentRuntime as Run

    assert Req is AgentRequest
    assert Res is AgentResult
    assert Run is AgentRuntime
