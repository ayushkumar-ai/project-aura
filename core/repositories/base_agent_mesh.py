"""M59 — Base Agent Mesh Repository Abstract Interface.

Defines persistence contracts for AgentRuns, execution steps, mesh delegations,
lifecycle events, security audits, and tenant lifecycle purge operations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.agent_mesh.types import (
    AgentDelegation,
    AgentMeshAudit,
    AgentMeshEvent,
    AgentRun,
    AgentRunStatus,
    AgentRunStep,
)


class BaseAgentMeshRepository(ABC):
    """Abstract repository for Project AURA unified agent runtime and intelligence mesh."""

    # 1. Agent Runs
    @abstractmethod
    def save_run(self, run: AgentRun) -> AgentRun:
        """Create or update an AgentRun record."""
        ...

    @abstractmethod
    def get_run(self, run_id: str, tenant_id: str) -> AgentRun | None:
        """Fetch a single AgentRun strictly scoped to a tenant."""
        ...

    @abstractmethod
    def list_runs(self, tenant_id: str, limit: int = 50) -> list[AgentRun]:
        """List AgentRuns for a tenant."""
        ...

    @abstractmethod
    def update_run_status(
        self,
        run_id: str,
        tenant_id: str,
        status: AgentRunStatus,
        error_detail: str | None = None,
    ) -> AgentRun | None:
        """Update run lifecycle status."""
        ...

    @abstractmethod
    def delete_run(self, run_id: str, tenant_id: str) -> bool:
        """Permanently delete an AgentRun and all child records."""
        ...

    # 2. Agent Steps
    @abstractmethod
    def save_step(self, step: AgentRunStep) -> AgentRunStep:
        """Record an execution step for a run."""
        ...

    @abstractmethod
    def list_steps(self, run_id: str, tenant_id: str) -> list[AgentRunStep]:
        """List all execution steps for a run ordered by step number."""
        ...

    # 3. Delegations
    @abstractmethod
    def save_delegation(self, delegation: AgentDelegation) -> AgentDelegation:
        """Record a parent-child agent delegation."""
        ...

    @abstractmethod
    def list_delegations(self, parent_run_id: str, tenant_id: str) -> list[AgentDelegation]:
        """List all child delegations for a parent run."""
        ...

    # 4. Events
    @abstractmethod
    def save_event(self, event: AgentMeshEvent) -> AgentMeshEvent:
        """Record an observable lifecycle event."""
        ...

    @abstractmethod
    def list_events(self, run_id: str, tenant_id: str) -> list[AgentMeshEvent]:
        """List all lifecycle events for an agent run."""
        ...

    # 5. Audits
    @abstractmethod
    def save_audit(self, audit: AgentMeshAudit) -> AgentMeshAudit:
        """Record an immutable security audit event."""
        ...

    @abstractmethod
    def list_audits(self, tenant_id: str, limit: int = 100) -> list[AgentMeshAudit]:
        """List audit events for a tenant."""
        ...

    # 6. Tenant Lifecycle
    @abstractmethod
    def purge_tenant_data(self, tenant_id: str) -> int:
        """Hard-delete all agent runs, steps, delegations, events, and audits for a tenant (GDPR)."""
        ...
