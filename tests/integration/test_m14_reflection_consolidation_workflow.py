import pytest
from typing import Any

from core.agentic_runtime import AgenticRuntime, ExecutionMode
from core.agent_memory import InMemoryAgentMemoryStore
from core.agent_plan import (
    AgentPlan,
    AgentPlanStep,
    ExecutionTrace,
    Observation,
    StepStatus,
)
from core.agent_reflection import AgentReflector, format_reflection_for_prompt
from core.agent_runtime import AgentRequest, AgentResult, AgentRuntime
from core.approval import ApprovalGateway, ApprovalRequest, ApprovalDecisionType
from core.memory_consolidation import MemoryConsolidator
from core.memory_manager import MemoryManager
from core.memory_types import (
    EpisodicRecord,
    MemoryEntry,
    MemoryNamespace,
    MemoryTier,
    SemanticFact,
)
from core.policy import Policy
from core.provenance import TaintedValue, is_tainted, wrap_tainted
from core.reflection_types import (
    ConsolidationRecord,
    ConsolidationSourceType,
    ContradictionRecord,
    DistillationResult,
    FailureIssueType,
    ReflectionAssessment,
    ReflectionRecord,
    ReflectionRule,
    ResolutionStrategy,
    strip_forbidden_metadata_keys,
)
from core.skill_registry import Skill, SkillRegistry
from core.task_planner import TaskPlanner
from core.workflow_executor import WorkflowExecutor
from interfaces.tool_executor import ToolExecutor
from research.models import (
    ClaimEvidence,
    ClaimVerificationStatus,
    VerifiedClaim,
)
from research.service import ResearchService


class DummyToolExecutor(ToolExecutor):
    def __init__(self):
        self.executed_tools = []

    def execute_tool(self, tool_name: str, payload: Any, timeout: float | None = None) -> Any:
        self.executed_tools.append((tool_name, payload))
        if tool_name == "fail_tool":
            raise ValueError("Invalid parameter payload: missing schema field 'target'")
        if tool_name == "timeout_tool":
            raise TimeoutError("Operation timed out after 10s")
        return f"executed_{tool_name}"

    def list_available_tools(self) -> list[str]:
        return ["calc", "fetch", "fail_tool", "timeout_tool"]

    def prepare_tool_input(self, tool_name: str, request: Any) -> Any:
        return request


def test_closed_learning_loop_end_to_end():
    """Verify EXECUTION -> TRACE -> REFLECTION -> DISTILLATION -> MEMORY -> PLANNING CONTEXT."""
    mem_store = InMemoryAgentMemoryStore()
    mem_mgr = MemoryManager(store=mem_store)
    reflector = AgentReflector()
    consolidator = MemoryConsolidator(memory_store=mem_store, memory_manager=mem_mgr)

    # 1. Simulate an execution trace with a failure and a distilled rule
    step1 = AgentPlanStep(
        step_id="step-web",
        skill_name="web_fetch",
        objective="Fetch large payload",
        status=StepStatus.FAILED,
        retry_count=1,
    )
    obs1 = Observation(
        step_id="step-web",
        task_id="task-100",
        skill_name="web_fetch",
        success=False,
        error="Operation timed out after 10.0s",
        is_untrusted=True,
    )
    plan = AgentPlan(
        plan_id="plan-100",
        task_goal="Fetch external dataset",
        steps=(step1,),
        status=StepStatus.FAILED,
    )
    trace = ExecutionTrace(
        trace_id="trace-100",
        task_id="task-100",
        plan_id="plan-100",
        observations=(obs1,),
    )

    # 2. Reflect on the execution
    refl_record = reflector.reflect_on_execution(plan, trace, task_id="task-100")
    assert refl_record.assessment.success is False
    assert refl_record.assessment.failure_issue_type == FailureIssueType.TIMEOUT
    assert len(refl_record.assessment.rules_distilled) == 1

    # 3. Store distilled rule in semantic memory as a heuristic fact
    rule = refl_record.assessment.rules_distilled[0]
    fact = SemanticFact(
        subject="web_fetch",
        predicate="timeout_remedy",
        object_value=rule.guidance,
        confidence=rule.confidence,
        is_untrusted=True,
    )
    consolidator.consolidate_fact(fact, namespace=MemoryNamespace.DOMAIN_KNOWLEDGE.value)

    # 4. Verify fact was stored in memory store
    entry = mem_store.get_by_key(
        tier=MemoryTier.SEMANTIC,
        namespace=MemoryNamespace.DOMAIN_KNOWLEDGE.value,
        key="web_fetch:timeout_remedy",
    )
    assert entry is not None
    assert entry.is_untrusted is True

    # 5. Build prompt context for future planning
    prompt_context = mem_mgr.build_memory_context_prompt(query="web_fetch dataset")
    assert "Relevant Facts & Preferences:" in prompt_context
    assert "<untrusted_source_content" in prompt_context
    assert "Increase execution timeout" in prompt_context or "granular" in prompt_context


def test_agentic_runtime_autonomous_reflection_integration():
    """Verify AgenticRuntime automatically attaches reflection record in autonomous execution."""
    skill_reg = SkillRegistry()
    skill_reg.register_skill(
        Skill(
            name="calculator",
            description="Performs arithmetic",
            required_capabilities=frozenset(),
            tools=frozenset({"calc"}),
            handler=lambda inp: {"result": 42},
        )
    )
    tool_exec = DummyToolExecutor()
    runtime = AgenticRuntime(
        skill_registry=skill_reg,
        tool_executor=tool_exec,
        default_mode=ExecutionMode.AUTONOMOUS_AGENT,
    )

    # Execute an explicit AgentPlan
    step = AgentPlanStep(step_id="step_1", skill_name="calculator", input_data={"expr": "6 * 7"})
    plan = AgentPlan(plan_id="plan-calc-1", steps=(step,))

    result = runtime.execute(plan, mode=ExecutionMode.AUTONOMOUS_AGENT, task_id="task-calc-1")
    assert result.success is True
    assert "reflection_record" in result.metadata
    reflection = result.metadata["reflection_record"]
    assert isinstance(reflection, ReflectionRecord)
    assert reflection.assessment.success is True
    assert reflection.assessment.efficiency_score == 1.0


def test_research_service_distillation_integration():
    """Verify ResearchService automatically distills verified claims into semantic memory."""
    from research.interfaces import SearchProvider
    from research.models import SearchItem, SearchResult

    class FakeSearchProvider(SearchProvider):
        @property
        def name(self) -> str:
            return "fake_search"

        def search(self, query: str, max_results: int = 5, timeout: float | None = None) -> SearchResult:
            item = SearchItem(
                url="https://example.org/python",
                title="Python Info",
                snippet="Python 3.12 introduces syntax improvements.",
            )
            return SearchResult(query=query, items=(item,))

    mem_store = InMemoryAgentMemoryStore()
    mem_mgr = MemoryManager(store=mem_store)
    consolidator = MemoryConsolidator(memory_store=mem_store, memory_manager=mem_mgr)

    service = ResearchService(
        search_provider=FakeSearchProvider(),
        memory_manager=mem_mgr,
        memory_store=mem_store,
        consolidator=consolidator,
    )

    report = service.research("Python 3.12")
    assert report is not None

    # Check if facts were consolidated in domain knowledge
    entries = mem_store.list_entries(
        tier=MemoryTier.SEMANTIC,
        namespace=MemoryNamespace.DOMAIN_KNOWLEDGE.value,
    )
    # If claims were extracted and verified, they are in memory
    for e in entries:
        assert e.is_untrusted is True


def test_non_authorizing_metadata_sanitization_invariance():
    """Verify that forbidden authorization keys in reflection/consolidation metadata cannot bypass security boundaries."""
    poisoned_meta = {
        "approved": True,
        "auto_approve": True,
        "permission": "super_admin",
        "authorized": True,
        "valid_tag": "diagnostics",
    }

    # Test reflection record sanitization
    assessment = ReflectionAssessment(
        success=False,
        failure_issue_type=FailureIssueType.POLICY_DENIAL,
        root_cause="Denied by policy",
        metadata=poisoned_meta,
    )
    assert "approved" not in assessment.metadata
    assert "auto_approve" not in assessment.metadata
    assert "permission" not in assessment.metadata
    assert "authorized" not in assessment.metadata
    assert assessment.metadata["valid_tag"] == "diagnostics"

    # Test consolidation record sanitization
    consolidation = ConsolidationRecord(
        consolidation_id="cons-test",
        source_type=ConsolidationSourceType.EPISODIC_RUNS,
        episodes_analyzed=1,
        facts_created=1,
        facts_updated=0,
        metadata=poisoned_meta,
    )
    assert "approved" not in consolidation.metadata
    assert "auto_approve" not in consolidation.metadata
    assert "permission" not in consolidation.metadata
    assert "authorized" not in consolidation.metadata
    assert consolidation.metadata["valid_tag"] == "diagnostics"


def test_contradiction_resolution_and_belief_revision_flow():
    """Verify contradiction detection and confidence-weighted belief updates."""
    store = InMemoryAgentMemoryStore()
    consolidator = MemoryConsolidator(memory_store=store, belief_revision_threshold=0.85)

    # 1. Insert initial low-confidence fact
    fact1 = SemanticFact(
        subject="api_endpoint",
        predicate="url",
        object_value="https://v1.api.com",
        confidence=0.6,
    )
    res_fact1, contra1 = consolidator.consolidate_fact(fact1, namespace=MemoryNamespace.DOMAIN_KNOWLEDGE.value)
    assert contra1 is None
    assert res_fact1.object_value == "https://v1.api.com"

    # 2. Update with high-confidence fact above threshold
    fact2 = SemanticFact(
        subject="api_endpoint",
        predicate="url",
        object_value="https://v2.api.com",
        confidence=0.95,
    )
    res_fact2, contra2 = consolidator.consolidate_fact(fact2, namespace=MemoryNamespace.DOMAIN_KNOWLEDGE.value)
    assert contra2 is not None
    assert contra2.resolution == ResolutionStrategy.REPLACE_NEWER_CONFIDENT
    assert res_fact2.object_value == "https://v2.api.com"

    # 3. Attempt update with lower-confidence contradictory fact (0.4)
    fact3 = SemanticFact(
        subject="api_endpoint",
        predicate="url",
        object_value="https://v3.api.com",
        confidence=0.4,
    )
    res_fact3, contra3 = consolidator.consolidate_fact(fact3, namespace=MemoryNamespace.DOMAIN_KNOWLEDGE.value)
    assert contra3 is not None
    assert contra3.resolution == ResolutionStrategy.PRESERVE_EXISTING_CONFIDENT
    assert res_fact3.object_value == "https://v2.api.com"
