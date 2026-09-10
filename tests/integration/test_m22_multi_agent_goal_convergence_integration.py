"""
Integration tests for Milestone 22:
Autonomous Multi-Agent Goal Convergence, Team-Aware Scheduling & Resilient Distributed Coordination.
"""

import pytest
import tempfile
import shutil
from typing import Any

from app.aura import AURA
from core.agentic_runtime import AgenticRuntime
from core.agent_role import AgentRole
from core.role_registry import RoleRegistry
from core.agent_message_bus import AgentMessageBus
from core.team_types import TeamDefinition, TeamTopology, TeamMember
from core.goal import Goal, GoalStatus
from core.consensus_engine import ConsensusEngine
from core.streaming_gateway import StreamingGateway
from core.runtime_checkpoint import CheckpointMetadata
from core.provenance import TaintedValue
from interfaces.model import ModelInterface


class MockTeamConvergenceModel(ModelInterface):
    """Mock model capable of handling hierarchical, pipeline, consensus, and delegation requests."""

    def generate(self, prompt: str, **kwargs: Any) -> str:
        if "Cast a clear vote" in prompt or "Evaluate the following task" in prompt or "CONSENSUS" in prompt:
            return "ACCEPT\nScore: 0.98\nRationale: Multi-agent consensus reached on high-reliability design."
        elif "Software Architect" in prompt or "lead" in prompt.lower() or "Architect" in prompt:
            return "Architecture specification complete: Verified microservices topology and data contracts."
        elif "Senior Software Engineer" in prompt or "coder" in prompt.lower() or "Engineer" in prompt:
            return "Implementation complete: Built resilient multi-agent coordination pipelines."
        elif "Code Reviewer" in prompt or "reviewer" in prompt.lower() or "Reviewer" in prompt:
            return "Review passed: 100% test coverage and zero architectural drift detected."
        elif "Security Auditor" in prompt or "auditor" in prompt.lower() or "Security" in prompt:
            return "Security assessment: Verified zero privilege leaks and taint boundaries enforced."
        return f"Completed autonomous subtask for prompt: {prompt[:60]}..."

    def generate_stream(self, prompt: str, **kwargs: Any):
        yield self.generate(prompt, **kwargs)


@pytest.fixture
def temp_checkpoint_dir():
    d = tempfile.mkdtemp(prefix="aura_test_m22_integ_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_m22_hierarchical_team_goal_convergence():
    model = MockTeamConvergenceModel()
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

    team = TeamDefinition(
        team_id="core_eng_team",
        name="Core Engineering Team",
        topology=TeamTopology.HIERARCHICAL,
        members=[
            TeamMember(role_id="architect", is_lead=True),
            TeamMember(role_id="coder"),
            TeamMember(role_id="reviewer"),
        ],
    )

    goal_task = aura.submit_team_goal(
        title="Develop Distributed Coordination Protocol",
        description="Design and implement resilient multi-agent coordination protocol",
        team=team,
        session_id="session_team_goal_1",
    )

    assert goal_task is not None
    assert goal_task.goal_id is not None
    assert goal_task.assigned_team_id == "core_eng_team"
    assert goal_task.execution_topology == TeamTopology.HIERARCHICAL

    # Execute goal via runtime GoalEngine
    exec_result = runtime.execute_goal(goal_task.goal_id)
    assert exec_result.success is True
    assert exec_result.status == GoalStatus.COMPLETED

    # Verify message bus interactions took place
    history = bus.get_history(session_id="session_team_goal_1")
    assert len(history) >= 2


def test_m22_sequential_pipeline_team_goal_convergence():
    model = MockTeamConvergenceModel()
    roles = RoleRegistry()
    bus = AgentMessageBus()

    runtime = AgenticRuntime(
        model=model,
        role_registry=roles,
        message_bus=bus,
    )
    aura = AURA(orchestrator=None, agentic_runtime=runtime)

    team = TeamDefinition(
        team_id="pipeline_audit_team",
        name="Sequential Audit Team",
        topology=TeamTopology.SEQUENTIAL_PIPELINE,
        members=[
            TeamMember(role_id="architect"),
            TeamMember(role_id="coder"),
            TeamMember(role_id="security_auditor"),
        ],
    )

    goal_task = aura.submit_team_goal(
        title="Secure Data Ingestion Pipeline",
        description="Architect, code, and audit secure data ingestion pipeline",
        team=team,
        session_id="session_pipeline_goal_1",
    )

    exec_result = runtime.execute_goal(goal_task.goal_id)
    assert exec_result.success is True
    assert exec_result.status == GoalStatus.COMPLETED


def test_m22_consensus_voting_team_goal_convergence():
    model = MockTeamConvergenceModel()
    roles = RoleRegistry()
    consensus = ConsensusEngine()
    bus = AgentMessageBus()

    runtime = AgenticRuntime(
        model=model,
        role_registry=roles,
        message_bus=bus,
        consensus_engine=consensus,
    )
    aura = AURA(orchestrator=None, agentic_runtime=runtime)

    team = TeamDefinition(
        team_id="governance_council",
        name="Architecture Governance Council",
        topology=TeamTopology.CONSENSUS_VOTING,
        members=[
            TeamMember(role_id="architect"),
            TeamMember(role_id="reviewer"),
            TeamMember(role_id="security_auditor"),
        ],
    )

    goal_task = aura.submit_team_goal(
        title="Approve Mission Critical Deployment Standard",
        description="Consensus vote across architect, reviewer, and security auditor",
        team=team,
        session_id="session_consensus_goal_1",
    )

    exec_result = runtime.execute_goal(goal_task.goal_id)
    assert exec_result.success is True
    assert exec_result.status == GoalStatus.COMPLETED


def test_m22_team_goal_checkpoint_and_resilient_recovery(temp_checkpoint_dir):
    model = MockTeamConvergenceModel()
    roles = RoleRegistry()
    roles.register(AgentRole(role_id="data_engineer", name="Data Engineer", description="Data Engineer", system_prompt="You are a data engineer."))
    bus = AgentMessageBus()

    runtime = AgenticRuntime(
        model=model,
        role_registry=roles,
        message_bus=bus,
    )
    runtime.checkpoint_manager.checkpoint_dir = temp_checkpoint_dir
    aura = AURA(orchestrator=None, agentic_runtime=runtime)

    team = TeamDefinition(
        team_id="etl_team",
        name="ETL Processing Team",
        topology=TeamTopology.HIERARCHICAL,
        members=[
            TeamMember(role_id="architect", is_lead=True),
            TeamMember(role_id="data_engineer"),
        ],
    )

    goal_task = aura.submit_team_goal(
        title="ETL Migration Goal",
        description="Migrate petabyte scale telemetry data",
        team=team,
        session_id="session_etl_1",
    )

    # Create checkpoint snapshot
    ckpt_path = runtime.checkpoint_manager.save_checkpoint("ckpt_m22_interm")
    assert ckpt_path is not None

    # Instantiate new runtime with same checkpoint directory
    fresh_roles = RoleRegistry()
    fresh_bus = AgentMessageBus()
    fresh_runtime = AgenticRuntime(
        model=model,
        role_registry=fresh_roles,
        message_bus=fresh_bus,
    )
    fresh_runtime.checkpoint_manager.checkpoint_dir = temp_checkpoint_dir

    # Recover from checkpoint
    rec_result = fresh_runtime.checkpoint_manager.restore_latest()
    assert isinstance(rec_result, CheckpointMetadata)
    assert rec_result.checkpoint_id == "ckpt_m22_interm"

    # Custom role was recovered
    assert fresh_roles.get_role("data_engineer") is not None


def test_m22_team_goal_security_and_taint_isolation():
    model = MockTeamConvergenceModel()
    roles = RoleRegistry()
    bus = AgentMessageBus()

    runtime = AgenticRuntime(
        model=model,
        role_registry=roles,
        message_bus=bus,
    )
    aura = AURA(orchestrator=None, agentic_runtime=runtime)

    tainted_description = TaintedValue(raw_value="Process untrusted CSV dataset from external webhook", source_type="external_http")

    team = TeamDefinition(
        team_id="sec_taint_team",
        name="Security Taint Verification Team",
        topology=TeamTopology.HIERARCHICAL,
        members=[
            TeamMember(role_id="architect", is_lead=True),
            TeamMember(role_id="coder"),
        ],
        metadata={
            "is_admin": True,  # Attempt privilege injection in metadata
            "bypass_policy": True,
            "data_source": tainted_description,
        },
    )

    goal_task = aura.submit_team_goal(
        title="Sanitize External Dataset",
        description="Decontaminate incoming untrusted data",
        team=team,
        session_id="session_taint_goal_1",
    )

    # Goal metadata must not contain escalated privilege keys
    assert "is_admin" not in goal_task.metadata
    assert "bypass_policy" not in goal_task.metadata

    # Tainted value must remain encapsulated
    assert isinstance(team.metadata["data_source"], TaintedValue)
    assert team.metadata["data_source"].source_type == "external_http"
