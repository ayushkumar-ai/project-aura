"""M59 — Unified Autonomous Agent Types and State Machine Unit Tests."""

import time
import pytest
from core.agent_mesh.types import (
    ActionType,
    AgentDelegation,
    AgentMeshAudit,
    AgentMeshEvent,
    AgentPhase,
    AgentRole,
    AgentRun,
    AgentRunBudget,
    AgentRunStatus,
    AgentRunStep,
    FailureCategory,
    VerificationStatus,
)


class TestAgentTypesAndStateUnit:
    def test_agent_run_defaults_and_serialization(self):
        # Invariants M59-F01, M59-F02, M59-F49
        run = AgentRun(
            tenant_id="tenant_aura",
            user_id="user_admin",
            intent="Execute batch job with key Authorization: Bearer sk-secret12345678901234567890",
        )
        assert run.run_id.startswith("run_")
        assert run.status == AgentRunStatus.PENDING
        assert run.current_phase == AgentPhase.RECEIVE
        assert run.is_terminal() is False
        # Verify secret scrubbing in intent
        assert "sk-secret12345678901234567890" not in run.intent

        serialized = run.to_dict()
        assert serialized["run_id"] == run.run_id
        assert serialized["tenant_id"] == "tenant_aura"
        assert serialized["status"] == "pending"
        assert serialized["current_phase"] == "receive"

        reconstructed = AgentRun.from_dict(serialized)
        assert reconstructed.run_id == run.run_id
        assert reconstructed.status == AgentRunStatus.PENDING
        assert reconstructed.current_phase == AgentPhase.RECEIVE

    def test_agent_run_valid_state_transitions(self):
        # Invariant M59-F03
        run = AgentRun(tenant_id="tenant_1", user_id="user_1", intent="Analyze data")

        # PENDING -> RUNNING
        run.transition_to(AgentRunStatus.RUNNING)
        assert run.status == AgentRunStatus.RUNNING
        assert not run.is_terminal()

        # RUNNING -> WAITING_APPROVAL
        run.transition_to(AgentRunStatus.WAITING_APPROVAL)
        assert run.status == AgentRunStatus.WAITING_APPROVAL

        # WAITING_APPROVAL -> RUNNING
        run.transition_to(AgentRunStatus.RUNNING)
        assert run.status == AgentRunStatus.RUNNING

        # RUNNING -> PAUSED
        run.transition_to(AgentRunStatus.PAUSED)
        assert run.status == AgentRunStatus.PAUSED

        # PAUSED -> RUNNING
        run.transition_to(AgentRunStatus.RUNNING)
        assert run.status == AgentRunStatus.RUNNING

        # RUNNING -> COMPLETED (Terminal)
        run.transition_to(AgentRunStatus.COMPLETED)
        assert run.status == AgentRunStatus.COMPLETED
        assert run.is_terminal() is True

    def test_agent_run_invalid_state_transitions(self):
        # Invariant M59-F03: Transitions from terminal state or illegal transitions must raise ValueError
        run = AgentRun(tenant_id="tenant_1", user_id="user_1", intent="Test task")
        run.transition_to(AgentRunStatus.RUNNING)
        run.transition_to(AgentRunStatus.COMPLETED)

        with pytest.raises(ValueError, match="Illegal AgentRun state transition"):
            run.transition_to(AgentRunStatus.RUNNING)

        failed_run = AgentRun(tenant_id="tenant_1", user_id="user_1", intent="Failing task")
        failed_run.transition_to(AgentRunStatus.RUNNING)
        failed_run.transition_to(AgentRunStatus.FAILED, error_detail="Simulated failure")
        assert failed_run.is_terminal() is True

        with pytest.raises(ValueError, match="Illegal AgentRun state transition"):
            failed_run.transition_to(AgentRunStatus.PAUSED)

    def test_agent_run_step_serialization_and_scrubbing(self):
        # Invariants M59-F11, M59-F49
        step = AgentRunStep(
            run_id="run_100",
            tenant_id="tenant_1",
            step_number=1,
            phase=AgentPhase.EXECUTE,
            plan_action="platform_action",
            action_type=ActionType.DEVICE,
            parameters={"device_id": "dev_1", "api_key": "Bearer sk-9876543210zyxwvutsrq12345"},
            result={"token": "ghp_1234567890abcdefghijklmnopqrstuv", "status": "ok"},
            status="completed",
            verification_status=VerificationStatus.VERIFIED_SUCCESS,
        )
        assert "sk-9876543210" not in str(step.parameters)
        assert step.step_id.startswith("step_")

        serialized = step.to_dict()
        assert serialized["step_id"] == step.step_id
        assert serialized["run_id"] == "run_100"
        assert serialized["verification_status"] == "verified_success"

        reconstructed = AgentRunStep.from_dict(serialized)
        assert reconstructed.step_id == step.step_id
        assert reconstructed.action_type == ActionType.DEVICE

    def test_agent_delegation_record(self):
        # Invariant M59-F35
        del_rec = AgentDelegation(
            parent_run_id="run_parent_1",
            child_run_id="run_child_1",
            tenant_id="tenant_1",
            role=AgentRole.RESEARCH,
            capabilities=["web_search", "summarize"],
            budget_allocated=AgentRunBudget(max_child_depth=2),
            status="active",
        )
        assert del_rec.delegation_id.startswith("del_")
        assert del_rec.role == AgentRole.RESEARCH

        serialized = del_rec.to_dict()
        assert serialized["role"] == "research"
        assert serialized["parent_run_id"] == "run_parent_1"

        reconstructed = AgentDelegation.from_dict(serialized)
        assert reconstructed.delegation_id == del_rec.delegation_id
        assert reconstructed.role == AgentRole.RESEARCH

    def test_agent_mesh_event_and_audit(self):
        # Invariants M59-F47, M59-F48, M59-F49
        event = AgentMeshEvent(
            run_id="run_100",
            tenant_id="tenant_1",
            event_type="intent_classified",
            phase=AgentPhase.INTENT,
            data={"secret": "Bearer sk-1234567890abcdef1234567890", "confidence": 0.95},
        )
        assert "sk-1234567890" not in str(event.data)
        assert event.event_id.startswith("evt_")

        audit = AgentMeshAudit(
            run_id="run_100",
            tenant_id="tenant_1",
            action="evaluate_plan",
            principal_id="user_admin",
            details={"secret_token": "Bearer sk-xyz123abc456789012345678"},
        )
        assert "xyz123abc456" not in str(audit.details)
        assert audit.audit_id.startswith("aud_")

    def test_agent_run_budget_defaults_and_limits(self):
        # Invariants M59-F41 through M59-F46
        budget = AgentRunBudget()
        assert budget.max_iterations == 25
        assert budget.max_child_depth == 3
        assert budget.timeout_seconds == 300.0
        assert budget.max_retries == 3
