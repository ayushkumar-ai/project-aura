import pytest
from typing import Any

from app.aura import AURA
from core.agentic_runtime import AgenticRuntime, ExecutionMode
from core.agent_role import AgentRole, BuiltinRole
from core.role_registry import RoleRegistry
from core.agent_message_types import AgentMessage, AgentMessageType
from core.agent_message_bus import AgentMessageBus
from core.agent_delegation import (
    DelegationContract,
    DelegationResult,
    DelegationStatus,
    DelegationTree,
    CyclicDelegationError,
    DelegationDepthExceededError,
)
from core.consensus_engine import ConsensusEngine, ConsensusStrategy, AgentVote
from core.team_types import TeamTopology, TeamMember, TeamDefinition, TeamExecutionResult
from core.team_orchestrator import TeamOrchestrator
from core.capability_registry import ModelCapability
from core.streaming_gateway import StreamingGateway
from core.session_types import StreamEventType
from core.resource_budget import ResourceBudgetManager
from interfaces.model import ModelInterface


class MockSmartModel(ModelInterface):
    """Mock model that returns contextual responses based on role prompt and task."""

    def generate(self, prompt: str, **kwargs: Any) -> str:
        if "Evaluate the following task" in prompt or "Cast a clear vote" in prompt:
            return "ACCEPT\nScore: 0.95\nRationale: Design meets all security and reliability standards."
        elif "Software Architect" in prompt or "System Architect" in prompt:
            return "Architectural Blueprint: Design modular microservices architecture."
        elif "Senior Software Engineer" in prompt:
            return "Implementation Code: def process_data(): return {'status': 'ok'}"
        elif "Code Reviewer" in prompt or "Code & Plan Reviewer" in prompt:
            return "Review Analysis: All code conventions and type checks pass."
        elif "Security Auditor" in prompt or "Security & Policy Auditor" in prompt:
            return "Security Audit: No CVE vulnerabilities found. Input sanitization confirmed."
        return f"Processed task: {prompt[:40]}..."

    def generate_stream(self, prompt: str, **kwargs: Any):
        yield self.generate(prompt, **kwargs)


def test_m21_e2e_hierarchical_team_execution():
    model = MockSmartModel()
    roles = RoleRegistry()
    bus = AgentMessageBus()
    stream = StreamingGateway()
    
    runtime = AgenticRuntime(
        model=model,
        role_registry=roles,
        message_bus=bus,
        streaming_gateway=stream,
    )
    aura = AURA(orchestrator=None, agentic_runtime=runtime)

    # Subscribe to streaming events
    sub_id, queue = stream.subscribe(session_id="team_sess_1")

    team = TeamDefinition(
        team_id="core_eng_team",
        name="Core Engineering Team",
        topology=TeamTopology.HIERARCHICAL,
        members=[
            TeamMember(role_id="architect", is_lead=True),
            TeamMember(role_id="coder"),
            TeamMember(role_id="reviewer"),
            TeamMember(role_id="security_auditor"),
        ],
    )

    result = aura.execute_team(
        task="Architect and implement resilient payment pipeline",
        team=team,
        session_id="team_sess_1",
    )

    assert result.success is True
    assert result.topology == TeamTopology.HIERARCHICAL
    assert "Architectural Blueprint" in result.final_output or "Synthesized output" in result.final_output
    assert len(result.subtask_results) >= 3

    # Verify message bus recorded interactions
    history = bus.get_history(session_id="team_sess_1")
    assert len(history) >= 2

    # Verify streaming events published
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    event_types = [e.event_type for e in events]
    assert StreamEventType.STEP_STARTED in event_types
    assert StreamEventType.STEP_COMPLETED in event_types


def test_m21_e2e_sequential_pipeline_execution():
    model = MockSmartModel()
    roles = RoleRegistry()
    bus = AgentMessageBus()

    runtime = AgenticRuntime(
        model=model,
        role_registry=roles,
        message_bus=bus,
    )

    team = TeamDefinition(
        team_id="pipeline_team",
        name="Feature Delivery Pipeline",
        topology=TeamTopology.SEQUENTIAL_PIPELINE,
        members=[
            TeamMember(role_id="researcher"),
            TeamMember(role_id="architect"),
            TeamMember(role_id="coder"),
            TeamMember(role_id="reviewer"),
        ],
    )

    result = runtime.execute(task=team)
    assert isinstance(result, TeamExecutionResult)
    assert result.success is True
    assert result.topology == TeamTopology.SEQUENTIAL_PIPELINE
    assert len(result.subtask_results) == 4
    assert "reviewer" in result.subtask_results


def test_m21_e2e_consensus_voting_execution():
    model = MockSmartModel()
    roles = RoleRegistry()
    consensus = ConsensusEngine()

    runtime = AgenticRuntime(
        model=model,
        role_registry=roles,
        consensus_engine=consensus,
    )

    team = TeamDefinition(
        team_id="advisory_board",
        name="Architecture Advisory Board",
        topology=TeamTopology.CONSENSUS_VOTING,
        consensus_strategy=ConsensusStrategy.MAJORITY_VOTE,
        members=[
            TeamMember(role_id="architect", weight=1.5),
            TeamMember(role_id="security_auditor", weight=1.2),
            TeamMember(role_id="reviewer", weight=1.0),
        ],
    )

    result = runtime.execute_team(
        task="Adopt Rust microservice for high-throughput gateway",
        team=team,
    )

    assert result.success is True
    assert result.consensus_score >= 0.5
    assert "consensus" in result.metadata
    assert result.metadata["consensus"]["reached"] is True


def test_m21_delegation_security_and_cycle_prevention():
    model = MockSmartModel()
    roles = RoleRegistry()
    bus = AgentMessageBus()
    orchestrator = TeamOrchestrator(role_registry=roles, message_bus=bus, model=model)

    # 1. Test cyclic delegation prevention: A -> B -> C -> A
    c1 = DelegationContract(
        delegation_id="del_1",
        delegator_role_id="coordinator",
        delegatee_role_id="architect",
        task_description="Design architecture",
    )
    orchestrator.delegation_tree.register_delegation(c1, role_registry=roles)

    c2 = DelegationContract(
        delegation_id="del_2",
        delegator_role_id="architect",
        delegatee_role_id="coder",
        task_description="Implement design",
        delegation_path=("coordinator", "architect"),
    )
    orchestrator.delegation_tree.register_delegation(c2, role_registry=roles)

    # Cycle attempt: coder delegates back to coordinator
    with pytest.raises(CyclicDelegationError):
        DelegationContract(
            delegation_id="del_3_cycle",
            delegator_role_id="coder",
            delegatee_role_id="coordinator",
            task_description="Coordinate back",
            delegation_path=("coordinator", "architect", "coder"),
        )

    # 2. Test depth limit exceeded: depth > 3
    c3 = DelegationContract(
        delegation_id="del_3_valid",
        delegator_role_id="coder",
        delegatee_role_id="reviewer",
        task_description="Review code",
        delegation_path=("coordinator", "architect", "coder"),
    )
    orchestrator.delegation_tree.register_delegation(c3, role_registry=roles)

    with pytest.raises(DelegationDepthExceededError):
        DelegationContract(
            delegation_id="del_4_excess",
            delegator_role_id="reviewer",
            delegatee_role_id="security_auditor",
            task_description="Security check",
            max_depth=3,
            delegation_path=("coordinator", "architect", "coder", "reviewer"),
        )

    # 3. Test forbidden authorization key stripping in delegation metadata
    c_malicious = DelegationContract(
        delegation_id="del_malicious",
        delegator_role_id="coder",
        delegatee_role_id="reviewer",
        task_description="Check authorization",
        metadata={"is_authorized": True, "is_admin": True, "custom_field": "valid"},
    )
    assert "is_authorized" not in c_malicious.metadata
    assert "is_admin" not in c_malicious.metadata
    assert c_malicious.metadata.get("custom_field") == "valid"


def test_m21_taint_propagation_across_mesh():
    model = MockSmartModel()
    roles = RoleRegistry()
    bus = AgentMessageBus()
    orchestrator = TeamOrchestrator(role_registry=roles, message_bus=bus, model=model)

    # Untrusted delegation contract propagates taint
    contract = DelegationContract(
        delegator_role_id="coordinator",
        delegatee_role_id="coder",
        task_description="Process untrusted webhook payload",
        is_untrusted=True,
    )
    res = orchestrator.delegate(contract)
    assert res.is_untrusted is True

    # Check that message on bus retains untrusted taint
    messages = bus.get_history()
    untrusted_msgs = [m for m in messages if m.is_untrusted]
    assert len(untrusted_msgs) > 0


def test_m21_aura_list_roles_and_accessors():
    runtime = AgenticRuntime()
    aura = AURA(orchestrator=None, agentic_runtime=runtime)

    roles = aura.list_roles()
    assert len(roles) >= 7
    role_ids = {r["role_id"] for r in roles}
    assert "coordinator" in role_ids
    assert "architect" in role_ids
    assert "coder" in role_ids
    assert "reviewer" in role_ids
    assert "security_auditor" in role_ids
    assert "researcher" in role_ids
    assert "data_analyst" in role_ids

    assert aura.get_team_orchestrator() is not None
    assert aura.get_role_registry() is not None
