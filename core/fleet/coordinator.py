"""M55 — Worker Fleet Coordinator.

Provides fleet-wide monitoring, administrative controls, tenant quota management,
and crash recovery coordination across distributed worker nodes.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional

from core.fleet.recovery import FleetRecoveryService
from core.fleet.types import TenantWorkerLimitRecord, WorkerRecord, WorkerStatus
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from core.repositories.base_fleet import BaseFleetRepository

logger = logging.getLogger("aura.fleet.coordinator")


class WorkerFleetCoordinator:
    """Administrative and coordination facade for the distributed worker fleet."""

    def __init__(
        self,
        fleet_repo: BaseFleetRepository,
        sweep_interval_seconds: float = 10.0,
        worker_expiry_seconds: float = 15.0,
        lease_expiry_seconds: float = 30.0,
    ) -> None:
        self.fleet_repo = fleet_repo
        self.recovery_service = FleetRecoveryService(
            fleet_repo=fleet_repo,
            sweep_interval_seconds=sweep_interval_seconds,
            worker_expiry_seconds=worker_expiry_seconds,
            lease_expiry_seconds=lease_expiry_seconds,
        )

    def start(self) -> None:
        """Start background coordination and recovery services."""
        self.recovery_service.start()
        logger.info("WorkerFleetCoordinator started.")

    def stop(self, timeout: float = 5.0) -> None:
        """Stop background coordination services cleanly."""
        self.recovery_service.stop(timeout=timeout)
        logger.info("WorkerFleetCoordinator stopped.")

    def get_fleet_status(self) -> Dict[str, Any]:
        """Aggregate high-level health and capacity metrics across the fleet."""
        workers = self.fleet_repo.list_workers()
        status_counts = {s.value: 0 for s in WorkerStatus}
        total_capacity = 0
        total_active_tasks = 0

        for w in workers:
            st = w.status.value if isinstance(w.status, WorkerStatus) else str(w.status)
            status_counts[st] = status_counts.get(st, 0) + 1
            if w.status == WorkerStatus.HEALTHY:
                total_capacity += w.concurrency_limit
                total_active_tasks += w.active_task_count

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_workers": len(workers),
            "status_breakdown": status_counts,
            "healthy_workers": status_counts.get("healthy", 0),
            "total_fleet_capacity": total_capacity,
            "total_active_tasks": total_active_tasks,
            "recovery_sweeper_running": self.recovery_service.is_running,
            "last_recovery_sweep": self.recovery_service.last_report,
        }

    def set_tenant_quota(
        self,
        tenant_id: str,
        max_active_tasks: int = 10,
        guaranteed_slots: int = 2,
        burst_capacity: int = 20,
    ) -> TenantWorkerLimitRecord:
        """Configure or update tenant concurrency limits."""
        record = TenantWorkerLimitRecord(
            tenant_id=tenant_id,
            max_active_tasks=max(1, max_active_tasks),
            guaranteed_slots=max(0, guaranteed_slots),
            burst_capacity=max(max_active_tasks, burst_capacity),
        )
        return self.fleet_repo.set_tenant_limits(record)

    def get_tenant_quota(self, tenant_id: str) -> TenantWorkerLimitRecord:
        """Retrieve tenant concurrency limit and utilization."""
        return self.fleet_repo.get_tenant_limits(tenant_id)

    def drain_worker(self, worker_id: str) -> bool:
        """Signal a worker to enter draining mode."""
        worker = self.fleet_repo.get_worker(worker_id)
        if not worker:
            return False
        return self.fleet_repo.update_worker_status(
            worker_id=worker_id,
            incarnation_token=worker.incarnation_token,
            status=WorkerStatus.DRAINING,
            draining_since=datetime.now(timezone.utc),
        )

    def trigger_recovery_sweep(self) -> Dict[str, Any]:
        """Trigger an immediate synchronous recovery sweep."""
        return self.recovery_service.sweep_once()
