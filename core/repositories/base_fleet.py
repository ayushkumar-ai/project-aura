"""M55 — Base Fleet Repository Interface.

Defines the abstract persistence contract for worker registration, distributed leases,
monotonic fencing, execution attempts, and tenant concurrency quotas.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.fleet.types import (
    AttemptStatus,
    ClaimedTask,
    ExecutionAttemptRecord,
    TenantWorkerLimitRecord,
    WorkerLeaseRecord,
    WorkerRecord,
    WorkerStatus,
)


class BaseFleetRepository(ABC):
    """Abstract Base Class for worker fleet and distributed lease persistence."""

    # --- Worker Fleet Node Lifecycle ---

    @abstractmethod
    def register_worker(self, worker: WorkerRecord) -> WorkerRecord:
        """Register a new worker node with its incarnation token and capabilities."""
        pass

    @abstractmethod
    def update_worker_heartbeat(self, worker_id: str, incarnation_token: str) -> bool:
        """Atomically record a heartbeat timestamp if worker_id and incarnation match."""
        pass

    @abstractmethod
    def update_worker_status(
        self,
        worker_id: str,
        incarnation_token: str,
        status: WorkerStatus,
        draining_since: Optional[datetime] = None,
    ) -> bool:
        """Update worker lifecycle status if incarnation token matches."""
        pass

    @abstractmethod
    def get_worker(self, worker_id: str) -> Optional[WorkerRecord]:
        """Retrieve worker metadata by ID."""
        pass

    @abstractmethod
    def list_workers(self, status: Optional[WorkerStatus] = None) -> List[WorkerRecord]:
        """List registered workers, optionally filtered by lifecycle status."""
        pass

    @abstractmethod
    def unregister_worker(self, worker_id: str, incarnation_token: str) -> bool:
        """Mark worker as STOPPED and clean up ephemeral worker state."""
        pass

    # --- Distributed Leases & Monotonic Fencing ---

    @abstractmethod
    def acquire_lease(
        self,
        resource_type: str,
        resource_id: str,
        worker_id: str,
        incarnation_token: str,
        duration_seconds: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[WorkerLeaseRecord]:
        """Atomically acquire a lease on a resource with a strictly incremented fencing token."""
        pass

    @abstractmethod
    def renew_lease(
        self,
        resource_type: str,
        resource_id: str,
        worker_id: str,
        incarnation_token: str,
        fencing_token: int,
        duration_seconds: float,
    ) -> bool:
        """Atomically extend lease expiry if worker, incarnation, and fencing token match."""
        pass

    @abstractmethod
    def release_lease(
        self,
        resource_type: str,
        resource_id: str,
        worker_id: str,
        incarnation_token: str,
        fencing_token: int,
    ) -> bool:
        """Atomically release an active lease upon clean task completion."""
        pass

    @abstractmethod
    def get_lease(self, resource_type: str, resource_id: str) -> Optional[WorkerLeaseRecord]:
        """Retrieve active or historical lease for a resource."""
        pass

    @abstractmethod
    def verify_fencing(
        self,
        resource_type: str,
        resource_id: str,
        worker_id: str,
        incarnation_token: str,
        fencing_token: int,
    ) -> bool:
        """Verify that the given worker/incarnation/fencing_token holds the authoritative active lease."""
        pass

    @abstractmethod
    def batch_renew_worker_leases(
        self,
        worker_id: str,
        incarnation_token: str,
        duration_seconds: float,
    ) -> int:
        """Batch renew all active leases held by a worker during heartbeat."""
        pass

    # --- Execution Attempts Ledger ---

    @abstractmethod
    def record_attempt(self, attempt: ExecutionAttemptRecord) -> ExecutionAttemptRecord:
        """Record the start of an execution attempt."""
        pass

    @abstractmethod
    def update_attempt_status(
        self,
        attempt_id: str,
        status: AttemptStatus,
        error_detail: str = "",
        finished_at: Optional[datetime] = None,
    ) -> bool:
        """Record the terminal outcome of an execution attempt (immutable once terminal)."""
        pass

    @abstractmethod
    def get_attempts_for_resource(self, resource_type: str, resource_id: str) -> List[ExecutionAttemptRecord]:
        """List all execution attempts for a resource in chronological order."""
        pass

    # --- Tenant Concurrency Quotas & Fairness ---

    @abstractmethod
    def get_tenant_limits(self, tenant_id: str) -> TenantWorkerLimitRecord:
        """Retrieve tenant concurrency quota and live capacity (creates default if missing)."""
        pass

    @abstractmethod
    def set_tenant_limits(self, limits: TenantWorkerLimitRecord) -> TenantWorkerLimitRecord:
        """Configure or update tenant concurrency limits."""
        pass

    @abstractmethod
    def adjust_tenant_active_count(self, tenant_id: str, delta: int) -> int:
        """Atomically adjust tenant in-flight active task count (fails on negative underflow)."""
        pass

    @abstractmethod
    def reconcile_tenant_capacities(self) -> Dict[str, int]:
        """Audit and reconcile tenant active task counts against live active task leases."""
        pass

    # --- Fair Task Claiming & Orchestration ---

    @abstractmethod
    def claim_next_fair_task(
        self,
        worker_id: str,
        incarnation_token: str,
        lease_duration_seconds: float = 30.0,
    ) -> Optional[ClaimedTask]:
        """Atomically claim the next eligible pending task using deficit round-robin fairness."""
        pass

    # --- Sweeping & Crash Recovery ---

    @abstractmethod
    def reap_expired_workers(self, threshold_seconds: float = 15.0) -> List[str]:
        """Transition workers exceeding missed heartbeat threshold to EXPIRED."""
        pass

    @abstractmethod
    def sweep_orphaned_leases(self, lease_expiry_seconds: float = 30.0) -> List[str]:
        """Fence expired leases and recover orphaned tasks for retry or failure."""
        pass
