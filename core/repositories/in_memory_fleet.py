"""M55 — In-Memory Fleet Repository.

Thread-safe in-memory implementation of BaseFleetRepository for unit testing and local development.
Faithfully emulates PostgreSQL row locking, monotonic fencing token progression,
lease expiration, multi-tenant deficit fairness, and orphan lease recovery.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
import time
from typing import Any, Dict, List, Optional
import uuid

from core.fleet.types import (
    AttemptStatus,
    ClaimedTask,
    ExecutionAttemptRecord,
    FencingTokenMismatchError,
    LeaseAlreadyHeldError,
    LeaseExpiredError,
    LeaseState,
    TenantLimitExceededError,
    TenantWorkerLimitRecord,
    WorkerLeaseRecord,
    WorkerNotHealthyError,
    WorkerRecord,
    WorkerStatus,
)
from core.repositories.base_fleet import BaseFleetRepository
from core.repositories.base import BaseTaskRepository


class InMemoryFleetRepository(BaseFleetRepository):
    """Thread-safe in-memory implementation of BaseFleetRepository."""

    def __init__(self, task_repo: Optional[BaseTaskRepository] = None) -> None:
        self.task_repo = task_repo
        self._lock = threading.RLock()

        # In-memory storage structures
        self._workers: Dict[str, WorkerRecord] = {}
        self._leases: Dict[str, WorkerLeaseRecord] = {}  # key: f"{resource_type}:{resource_id}"
        self._resource_fencing_counters: Dict[str, int] = {}  # key: f"{resource_type}:{resource_id}" -> latest int
        self._attempts: Dict[str, ExecutionAttemptRecord] = {}  # key: attempt_id
        self._tenant_limits: Dict[str, TenantWorkerLimitRecord] = {}  # key: tenant_id

    # --- Worker Node Lifecycle ---

    def register_worker(self, worker: WorkerRecord) -> WorkerRecord:
        with self._lock:
            existing = self._workers.get(worker.worker_id)
            gen = (existing.generation + 1) if existing else worker.generation
            registered = WorkerRecord(
                worker_id=worker.worker_id,
                instance_id=worker.instance_id,
                hostname=worker.hostname,
                process_id=worker.process_id,
                incarnation_token=worker.incarnation_token,
                generation=gen,
                status=worker.status,
                capabilities=list(worker.capabilities),
                concurrency_limit=worker.concurrency_limit,
                active_task_count=0,
                heartbeat_interval_seconds=worker.heartbeat_interval_seconds,
                missed_heartbeats_threshold=worker.missed_heartbeats_threshold,
                last_heartbeat_at=datetime.now(timezone.utc),
                draining_since=worker.draining_since,
                created_at=existing.created_at if existing else datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
            self._workers[worker.worker_id] = registered
            return registered

    def update_worker_heartbeat(self, worker_id: str, incarnation_token: str) -> bool:
        with self._lock:
            worker = self._workers.get(worker_id)
            if not worker or worker.incarnation_token != incarnation_token:
                return False
            if worker.status in (WorkerStatus.STOPPED, WorkerStatus.EXPIRED):
                return False
            worker.last_heartbeat_at = datetime.now(timezone.utc)
            worker.updated_at = datetime.now(timezone.utc)
            if worker.status == WorkerStatus.UNHEALTHY:
                worker.status = WorkerStatus.HEALTHY
            return True

    def update_worker_status(
        self,
        worker_id: str,
        incarnation_token: str,
        status: WorkerStatus,
        draining_since: Optional[datetime] = None,
    ) -> bool:
        with self._lock:
            worker = self._workers.get(worker_id)
            if not worker or worker.incarnation_token != incarnation_token:
                return False
            worker.status = status
            if draining_since is not None:
                worker.draining_since = draining_since
            elif status == WorkerStatus.DRAINING and worker.draining_since is None:
                worker.draining_since = datetime.now(timezone.utc)
            worker.updated_at = datetime.now(timezone.utc)
            return True

    def get_worker(self, worker_id: str) -> Optional[WorkerRecord]:
        with self._lock:
            return self._workers.get(worker_id)

    def list_workers(self, status: Optional[WorkerStatus] = None) -> List[WorkerRecord]:
        with self._lock:
            if status is None:
                return list(self._workers.values())
            return [w for w in self._workers.values() if w.status == status]

    def unregister_worker(self, worker_id: str, incarnation_token: str) -> bool:
        with self._lock:
            worker = self._workers.get(worker_id)
            if not worker or worker.incarnation_token != incarnation_token:
                return False
            worker.status = WorkerStatus.STOPPED
            worker.updated_at = datetime.now(timezone.utc)
            return True

    # --- Distributed Leases & Monotonic Fencing ---

    def _resource_key(self, resource_type: str, resource_id: str) -> str:
        return f"{resource_type}:{resource_id}"

    def acquire_lease(
        self,
        resource_type: str,
        resource_id: str,
        worker_id: str,
        incarnation_token: str,
        duration_seconds: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[WorkerLeaseRecord]:
        with self._lock:
            worker = self._workers.get(worker_id)
            if not worker or worker.incarnation_token != incarnation_token:
                return None
            if worker.status != WorkerStatus.HEALTHY:
                return None

            key = self._resource_key(resource_type, resource_id)
            now = datetime.now(timezone.utc)
            existing = self._leases.get(key)

            if existing and existing.lease_state.is_valid and not existing.is_expired(now):
                if existing.worker_id != worker_id or existing.incarnation_token != incarnation_token:
                    # Already held by another active worker
                    return None

            # Monotonically increment fencing token
            current_token = self._resource_fencing_counters.get(key, 0)
            next_token = current_token + 1
            self._resource_fencing_counters[key] = next_token

            expires_at = now + timedelta(seconds=max(1.0, duration_seconds))
            lease = WorkerLeaseRecord(
                lease_id=f"lse_{uuid.uuid4().hex[:12]}",
                resource_type=resource_type,
                resource_id=resource_id,
                worker_id=worker_id,
                incarnation_token=incarnation_token,
                fencing_token=next_token,
                lease_state=LeaseState.ACTIVE,
                acquired_at=now,
                expires_at=expires_at,
                renewed_at=now,
                metadata=metadata or {},
            )
            self._leases[key] = lease
            return lease

    def renew_lease(
        self,
        resource_type: str,
        resource_id: str,
        worker_id: str,
        incarnation_token: str,
        fencing_token: int,
        duration_seconds: float,
    ) -> bool:
        with self._lock:
            key = self._resource_key(resource_type, resource_id)
            now = datetime.now(timezone.utc)
            lease = self._leases.get(key)
            if not lease:
                return False
            if (
                lease.worker_id != worker_id
                or lease.incarnation_token != incarnation_token
                or lease.fencing_token != fencing_token
            ):
                return False
            if not lease.lease_state.is_valid:
                return False

            lease.expires_at = now + timedelta(seconds=max(1.0, duration_seconds))
            lease.renewed_at = now
            lease.lease_state = LeaseState.RENEWED
            return True

    def release_lease(
        self,
        resource_type: str,
        resource_id: str,
        worker_id: str,
        incarnation_token: str,
        fencing_token: int,
    ) -> bool:
        with self._lock:
            key = self._resource_key(resource_type, resource_id)
            lease = self._leases.get(key)
            if not lease:
                return False
            if (
                lease.worker_id != worker_id
                or lease.incarnation_token != incarnation_token
                or lease.fencing_token != fencing_token
            ):
                return False

            lease.lease_state = LeaseState.RELEASED
            return True

    def get_lease(self, resource_type: str, resource_id: str) -> Optional[WorkerLeaseRecord]:
        with self._lock:
            key = self._resource_key(resource_type, resource_id)
            return self._leases.get(key)

    def verify_fencing(
        self,
        resource_type: str,
        resource_id: str,
        worker_id: str,
        incarnation_token: str,
        fencing_token: int,
    ) -> bool:
        with self._lock:
            key = self._resource_key(resource_type, resource_id)
            lease = self._leases.get(key)
            if not lease:
                return False
            now = datetime.now(timezone.utc)
            if lease.is_expired(now) or not lease.lease_state.is_valid:
                return False
            return (
                lease.worker_id == worker_id
                and lease.incarnation_token == incarnation_token
                and lease.fencing_token == fencing_token
            )

    def batch_renew_worker_leases(
        self,
        worker_id: str,
        incarnation_token: str,
        duration_seconds: float,
    ) -> int:
        with self._lock:
            count = 0
            now = datetime.now(timezone.utc)
            new_expiry = now + timedelta(seconds=max(1.0, duration_seconds))
            for lease in self._leases.values():
                if (
                    lease.worker_id == worker_id
                    and lease.incarnation_token == incarnation_token
                    and lease.lease_state.is_valid
                    and not lease.is_expired(now)
                ):
                    lease.expires_at = new_expiry
                    lease.renewed_at = now
                    lease.lease_state = LeaseState.RENEWED
                    count += 1
            return count

    # --- Execution Attempts Ledger ---

    def record_attempt(self, attempt: ExecutionAttemptRecord) -> ExecutionAttemptRecord:
        with self._lock:
            self._attempts[attempt.attempt_id] = attempt
            return attempt

    def update_attempt_status(
        self,
        attempt_id: str,
        status: AttemptStatus,
        error_detail: str = "",
        finished_at: Optional[datetime] = None,
    ) -> bool:
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            if not attempt:
                return False
            if attempt.status.is_terminal:
                # Immutable once terminal
                return False
            attempt.status = status
            attempt.error_detail = error_detail or attempt.error_detail
            attempt.finished_at = finished_at or datetime.now(timezone.utc)
            return True

    def get_attempts_for_resource(self, resource_type: str, resource_id: str) -> List[ExecutionAttemptRecord]:
        with self._lock:
            return sorted(
                [a for a in self._attempts.values() if a.resource_type == resource_type and a.resource_id == resource_id],
                key=lambda x: x.started_at,
            )

    # --- Tenant Concurrency Quotas & Fairness ---

    def get_tenant_limits(self, tenant_id: str) -> TenantWorkerLimitRecord:
        with self._lock:
            if tenant_id not in self._tenant_limits:
                self._tenant_limits[tenant_id] = TenantWorkerLimitRecord(tenant_id=tenant_id)
            return self._tenant_limits[tenant_id]

    def set_tenant_limits(self, limits: TenantWorkerLimitRecord) -> TenantWorkerLimitRecord:
        with self._lock:
            limits.updated_at = datetime.now(timezone.utc)
            self._tenant_limits[limits.tenant_id] = limits
            return limits

    def adjust_tenant_active_count(self, tenant_id: str, delta: int) -> int:
        with self._lock:
            record = self.get_tenant_limits(tenant_id)
            new_count = record.active_task_count + delta
            if new_count < 0:
                raise ValueError(f"Tenant {tenant_id} active_task_count cannot underflow below 0 (attempted {new_count})")
            record.active_task_count = new_count
            record.updated_at = datetime.now(timezone.utc)
            return new_count

    def reconcile_tenant_capacities(self) -> Dict[str, int]:
        with self._lock:
            now = datetime.now(timezone.utc)
            # Count truly active task leases per tenant
            active_counts: Dict[str, int] = {}
            for lease in self._leases.values():
                if lease.resource_type == "task" and lease.lease_state.is_valid and not lease.is_expired(now):
                    tenant_id = lease.metadata.get("tenant_id") or lease.metadata.get("user_id")
                    if tenant_id:
                        active_counts[tenant_id] = active_counts.get(tenant_id, 0) + 1

            reconciled = {}
            for tenant_id, record in self._tenant_limits.items():
                expected = active_counts.get(tenant_id, 0)
                if record.active_task_count != expected:
                    record.active_task_count = expected
                    record.updated_at = now
                    reconciled[tenant_id] = expected
            return reconciled

    # --- Fair Task Claiming & Orchestration ---

    def claim_next_fair_task(
        self,
        worker_id: str,
        incarnation_token: str,
        lease_duration_seconds: float = 30.0,
    ) -> Optional[ClaimedTask]:
        with self._lock:
            worker = self._workers.get(worker_id)
            if not worker or worker.incarnation_token != incarnation_token:
                return None
            if worker.status != WorkerStatus.HEALTHY:
                return None
            if worker.active_task_count >= worker.concurrency_limit:
                return None

            if not self.task_repo:
                return None

            # Fetch pending tasks
            now = datetime.now(timezone.utc)
            tasks = getattr(self.task_repo, "_tasks", {})
            if isinstance(tasks, dict):
                candidate_tasks = []
                for t in tasks.values():
                    st = t.get("status") if isinstance(t, dict) else getattr(t, "status", None)
                    if st == "pending":
                        candidate_tasks.append(t)
            else:
                candidate_tasks = []

            if not candidate_tasks:
                return None

            # Filter candidates where tenant has capacity and sort by lowest tenant utilization ratio
            eligible = []
            for t in candidate_tasks:
                user_id = t.get("user_id", "") if isinstance(t, dict) else getattr(t, "user_id", "")
                created_at = t.get("created_at", 0) if isinstance(t, dict) else getattr(t, "created_at", now)
                if not user_id:
                    continue
                t_limits = self.get_tenant_limits(user_id)
                if t_limits.has_capacity:
                    eligible.append((t_limits.utilization_ratio, created_at, t))

            if not eligible:
                return None

            # Sort by lowest utilization ratio, then oldest task
            eligible.sort(key=lambda x: (x[0], x[1]))
            chosen_tuple = eligible[0]
            task = chosen_tuple[2]
            task_id = str(task.get("id") if isinstance(task, dict) else task.id)
            user_id = str(task.get("user_id") if isinstance(task, dict) else task.user_id)

            # Acquire lease
            lease = self.acquire_lease(
                resource_type="task",
                resource_id=task_id,
                worker_id=worker_id,
                incarnation_token=incarnation_token,
                duration_seconds=lease_duration_seconds,
                metadata={"tenant_id": user_id, "user_id": user_id},
            )
            if not lease:
                return None

            # Increment tenant and worker active counts
            self.adjust_tenant_active_count(user_id, 1)
            worker.active_task_count += 1
            worker.updated_at = now

            # Transition task to running in task_repo
            if isinstance(task, dict):
                task["status"] = "running"
                if not task.get("started_at"):
                    task["started_at"] = time.time()
                task["updated_at"] = time.time()
            else:
                task.status = "running"
                if hasattr(task, "started_at") and not task.started_at:
                    task.started_at = now
                if hasattr(task, "updated_at"):
                    task.updated_at = now

            # Record attempt
            attempt_num = len(self.get_attempts_for_resource("task", task_id)) + 1
            attempt = ExecutionAttemptRecord(
                attempt_id=f"att_{uuid.uuid4().hex[:12]}",
                resource_type="task",
                resource_id=task_id,
                tenant_id=user_id,
                worker_id=worker_id,
                incarnation_token=incarnation_token,
                fencing_token=lease.fencing_token,
                attempt_number=attempt_num,
                status=AttemptStatus.RUNNING,
                started_at=now,
            )
            self.record_attempt(attempt)

            return ClaimedTask(
                task_id=task_id,
                user_id=user_id,
                lease_id=lease.lease_id,
                fencing_token=lease.fencing_token,
                incarnation_token=incarnation_token,
                attempt_id=attempt.attempt_id,
                attempt_number=attempt_num,
                task_data={"id": task_id, "user_id": user_id, "workflow": task.get("workflow", {}) if isinstance(task, dict) else getattr(task, "workflow", {})},
            )

    # --- Sweeping & Crash Recovery ---

    def reap_expired_workers(self, threshold_seconds: float = 15.0) -> List[str]:
        with self._lock:
            reaped = []
            now = datetime.now(timezone.utc)
            threshold = timedelta(seconds=threshold_seconds)
            for worker in self._workers.values():
                if worker.status in (WorkerStatus.HEALTHY, WorkerStatus.UNHEALTHY, WorkerStatus.DRAINING):
                    if now - worker.last_heartbeat_at > threshold:
                        worker.status = WorkerStatus.EXPIRED
                        worker.updated_at = now
                        reaped.append(worker.worker_id)
            return reaped

    def sweep_orphaned_leases(self, lease_expiry_seconds: float = 30.0) -> List[str]:
        with self._lock:
            recovered = []
            now = datetime.now(timezone.utc)
            for key, lease in list(self._leases.items()):
                if lease.lease_state.is_valid:
                    worker = self._workers.get(lease.worker_id)
                    is_worker_dead = not worker or worker.status == WorkerStatus.EXPIRED or worker.status == WorkerStatus.STOPPED
                    if lease.is_expired(now) or is_worker_dead:
                        lease.lease_state = LeaseState.EXPIRED
                        recovered.append(lease.resource_id)

                        # Mark attempt recovered
                        for attempt in self._attempts.values():
                            if (
                                attempt.resource_id == lease.resource_id
                                and attempt.fencing_token == lease.fencing_token
                                and attempt.status == AttemptStatus.RUNNING
                            ):
                                attempt.status = AttemptStatus.RECOVERED
                                attempt.finished_at = now
                                attempt.error_detail = "Lease expired or worker lost heartbeat"

                        # Decrement tenant active count if task lease
                        if lease.resource_type == "task":
                            tenant_id = lease.metadata.get("tenant_id") or lease.metadata.get("user_id")
                            if tenant_id and tenant_id in self._tenant_limits:
                                try:
                                    self.adjust_tenant_active_count(tenant_id, -1)
                                except ValueError:
                                    pass

                            # Re-queue task if in task_repo
                            if self.task_repo:
                                tasks = getattr(self.task_repo, "_tasks", {})
                                task = tasks.get(lease.resource_id)
                                if task:
                                    st = task.get("status") if isinstance(task, dict) else getattr(task, "status", None)
                                    if st == "running":
                                        retries = task.get("retry_count", 0) if isinstance(task, dict) else getattr(task, "retry_count", 0)
                                        max_retries = task.get("max_retries", 3) if isinstance(task, dict) else getattr(task, "max_retries", 3)
                                        if retries < max_retries:
                                            if isinstance(task, dict):
                                                task["status"] = "pending"
                                                task["retry_count"] = retries + 1
                                                task["updated_at"] = time.time()
                                            else:
                                                task.status = "pending"
                                                task.retry_count = retries + 1
                                                task.updated_at = now
                                        else:
                                            if isinstance(task, dict):
                                                task["status"] = "failed"
                                                task["error_message"] = f"Exhausted max retries ({max_retries}) after worker failure"
                                                task["updated_at"] = time.time()
                                            else:
                                                task.status = "failed"
                                                task.error_message = f"Exhausted max retries ({max_retries}) after worker failure"
                                                task.updated_at = now

            return recovered
