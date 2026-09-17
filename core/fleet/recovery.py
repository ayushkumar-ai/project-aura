"""M55 — Fleet Crash Recovery & Orphan Lease Sweeper.

Periodically detects expired worker nodes, fences abandoned leases,
requeues or fails orphaned tasks, and reconciles tenant in-flight counters.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from core.repositories.base_fleet import BaseFleetRepository

logger = logging.getLogger("aura.fleet.recovery")


class FleetRecoveryService:
    """Background service that reaps dead workers and recovers abandoned leases."""

    def __init__(
        self,
        fleet_repo: BaseFleetRepository,
        sweep_interval_seconds: float = 10.0,
        worker_expiry_seconds: float = 15.0,
        lease_expiry_seconds: float = 30.0,
    ) -> None:
        self.fleet_repo = fleet_repo
        self.interval = max(0.5, sweep_interval_seconds)
        self.worker_expiry_seconds = max(1.0, worker_expiry_seconds)
        self.lease_expiry_seconds = max(1.0, lease_expiry_seconds)

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_sweep_time: Optional[datetime] = None
        self._last_report: Dict[str, Any] = {}

    @property
    def is_running(self) -> bool:
        return not self._stop_event.is_set() and self._thread is not None and self._thread.is_alive()

    @property
    def last_report(self) -> Dict[str, Any]:
        return dict(self._last_report)

    def start(self) -> None:
        """Start background recovery sweeper thread."""
        with self._lock:
            if self.is_running:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._sweep_loop,
                name="aura-fleet-recovery-sweeper",
                daemon=True,
            )
            self._thread.start()
            logger.info(
                f"FleetRecoveryService started (sweep_interval={self.interval}s, worker_expiry={self.worker_expiry_seconds}s)"
            )

    def stop(self, timeout: float = 5.0) -> None:
        """Stop background recovery sweeper thread cleanly."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        logger.info("FleetRecoveryService stopped cleanly.")

    def sweep_once(self) -> Dict[str, Any]:
        """Perform a single synchronous crash recovery and orphan lease sweep."""
        now = datetime.now(timezone.utc)
        report: Dict[str, Any] = {
            "timestamp": now.isoformat(),
            "reaped_workers": [],
            "recovered_leases": [],
            "reconciled_tenants": {},
            "errors": [],
        }

        # 1. Reap dead workers
        try:
            reaped = self.fleet_repo.reap_expired_workers(self.worker_expiry_seconds)
            report["reaped_workers"] = reaped
            if reaped:
                logger.info(f"Reaped {len(reaped)} expired workers: {reaped}")
        except Exception as e:
            logger.error(f"Error reaping expired workers: {e}")
            report["errors"].append(f"reap_expired_workers: {e}")

        # 2. Sweep orphan leases & recover tasks
        try:
            recovered = self.fleet_repo.sweep_orphaned_leases(self.lease_expiry_seconds)
            report["recovered_leases"] = recovered
            if recovered:
                logger.info(f"Recovered {len(recovered)} orphaned resource leases: {recovered}")
        except Exception as e:
            logger.error(f"Error sweeping orphaned leases: {e}")
            report["errors"].append(f"sweep_orphaned_leases: {e}")

        # 3. Reconcile tenant active task counts
        try:
            reconciled = self.fleet_repo.reconcile_tenant_capacities()
            report["reconciled_tenants"] = reconciled
            if reconciled:
                logger.info(f"Reconciled {len(reconciled)} tenant capacity counters: {reconciled}")
        except Exception as e:
            logger.error(f"Error reconciling tenant capacities: {e}")
            report["errors"].append(f"reconcile_tenant_capacities: {e}")

        self._last_sweep_time = now
        self._last_report = report
        return report

    def _sweep_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.sweep_once()
            except Exception as e:
                logger.error(f"Unexpected error in recovery sweep loop: {e}")
            self._stop_event.wait(timeout=self.interval)
