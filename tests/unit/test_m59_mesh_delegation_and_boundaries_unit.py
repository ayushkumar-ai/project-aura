"""M59 — Enterprise Intelligence Mesh Delegation & Boundaries Unit Tests."""

import pytest
from core.agent_mesh.mesh import IntelligenceMeshCoordinator
from core.agent_mesh.types import (
    AgentRole,
    AgentRun,
    AgentRunBudget,
    AgentRunStatus,
)
from core.repositories.in_memory_agent_mesh import InMemoryAgentMeshRepository


class TestMeshDelegationAndBoundariesUnit:
    def test_list_roles(self):
        # Invariant M59-F27
        repo = InMemoryAgentMeshRepository()
        coordinator = IntelligenceMeshCoordinator(repository=repo)
        roles = coordinator.list_roles()
        assert len(roles) > 0
        role_names = [r["role"] for r in roles]
        assert "research" in role_names
        assert "device" in role_names
        assert "generalist" in role_names

    def test_successful_task_delegation(self):
        # Invariants M59-F28, M59-F31, M59-F35
        repo = InMemoryAgentMeshRepository()
        parent_run = AgentRun(
            run_id="run_root",
            tenant_id="tenant_1",
            user_id="user_admin",
            depth=0,
            status=AgentRunStatus.RUNNING,
            intent="Main autonomous goal",
        )
        repo.save_run(parent_run)

        coordinator = IntelligenceMeshCoordinator(repository=repo, runtime=None)
        child = coordinator.delegate_task(
            parent_run_id="run_root",
            tenant_id="tenant_1",
            role=AgentRole.RESEARCH,
            subtask_intent="Research competitive models",
        )

        assert child.parent_run_id == "run_root"
        assert child.tenant_id == "tenant_1"
        assert child.user_id == "user_admin"
        assert child.depth == 1
        assert child.status == AgentRunStatus.PENDING

        delegations = repo.list_delegations("run_root", "tenant_1")
        assert len(delegations) == 1
        assert delegations[0].child_run_id == child.run_id
        assert delegations[0].role == AgentRole.RESEARCH

    def test_max_delegation_depth_limit(self):
        # Invariant M59-F32: Hard depth limit of 3
        repo = InMemoryAgentMeshRepository()
        run_d3 = AgentRun(
            run_id="run_depth_3",
            tenant_id="tenant_1",
            depth=3,
            status=AgentRunStatus.RUNNING,
            intent="Deep subtask",
        )
        repo.save_run(run_d3)

        coordinator = IntelligenceMeshCoordinator(repository=repo, max_depth=3)
        with pytest.raises(PermissionError, match="Delegation depth limit"):
            coordinator.delegate_task(
                parent_run_id="run_depth_3",
                tenant_id="tenant_1",
                role=AgentRole.RESEARCH,
                subtask_intent="Exceeding depth",
            )

    def test_max_fan_out_limit(self):
        # Invariant M59-F33: Hard fan-out limit of 5
        repo = InMemoryAgentMeshRepository()
        parent = AgentRun(
            run_id="run_parent_fan",
            tenant_id="tenant_1",
            depth=0,
            status=AgentRunStatus.RUNNING,
            intent="Parent fan-out task",
        )
        repo.save_run(parent)

        coordinator = IntelligenceMeshCoordinator(repository=repo, max_fan_out=3)
        for i in range(3):
            coordinator.delegate_task(
                parent_run_id="run_parent_fan",
                tenant_id="tenant_1",
                role=AgentRole.RESEARCH,
                subtask_intent=f"Subtask {i}",
            )

        # 4th delegation should exceed fan-out limit of 3
        with pytest.raises(PermissionError, match="Fan-out limit"):
            coordinator.delegate_task(
                parent_run_id="run_parent_fan",
                tenant_id="tenant_1",
                role=AgentRole.RESEARCH,
                subtask_intent="Subtask 4th (over limit)",
            )

    def test_delegation_from_terminal_parent_rejected(self):
        # Invariant M59-F04
        repo = InMemoryAgentMeshRepository()
        terminal_run = AgentRun(
            run_id="run_completed",
            tenant_id="tenant_1",
            status=AgentRunStatus.COMPLETED,
            intent="Completed run",
        )
        repo.save_run(terminal_run)

        coordinator = IntelligenceMeshCoordinator(repository=repo)
        with pytest.raises(RuntimeError, match="Cannot delegate from terminal parent run"):
            coordinator.delegate_task(
                parent_run_id="run_completed",
                tenant_id="tenant_1",
                role=AgentRole.RESEARCH,
                subtask_intent="Illegal subtask",
            )

    def test_cross_tenant_delegation_rejected(self):
        # Invariant M59-F31
        repo = InMemoryAgentMeshRepository()
        parent_tenant_a = AgentRun(
            run_id="run_tenant_A",
            tenant_id="tenant_A",
            status=AgentRunStatus.RUNNING,
            intent="Tenant A task",
        )
        repo.save_run(parent_tenant_a)

        coordinator = IntelligenceMeshCoordinator(repository=repo)
        with pytest.raises(ValueError, match="not found for tenant"):
            coordinator.delegate_task(
                parent_run_id="run_tenant_A",
                tenant_id="tenant_B",
                role=AgentRole.RESEARCH,
                subtask_intent="Attempt cross-tenant hijack",
            )

    def test_budget_inheritance_bounds(self):
        # Invariant M59-F29
        repo = InMemoryAgentMeshRepository()
        parent_budget = AgentRunBudget(
            max_iterations=10,
            max_tool_calls=15,
            max_model_calls=5,
            timeout_seconds=60.0,
        )
        parent = AgentRun(
            run_id="run_budgeted",
            tenant_id="tenant_1",
            budget=parent_budget,
            status=AgentRunStatus.RUNNING,
        )
        repo.save_run(parent)

        coordinator = IntelligenceMeshCoordinator(repository=repo)
        requested_excessive_budget = AgentRunBudget(
            max_iterations=100,
            max_tool_calls=200,
            timeout_seconds=500.0,
        )
        child = coordinator.delegate_task(
            parent_run_id="run_budgeted",
            tenant_id="tenant_1",
            role=AgentRole.RESEARCH,
            subtask_intent="Bounded child",
            budget=requested_excessive_budget,
        )

        assert child.budget.max_iterations == 10
        assert child.budget.max_tool_calls == 15
        assert child.budget.timeout_seconds == 60.0
