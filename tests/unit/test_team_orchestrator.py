import pytest
from uuid import UUID, uuid4

from core.agent_delegation import DelegationContract, DelegationStatus
from core.agent_role import AgentRole
from core.consensus_engine import ConsensusStrategy
from core.models import AURAResponse
from core.resource_budget import ResourceBudgetManager
from core.role_registry import RoleRegistry
from core.team_orchestrator import TeamOrchestrator
from core.team_types import (
    TeamDefinition,
    TeamMember,
    TeamTopology,
)
from interfaces.model import ModelInterface


class MockEchoModel(ModelInterface):
    """Mock model provider that prefixes responses with role context."""

    def __init__(self, fixed_response: str | None = None):
        self.call_count = 0
        self.fixed_response = fixed_response

    @property
    def name(self) -> str:
        return "mock_echo"

    def generate(self, prompt: str, request_id: UUID | None = None) -> AURAResponse:
        self.call_count += 1
        if self.fixed_response:
            return AURAResponse(
                request_id=request_id or uuid4(),
                content=self.fixed_response,
                metadata={"call_count": str(self.call_count)},
            )

        role_label = "Agent"
        if "=== ROLE:" in prompt:
            role_part = prompt.split("=== ROLE:")[1].split("===")[0].strip()
            role_label = role_part.split("(")[0].strip()

        return AURAResponse(
            request_id=request_id or uuid4(),
            content=f"Output from {role_label} for task",
            metadata={"call_count": str(self.call_count)},
        )


def test_team_orchestrator_hierarchical_topology():
    mock_model = MockEchoModel()
    roles = RoleRegistry()
    orchestrator = TeamOrchestrator(role_registry=roles, model=mock_model)

    team = TeamDefinition(
        team_id="dev_team",
        name="Dev Team",
        members=[
            TeamMember(role_id="coordinator", is_lead=True),
            TeamMember(role_id="architect"),
            TeamMember(role_id="coder"),
        ],
        topology=TeamTopology.HIERARCHICAL,
    )

    res = orchestrator.execute_team(task="Build authentication system", team=team)

    assert res.success is True
    assert res.team_id == "dev_team"
    assert res.topology == TeamTopology.HIERARCHICAL
    assert "Synthesis coordinated by Lead [coordinator]" in res.final_output
    assert "architect" in res.subtask_results
    assert "coder" in res.subtask_results
    assert res.messages_exchanged > 0


def test_team_orchestrator_sequential_pipeline_topology():
    mock_model = MockEchoModel()
    roles = RoleRegistry()
    orchestrator = TeamOrchestrator(role_registry=roles, model=mock_model)

    team = TeamDefinition(
        team_id="pipe_team",
        name="Pipeline Team",
        members=[
            TeamMember(role_id="architect"),
            TeamMember(role_id="coder"),
            TeamMember(role_id="reviewer"),
        ],
        topology=TeamTopology.SEQUENTIAL_PIPELINE,
    )

    res = orchestrator.execute_team(task="Implement cache layer", team=team)

    assert res.success is True
    assert res.topology == TeamTopology.SEQUENTIAL_PIPELINE
    assert res.iterations == 3
    assert "architect" in res.subtask_results
    assert "coder" in res.subtask_results
    assert "reviewer" in res.subtask_results


def test_team_orchestrator_round_robin_debate():
    mock_model = MockEchoModel()
    roles = RoleRegistry()
    orchestrator = TeamOrchestrator(role_registry=roles, model=mock_model)

    team = TeamDefinition(
        team_id="debate_team",
        name="Debate Team",
        members=[
            TeamMember(role_id="architect"),
            TeamMember(role_id="reviewer"),
        ],
        topology=TeamTopology.ROUND_ROBIN_DEBATE,
        max_iterations=2,
    )

    res = orchestrator.execute_team(task="Evaluate SQL vs NoSQL", team=team)

    assert res.success is True
    assert res.topology == TeamTopology.ROUND_ROBIN_DEBATE
    assert res.iterations == 2
    assert "architect_iter_1" in res.subtask_results
    assert "reviewer_iter_2" in res.subtask_results


def test_team_orchestrator_consensus_voting():
    # Model returns unanimous "approve" decision
    mock_model = MockEchoModel(fixed_response="approve\nRationale: Meets all criteria")
    roles = RoleRegistry()
    orchestrator = TeamOrchestrator(role_registry=roles, model=mock_model)

    team = TeamDefinition(
        team_id="vote_team",
        name="Voting Team",
        members=[
            TeamMember(role_id="coder", weight=1.0),
            TeamMember(role_id="reviewer", weight=1.0),
            TeamMember(role_id="security_auditor", weight=1.0),
        ],
        topology=TeamTopology.CONSENSUS_VOTING,
        consensus_strategy=ConsensusStrategy.MAJORITY_VOTE,
    )

    res = orchestrator.execute_team(task="Approve release 1.0", team=team)

    assert res.success is True
    assert res.topology == TeamTopology.CONSENSUS_VOTING
    assert "Consensus Result:" in res.final_output
    assert "approve" in res.final_output


def test_team_orchestrator_direct_delegation():
    mock_model = MockEchoModel()
    roles = RoleRegistry()
    orchestrator = TeamOrchestrator(role_registry=roles, model=mock_model)

    contract = DelegationContract(
        delegator_role_id="architect",
        delegatee_role_id="coder",
        task_description="Implement interface A",
    )

    del_res = orchestrator.delegate(contract)
    assert del_res.status == DelegationStatus.COMPLETED
    assert "Software Engineer" in del_res.output or "Output from" in del_res.output


def test_team_orchestrator_budget_exhaustion():
    mock_model = MockEchoModel()
    roles = RoleRegistry()
    # Tight rate limit
    budget_mgr = ResourceBudgetManager(global_max_tokens_per_minute=100)
    # Saturate token rate limit
    budget_mgr.release_quota(goal_id="tight_budget_team", actual_tokens=150, actual_tool_calls=1)

    orchestrator = TeamOrchestrator(role_registry=roles, model=mock_model, budget_manager=budget_mgr)

    team = TeamDefinition(
        team_id="tight_budget_team",
        name="Tight Budget Team",
        members=[TeamMember(role_id="coder")],
    )

    res = orchestrator.execute_team(task="Expensive task", team=team)
    assert res.success is False
    assert "rate limit exceeded" in res.error.lower() or "limit" in res.error.lower()
