"""Mission Graph DAG Engine (M25).

Provides a bounded Directed Acyclic Graph (DAG) orchestrating campaign phases,
dependency resolution, cycle detection (Kahn's algorithm), and contingency branching.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any, Sequence

from core.campaign_types import (
    CampaignPhase,
    PhaseStatus,
    MAX_CAMPAIGN_PHASES,
)

logger = logging.getLogger("aura.mission_graph")


class MissionGraph:
    """Thread-safe DAG representing campaign execution phases and dependencies."""

    def __init__(self, phases: Sequence[CampaignPhase] = ()):
        self._lock = threading.RLock()
        self._phases: dict[str, CampaignPhase] = {}
        for p in phases:
            self.add_phase(p)

    def add_phase(self, phase: CampaignPhase) -> None:
        """Register a phase into the graph."""
        if not isinstance(phase, CampaignPhase):
            raise TypeError("phase must be a CampaignPhase instance.")

        with self._lock:
            if len(self._phases) >= MAX_CAMPAIGN_PHASES and phase.phase_id not in self._phases:
                raise ValueError(
                    f"MissionGraph phase limit reached ({MAX_CAMPAIGN_PHASES}). Cannot add '{phase.phase_id}'."
                )
            self._phases[phase.phase_id] = phase

    def remove_phase(self, phase_id: str) -> bool:
        """Safely remove a phase and clean up downstream references."""
        clean_id = str(phase_id).strip()
        with self._lock:
            if clean_id not in self._phases:
                return False
            del self._phases[clean_id]
            # Clean up dependencies in remaining phases
            for pid, ph in list(self._phases.items()):
                if clean_id in ph.depends_on_phase_ids:
                    new_deps = tuple(d for d in ph.depends_on_phase_ids if d != clean_id)
                    object.__setattr__(ph, "depends_on_phase_ids", new_deps)
            return True

    def get_phase(self, phase_id: str) -> CampaignPhase | None:
        """Retrieve a phase by ID."""
        with self._lock:
            return self._phases.get(str(phase_id).strip())

    def list_phases(self) -> list[CampaignPhase]:
        """Return all phases in registration order."""
        with self._lock:
            return list(self._phases.values())

    def update_phase(self, phase: CampaignPhase) -> None:
        """Overwrite an existing phase definition/state."""
        if not isinstance(phase, CampaignPhase):
            raise TypeError("phase must be a CampaignPhase instance.")
        with self._lock:
            if phase.phase_id not in self._phases:
                raise KeyError(f"Phase '{phase.phase_id}' does not exist in MissionGraph.")
            self._phases[phase.phase_id] = phase

    def update_phase_status(
        self,
        phase_id: str,
        status: PhaseStatus,
        started_at: float | None = None,
        completed_at: float | None = None,
        error: str | None = None,
        milestones: tuple[Any, ...] | None = None,
    ) -> CampaignPhase:
        """Atomically transition a phase's status."""
        clean_id = str(phase_id).strip()
        with self._lock:
            ph = self._phases.get(clean_id)
            if ph is None:
                raise KeyError(f"Phase '{clean_id}' does not exist in MissionGraph.")
            upd = ph.with_status(
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                error=error,
                milestones=milestones,
            )
            self._phases[clean_id] = upd
            return upd

    def validate_graph(self) -> None:
        """Validate acyclicity, valid dependency references, and non-empty structure."""
        with self._lock:
            if not self._phases:
                raise ValueError("MissionGraph is empty. At least one phase is required.")

            # Validate dependency existence
            for pid, ph in self._phases.items():
                for dep_id in ph.depends_on_phase_ids:
                    if dep_id not in self._phases:
                        raise ValueError(
                            f"Phase '{pid}' depends on non-existent phase '{dep_id}'."
                        )
                    if dep_id == pid:
                        raise ValueError(f"Self-dependency detected in phase '{pid}'.")

            # Cycle detection using Kahn's algorithm
            self.get_topological_order()

    def get_topological_order(self) -> list[str]:
        """Compute deterministic topological ordering of phases using Kahn's algorithm."""
        with self._lock:
            in_degree: dict[str, int] = {pid: 0 for pid in self._phases}
            adj: dict[str, list[str]] = {pid: [] for pid in self._phases}

            for pid, ph in self._phases.items():
                for dep_id in ph.depends_on_phase_ids:
                    adj[dep_id].append(pid)
                    in_degree[pid] += 1

            # Queue nodes with 0 in-degree (sorted for determinism)
            queue = deque(sorted([pid for pid, deg in in_degree.items() if deg == 0]))
            order: list[str] = []

            while queue:
                curr = queue.popleft()
                order.append(curr)

                for nxt in sorted(adj[curr]):
                    in_degree[nxt] -= 1
                    if in_degree[nxt] == 0:
                        queue.append(nxt)

            if len(order) != len(self._phases):
                remaining = set(self._phases.keys()) - set(order)
                raise ValueError(
                    f"Cyclic dependency detected in MissionGraph among phases: {sorted(remaining)}"
                )

            return order

    def get_ready_phases(self) -> list[CampaignPhase]:
        """Discover phases eligible for execution (all dependencies completed)."""
        with self._lock:
            ready: list[CampaignPhase] = []
            for pid, ph in self._phases.items():
                if ph.status != PhaseStatus.PENDING:
                    continue

                # If it is a contingency phase, it is only ready if explicitly triggered
                if ph.is_contingency:
                    continue

                deps_satisfied = True
                for dep_id in ph.depends_on_phase_ids:
                    dep_ph = self._phases.get(dep_id)
                    if dep_ph is None or dep_ph.status != PhaseStatus.COMPLETED:
                        deps_satisfied = False
                        break

                if deps_satisfied:
                    ready.append(ph)

            return ready

    def inject_contingency_branch(
        self,
        contingency_phase: CampaignPhase,
        failed_phase_id: str,
    ) -> None:
        """Inject a dynamic contingency phase to replace or recover from a failed phase."""
        if not isinstance(contingency_phase, CampaignPhase):
            raise TypeError("contingency_phase must be a CampaignPhase instance.")

        clean_failed = str(failed_phase_id).strip()
        with self._lock:
            if clean_failed not in self._phases:
                raise KeyError(f"Failed phase '{clean_failed}' does not exist in MissionGraph.")

            # Mark contingency phase
            marked_phase = CampaignPhase(
                phase_id=contingency_phase.phase_id,
                name=contingency_phase.name,
                goal_ids=contingency_phase.goal_ids,
                depends_on_phase_ids=contingency_phase.depends_on_phase_ids,
                milestones=contingency_phase.milestones,
                assigned_team_id=contingency_phase.assigned_team_id,
                assigned_role_id=contingency_phase.assigned_role_id,
                max_concurrency=contingency_phase.max_concurrency,
                timeout_seconds=contingency_phase.timeout_seconds,
                status=PhaseStatus.PENDING,
                is_contingency=True,
                contingency_for_phase_id=clean_failed,
                metadata=dict(contingency_phase.metadata),
            )
            self.add_phase(marked_phase)
            self.validate_graph()
            logger.info("Injected contingency phase '%s' for failed phase '%s'", marked_phase.phase_id, clean_failed)

    def is_terminal(self) -> bool:
        """Check if all non-contingency phases (or all active phases) have reached terminal states."""
        with self._lock:
            if not self._phases:
                return True
            for ph in self._phases.values():
                if ph.is_contingency and ph.status == PhaseStatus.PENDING:
                    continue  # Un-triggered contingency phases don't prevent termination
                if not ph.status.is_terminal():
                    return False
            return True

    def is_successful(self) -> bool:
        """Check if all planned execution phases completed successfully."""
        with self._lock:
            if not self._phases:
                return False
            for ph in self._phases.values():
                if ph.is_contingency and ph.status == PhaseStatus.PENDING:
                    continue
                if ph.status not in (PhaseStatus.COMPLETED, PhaseStatus.SKIPPED):
                    return False
            return True

    def has_failed_phases(self) -> bool:
        """Check if any phase failed."""
        with self._lock:
            return any(ph.status == PhaseStatus.FAILED for ph in self._phases.values())

    def get_phase_summary(self) -> dict[str, Any]:
        """Return statistics on phases by status."""
        with self._lock:
            counts = {st.value: 0 for st in PhaseStatus}
            for ph in self._phases.values():
                counts[ph.status.value] += 1
            return {
                "total_phases": len(self._phases),
                "counts_by_status": counts,
                "is_terminal": self.is_terminal(),
                "is_successful": self.is_successful(),
            }

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "phases": [p.to_dict() for p in self._phases.values()],
            }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MissionGraph:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        phases = [CampaignPhase.from_dict(p) for p in data.get("phases", [])]
        return cls(phases=phases)
