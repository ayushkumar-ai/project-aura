import json
from uuid import UUID, uuid4
import pytest

from core.agentic_runtime import AgenticRuntime
from core.approval import ApprovalGateway
from core.capability_registry import CapabilityRegistry, ModelCapability, ModelDescriptor
from core.model_router import ModelRouter
from core.models import AURARequest, AURAResponse
from core.orchestrator import Orchestrator
from core.policy import Policy
from core.provider_registry import ProviderRegistry
from core.skill_registry import Skill, SkillRegistry
from core.task_state import StepStatus, TaskStatus
from core.task_state_store import InMemoryTaskStateStore
from core.tool_registry import ToolRegistry
from interfaces.model import ModelInterface
from interfaces.tool_executor import ToolExecutor
from research.models import SearchItem, WebDocument
from research.providers.fake import FakeWebProvider
from research.service import ResearchService
from research.skill import create_research_skill
from research.tool import WebSearchTool


class ProgrammableModel(ModelInterface):
    """Test model double supporting canned sequential responses and prompt logging."""

    def __init__(self, responses: list[str] | None = None):
        self.responses = list(responses) if responses is not None else []
        self.recorded_prompts: list[str] = []
        self.calls: int = 0

    def generate(self, prompt: str, request_id: UUID) -> AURAResponse:
        self.recorded_prompts.append(prompt)
        self.calls += 1
        if not self.responses:
            return AURAResponse(request_id=request_id, content="{}")
        content = self.responses.pop(0)
        return AURAResponse(request_id=request_id, content=content)


@pytest.fixture
def research_stack():
    """Builds a complete, cooperative AURA component stack equipped with research capabilities."""
    cap_reg = CapabilityRegistry()
    prov_reg = ProviderRegistry()
    skill_reg = SkillRegistry()
    tool_reg = ToolRegistry()
    store = InMemoryTaskStateStore()

    # Configure FakeWebProvider
    web_provider = FakeWebProvider(
        default_items=[
            SearchItem(title="Quantum Progress 2026", url="https://quantum-daily.org/article1", snippet="Breakthrough in logical qubits."),
            SearchItem(title="Tech Review Quantum", url="https://techreview.com/quantum", snippet="10,000 physical qubits achieved."),
        ],
        documents_by_url={
            "https://quantum-daily.org/article1": WebDocument(
                url="https://quantum-daily.org/article1",
                title="Quantum Progress 2026",
                content="Scientists have demonstrated 1,000 fault-tolerant logical qubits in 2026.",
            ),
            "https://techreview.com/quantum": WebDocument(
                url="https://techreview.com/quantum",
                title="Tech Review Quantum",
                content="Quantum computing reached commercial viability with hybrid error correction.",
            ),
        },
    )
    research_service = ResearchService(
        search_provider=web_provider,
        fetch_provider=web_provider,
        max_search_results=5,
        max_fetch_sources=3,
    )

    # Register WebSearchTool
    search_tool = WebSearchTool(service=research_service)
    tool_reg.register("web_search", search_tool)

    # Register research skill
    research_skill = create_research_skill(service=research_service)
    skill_reg.register(research_skill)

    # Register models
    planner_and_synthesis_model = ProgrammableModel()
    prov_reg.register("primary_prov", planner_and_synthesis_model)
    cap_reg.register_model(
        ModelDescriptor(
            model_id="primary_m",
            provider_id="primary_prov",
            capabilities={ModelCapability.REASONING},
        )
    )
    router = ModelRouter(cap_reg, prov_reg)

    policy = Policy(authorized_tools={"web_search", "echo"})
    tool_executor = ToolExecutor(registry=tool_reg, policy=policy)

    agentic_runtime = AgenticRuntime(
        skill_registry=skill_reg,
        model_router=router,
        tool_executor=tool_executor,
        policy=policy,
        state_store=store,
    )

    return {
        "agentic_runtime": agentic_runtime,
        "model": planner_and_synthesis_model,
        "web_provider": web_provider,
        "service": research_service,
        "store": store,
        "tool_reg": tool_reg,
        "skill_reg": skill_reg,
        "policy": policy,
        "tool_executor": tool_executor,
        "router": router,
    }


def test_end_to_end_research_task_execution(research_stack):
    """Verify natural-language research task plans, executes web research, and synthesizes attributed response."""
    runtime = research_stack["agentic_runtime"]
    model = research_stack["model"]
    store = research_stack["store"]

    # 1. Model response for TaskPlanner: plan with research_web skill
    plan_json = json.dumps({
        "steps": [
            {
                "step_id": "research_step",
                "skill_name": "research_web",
                "input_data": {"query": "latest quantum computing achievements"},
            }
        ]
    })
    # 2. Model response for Research synthesis
    synthesis_json = "Recent achievements show 1,000 fault-tolerant logical qubits [1] and commercial viability [2]."

    model.responses.append(plan_json)
    model.responses.append(synthesis_json)

    result = runtime.execute_task(
        task="Research the latest quantum computing achievements",
        task_id="task_research_1",
    )

    assert result.success is True
    assert result.task_id == "task_research_1"
    assert result.executed_steps == ["research_step"]
    assert "Recent achievements show" in result.final_output
    assert "Sources:" in result.final_output
    assert "[1] Quantum Progress 2026 - https://quantum-daily.org/article1" in result.final_output
    assert "[2] Tech Review Quantum - https://techreview.com/quantum" in result.final_output

    # Verify task state in TaskStateStore
    task_state = store.get("task_research_1")
    assert task_state.status == TaskStatus.COMPLETED
    assert task_state.step_states["research_step"].status == StepStatus.COMPLETED


def test_research_skill_fault_isolation_in_workflow(research_stack):
    """Verify that if one web source times out during workflow execution, remaining sources succeed."""
    runtime = research_stack["agentic_runtime"]
    model = research_stack["model"]
    web_provider = research_stack["web_provider"]

    # Simulate timeout for first URL
    web_provider.simulate_timeout_urls.add("https://quantum-daily.org/article1")

    plan_json = json.dumps({
        "steps": [
            {
                "step_id": "r1",
                "skill_name": "research_web",
                "input_data": {"query": "quantum"},
            }
        ]
    })
    synthesis_json = "Quantum computing has reached commercial viability [1]."

    model.responses.append(plan_json)
    model.responses.append(synthesis_json)

    result = runtime.execute_task("Research quantum with partial failure", task_id="task_res_fault")

    assert result.success is True
    assert "Tech Review Quantum" in result.final_output


def test_orchestrator_single_turn_tool_execution(research_stack):
    """Verify Orchestrator can execute WebSearchTool directly via tool resolution/invocation."""
    model = research_stack["model"]
    policy = research_stack["policy"]
    tool_reg = research_stack["tool_reg"]
    tool_executor = research_stack["tool_executor"]

    orchestrator = Orchestrator(
        model=model,
        policy=policy,
        tool_registry=tool_reg,
        tool_executor=tool_executor,
    )

    request = AURARequest(
        user_input="Search quantum",
        metadata={
            "tool": "web_search",
            "tool_input": "quantum status",
        },
    )

    response = orchestrator.run(request)
    assert response.metadata.get("tool") == "web_search"
    parsed = json.loads(response.content)
    assert parsed["query"] == "quantum status"
    assert len(parsed["sources"]) == 2
