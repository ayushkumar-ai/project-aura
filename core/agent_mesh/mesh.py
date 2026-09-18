"""M59 — Enterprise Intelligence Mesh & Bounded Delegation Coordinator.

Orchestrates multi-agent role specialization, task delegation, cycle detection,
and strict hierarchical permission subsetting.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from core.agent_mesh.types import (
    AgentDelegation,
    AgentRole,
    AgentRun,
    AgentRunBudget,
    AgentRunStatus,
)

if TYPE_CHECKING:
    from core.agent_mesh.runtime import UnifiedAgentRuntime
    from core.repositories.base_agent_mesh import BaseAgentMeshRepository

logger = logging.getLogger("aura.agent_mesh.mesh")


class IntelligenceMeshCoordinator:
    """Manages role-specialized agent delegation with hard boundary enforcement."""

    def __init__(
        self,
        repository: BaseAgentMeshRepository | Any,
        runtime: UnifiedAgentRuntime | None = None,
        max_depth: int = 3,
        max_fan_out: int = 5,
    ):
        self.repository = repository
        self.runtime = runtime
        self.max_depth = max_depth
        self.max_fan_out = max_fan_out

    def set_runtime(self, runtime: UnifiedAgentRuntime) -> None:
        self.runtime = runtime

    def list_roles(self) -> list[dict[str, str]]:
        """List available specialized intelligence mesh roles."""
        return [
            {"role": r.value, "description": f"Specialized {r.value} agent"}
            for r in AgentRole
        ]

    def delegate_task(
        self,
        parent_run_id: str,
        tenant_id: str,
        role: AgentRole | str,
        subtask_intent: str,
        capabilities: list[str] | None = None,
        budget: AgentRunBudget | None = None,
    ) -> AgentRun:
        """Spawn a bounded child agent run (Invariants M59-F27 through M59-F36)."""
        role_enum = AgentRole(role) if isinstance(role, str) else role

        # 1. Fetch parent run and verify existence and tenant ownership
        parent_run = self.repository.get_run(parent_run_id, tenant_id)
        if not parent_run:
            raise ValueError(f"Parent run '{parent_run_id}' not found for tenant '{tenant_id}'.")

        if parent_run.is_terminal():
            raise RuntimeError(f"Cannot delegate from terminal parent run '{parent_run_id}' (status: {parent_run.status.value}).")

        # 2. Enforce Max Depth Invariant (Invariant M59-F32)
        child_depth = parent_run.depth + 1
        if child_depth > self.max_depth:
            raise PermissionError(f"Delegation depth limit ({self.max_depth}) exceeded. Attempted depth: {child_depth}.")

        # 3. Enforce Max Fan-out Invariant (Invariant M59-F33)
        existing_delegations = self.repository.list_delegations(parent_run_id, tenant_id)
        if len(existing_delegations) >= self.max_fan_out:
            raise PermissionError(f"Fan-out limit ({self.max_fan_out}) exceeded for parent run '{parent_run_id}'.")

        # 4. Cycle Detection (Invariant M59-F34)
        self._assert_no_delegation_cycle(parent_run_id, tenant_id)

        # 5. Budget Inheritance Invariant (Invariant M59-F29)
        child_budget = self._calculate_child_budget(parent_run.budget, budget)

        # 6. Create Child Run (Invariant M59-F31: child_tenant == parent_tenant)
        child_run = AgentRun(
            run_id=f"run_{uuid4().hex[:16]}",
            tenant_id=tenant_id,
            user_id=parent_run.user_id,
            parent_run_id=parent_run_id,
            correlation_id=parent_run.correlation_id,
            causation_id=parent_run_id,
            intent=subtask_intent,
            status=AgentRunStatus.PENDING,
            depth=child_depth,
            budget=child_budget,
            created_at=time.time(),
        )
        saved_child = self.repository.save_run(child_run)

        # 7. Record Delegation Record
        delegation = AgentDelegation(
            delegation_id=f"del_{uuid4().hex[:16]}",
            parent_run_id=parent_run_id,
            child_run_id=saved_child.run_id,
            tenant_id=tenant_id,
            role=role_enum,
            capabilities=capabilities or [],
            budget_allocated=child_budget,
            status="active",
            created_at=time.time(),
        )
        self.repository.save_delegation(delegation)

        # 8. If runtime is configured, execute child run
        if self.runtime:
            saved_child = self.runtime.execute_run(saved_child)

        return saved_child

    def _assert_no_delegation_cycle(self, parent_run_id: str, tenant_id: str) -> None:
        """Traverse ancestor chain to detect circular delegation (Invariant M59-F34)."""
        visited = set()
        curr_id = parent_run_id

        while curr_id:
            if curr_id in visited:
                raise ValueError(f"Delegation cycle detected involving run '{curr_id}'.")
            visited.add(curr_id)
            run = self.repository.get_run(curr_id, tenant_id)
            if not run:
                break
            curr_id = run.parent_run_id

    def _calculate_child_budget(
        self, parent_budget: AgentRunBudget, requested_budget: AgentRunBudget | None
    ) -> AgentRunBudget:
        """Ensure child budget never exceeds parent budget (Invariant M59-F29)."""
        if not requested_budget:
            return AgentRunBudget(
                max_iterations=min(10, parent_budget.max_iterations),
                max_tool_calls=min(20, parent_budget.max_tool_calls),
                max_model_calls=min(10, parent_budget.max_model_calls),
                max_retries=parent_budget.max_retries,
                timeout_seconds=min(120.0, parent_budget.timeout_seconds),
            )

        return AgentRunBudget(
            max_iterations=min(requested_budget.max_iterations, parent_budget.max_iterations),
            max_tool_calls=min(requested_budget.max_tool_calls, parent_budget.max_tool_calls),
            max_model_calls=min(requested_budget.max_model_calls, parent_budget.max_model_calls),
            max_retries=min(requested_budget.max_retries, parent_budget.max_retries),
            timeout_seconds=min(requested_budget.timeout_seconds, parent_budget.timeout_seconds),
            max_tokens=min(requested_budget.max_tokens, parent_budget.max_tokens),
        )
