"""M55 — Distributed Execution Scaling & Worker Fleet Coordination Types.

Defines domain models, enums, records, and exceptions for worker fleet lifecycle,
distributed leases, monotonic fencing tokens, and multi-tenant fairness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid


class WorkerStatus(str, Enum):
    """Worker lifecycle states."""
    STARTING = "starting"
    HEALTHY = "healthy"
    DRAINING = "draining"
    UNHEALTHY = "unhealthy"
    STOPPED = "stopped"
    EXPIRED = "expired"

    @property
    def is_active(self) -> bool:
        return self in (WorkerStatus.STARTING, WorkerStatus.HEALTHY, WorkerStatus.DRAINING)

    @property
    def can_claim(self) -> bool:
        return self == WorkerStatus.HEALTHY

    @property
    def is_terminal(self) -> bool:
        return self in (WorkerStatus.STOPPED, WorkerStatus.EXPIRED)


class LeaseState(str, Enum):
    """Resource lease states."""
    ACTIVE = "active"
    RENEWED = "renewed"
    RELEASED = "released"
    EXPIRED = "expired"
    FENCED = "fenced"

    @property
    def is_valid(self) -> bool:
        return self in (LeaseState.ACTIVE, LeaseState.RENEWED)


class AttemptStatus(str, Enum):
    """Execution attempt lifecycle states."""
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    FENCED = "fenced"
    RECOVERED = "recovered"

    @property
    def is_terminal(self) -> bool:
        return self in (
            AttemptStatus.COMPLETED,
            AttemptStatus.FAILED,
            AttemptStatus.TIMED_OUT,
            AttemptStatus.FENCED,
            AttemptStatus.RECOVERED,
        )


@dataclass
class WorkerRecord:
    """Represents a registered worker node in the fleet."""
    worker_id: str
    instance_id: str
    hostname: str
    process_id: int
    incarnation_token: str
    generation: int = 1
    status: WorkerStatus = WorkerStatus.STARTING
    capabilities: List[str] = field(default_factory=lambda: ["*"])
    concurrency_limit: int = 4
    active_task_count: int = 0
    heartbeat_interval_seconds: float = 5.0
    missed_heartbeats_threshold: int = 3
    last_heartbeat_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    draining_since: Optional[datetime] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "instance_id": self.instance_id,
            "hostname": self.hostname,
            "process_id": self.process_id,
            "incarnation_token": self.incarnation_token,
            "generation": self.generation,
            "status": self.status.value if isinstance(self.status, WorkerStatus) else str(self.status),
            "capabilities": self.capabilities,
            "concurrency_limit": self.concurrency_limit,
            "active_task_count": self.active_task_count,
            "heartbeat_interval_seconds": self.heartbeat_interval_seconds,
            "missed_heartbeats_threshold": self.missed_heartbeats_threshold,
            "last_heartbeat_at": self.last_heartbeat_at.isoformat() if self.last_heartbeat_at else None,
            "draining_since": self.draining_since.isoformat() if self.draining_since else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class WorkerLeaseRecord:
    """Represents an active or historical distributed lease on a resource."""
    lease_id: str
    resource_type: str
    resource_id: str
    worker_id: str
    incarnation_token: str
    fencing_token: int
    lease_state: LeaseState = LeaseState.ACTIVE
    acquired_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    renewed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self, now: Optional[datetime] = None) -> bool:
        t = now or datetime.now(timezone.utc)
        return self.expires_at <= t

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "worker_id": self.worker_id,
            "incarnation_token": self.incarnation_token,
            "fencing_token": self.fencing_token,
            "lease_state": self.lease_state.value if isinstance(self.lease_state, LeaseState) else str(self.lease_state),
            "acquired_at": self.acquired_at.isoformat() if self.acquired_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "renewed_at": self.renewed_at.isoformat() if self.renewed_at else None,
            "metadata": self.metadata,
        }


@dataclass
class ExecutionAttemptRecord:
    """Represents an execution attempt by a specific worker under a fencing token."""
    attempt_id: str
    resource_type: str
    resource_id: str
    tenant_id: str
    worker_id: str
    incarnation_token: str
    fencing_token: int
    attempt_number: int = 1
    status: AttemptStatus = AttemptStatus.RUNNING
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None
    error_detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "tenant_id": self.tenant_id,
            "worker_id": self.worker_id,
            "incarnation_token": self.incarnation_token,
            "fencing_token": self.fencing_token,
            "attempt_number": self.attempt_number,
            "status": self.status.value if isinstance(self.status, AttemptStatus) else str(self.status),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error_detail": self.error_detail,
        }


@dataclass
class TenantWorkerLimitRecord:
    """Represents a tenant\'s concurrency quota and live capacity utilization."""
    tenant_id: str
    max_active_tasks: int = 10
    guaranteed_slots: int = 2
    burst_capacity: int = 20
    active_task_count: int = 0
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def utilization_ratio(self) -> float:
        return self.active_task_count / max(1, self.max_active_tasks)

    @property
    def has_capacity(self) -> bool:
        return self.active_task_count < self.max_active_tasks

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "max_active_tasks": self.max_active_tasks,
            "guaranteed_slots": self.guaranteed_slots,
            "burst_capacity": self.burst_capacity,
            "active_task_count": self.active_task_count,
            "utilization_ratio": round(self.utilization_ratio, 4),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class ClaimedTask:
    """Represents a task claimed atomically by a worker with active lease & fencing token."""
    task_id: str
    user_id: str
    lease_id: str
    fencing_token: int
    incarnation_token: str
    attempt_id: str
    attempt_number: int
    task_data: Dict[str, Any] = field(default_factory=dict)


# --- Fleet Exceptions ---

class FleetError(Exception):
    """Base exception for all worker fleet coordination errors."""
    pass


class WorkerRegistrationError(FleetError):
    """Raised when worker registration fails or violates identity constraints."""
    pass


class WorkerNotHealthyError(FleetError):
    """Raised when an operation requires a healthy worker but current status is not healthy."""
    pass


class WorkerDrainingError(FleetError):
    """Raised when a task claim is attempted on a draining worker."""
    pass


class StaleWorkerError(FleetError):
    """Raised when a worker\'s incarnation token is superseded or worker has expired."""
    pass


class FencingTokenMismatchError(FleetError):
    """Raised when a state mutation is rejected due to a stale or invalid fencing token (zombie defense)."""
    pass


class LeaseExpiredError(FleetError):
    """Raised when an operation fails because the resource lease has expired."""
    pass


class LeaseAlreadyHeldError(FleetError):
    """Raised when attempting to acquire a lease that is already actively held by another worker."""
    pass


class TenantLimitExceededError(FleetError):
    """Raised when a task claim or admission exceeds the tenant\'s maximum active task quota."""
    pass


class ResourceLockedError(FleetError):
    """Raised when a resource cannot be locked or claimed due to concurrent contention."""
    pass
