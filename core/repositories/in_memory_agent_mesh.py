"""M59 — In-Memory Agent Mesh Repository Implementation.

Thread-safe in-memory persistence for AgentRuns, execution steps, delegations, events, and audits.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from core.agent_mesh.types import (
    AgentDelegation,
    AgentMeshAudit,
    AgentMeshEvent,
    AgentRun,
    AgentRunStatus,
    AgentRunStep,
)
from core.repositories.base_agent_mesh import BaseAgentMeshRepository


class InMemoryAgentMeshRepository(BaseAgentMeshRepository):
    """Thread-safe in-memory implementation of BaseAgentMeshRepository."""

    def __init__(self):
        self._lock = threading.RLock()
        self._runs: dict[str, dict[str, AgentRun]] = {}                       # tenant_id -> {run_id: AgentRun}
        self._steps: dict[str, dict[str, list[AgentRunStep]]] = {}           # tenant_id -> {run_id: [AgentRunStep]}
        self._delegations: dict[str, dict[str, list[AgentDelegation]]] = {}   # tenant_id -> {parent_run_id: [AgentDelegation]}
        self._events: dict[str, dict[str, list[AgentMeshEvent]]] = {}         # tenant_id -> {run_id: [AgentMeshEvent]}
        self._audits: dict[str, list[AgentMeshAudit]] = {}                   # tenant_id -> [AgentMeshAudit]

    # 1. Agent Runs
    def save_run(self, run: AgentRun) -> AgentRun:
        with self._lock:
            if run.tenant_id not in self._runs:
                self._runs[run.tenant_id] = {}
            self._runs[run.tenant_id][run.run_id] = run
            return run

    def get_run(self, run_id: str, tenant_id: str) -> AgentRun | None:
        with self._lock:
            return self._runs.get(tenant_id, {}).get(run_id)

    def list_runs(self, tenant_id: str, limit: int = 50) -> list[AgentRun]:
        with self._lock:
            runs = list(self._runs.get(tenant_id, {}).values())
            runs.sort(key=lambda r: r.created_at, reverse=True)
            return runs[:limit]

    def update_run_status(
        self,
        run_id: str,
        tenant_id: str,
        status: AgentRunStatus,
        error_detail: str | None = None,
    ) -> AgentRun | None:
        with self._lock:
            run = self.get_run(run_id, tenant_id)
            if run:
                run.transition_to(status, error_detail=error_detail)
                return run
            return None

    def delete_run(self, run_id: str, tenant_id: str) -> bool:
        with self._lock:
            tenant_runs = self._runs.get(tenant_id, {})
            if run_id in tenant_runs:
                del tenant_runs[run_id]
                self._steps.get(tenant_id, {}).pop(run_id, None)
                self._delegations.get(tenant_id, {}).pop(run_id, None)
                self._events.get(tenant_id, {}).pop(run_id, None)
                return True
            return False

    # 2. Agent Steps
    def save_step(self, step: AgentRunStep) -> AgentRunStep:
        with self._lock:
            if step.tenant_id not in self._steps:
                self._steps[step.tenant_id] = {}
            if step.run_id not in self._steps[step.tenant_id]:
                self._steps[step.tenant_id][step.run_id] = []
            self._steps[step.tenant_id][step.run_id].append(step)
            return step

    def list_steps(self, run_id: str, tenant_id: str) -> list[AgentRunStep]:
        with self._lock:
            steps = list(self._steps.get(tenant_id, {}).get(run_id, []))
            steps.sort(key=lambda s: s.step_number)
            return steps

    # 3. Delegations
    def save_delegation(self, delegation: AgentDelegation) -> AgentDelegation:
        with self._lock:
            if delegation.tenant_id not in self._delegations:
                self._delegations[delegation.tenant_id] = {}
            if delegation.parent_run_id not in self._delegations[delegation.tenant_id]:
                self._delegations[delegation.tenant_id][delegation.parent_run_id] = []
            self._delegations[delegation.tenant_id][delegation.parent_run_id].append(delegation)
            return delegation

    def list_delegations(self, parent_run_id: str, tenant_id: str) -> list[AgentDelegation]:
        with self._lock:
            return list(self._delegations.get(tenant_id, {}).get(parent_run_id, []))

    # 4. Events
    def save_event(self, event: AgentMeshEvent) -> AgentMeshEvent:
        with self._lock:
            if event.tenant_id not in self._events:
                self._events[event.tenant_id] = {}
            if event.run_id not in self._events[event.tenant_id]:
                self._events[event.tenant_id][event.run_id] = []
            self._events[event.tenant_id][event.run_id].append(event)
            return event

    def list_events(self, run_id: str, tenant_id: str) -> list[AgentMeshEvent]:
        with self._lock:
            return list(self._events.get(tenant_id, {}).get(run_id, []))

    # 5. Audits
    def save_audit(self, audit: AgentMeshAudit) -> AgentMeshAudit:
        with self._lock:
            if audit.tenant_id not in self._audits:
                self._audits[audit.tenant_id] = []
            self._audits[audit.tenant_id].append(audit)
            return audit

    def list_audits(self, tenant_id: str, limit: int = 100) -> list[AgentMeshAudit]:
        with self._lock:
            audits = list(self._audits.get(tenant_id, []))
            audits.sort(key=lambda a: a.created_at, reverse=True)
            return audits[:limit]

    # 6. Purge
    def purge_tenant_data(self, tenant_id: str) -> int:
        with self._lock:
            count = len(self._runs.get(tenant_id, {}))
            self._runs.pop(tenant_id, None)
            self._steps.pop(tenant_id, None)
            self._delegations.pop(tenant_id, None)
            self._events.pop(tenant_id, None)
            self._audits.pop(tenant_id, None)
            return count
