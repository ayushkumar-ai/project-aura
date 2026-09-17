"""M55 — Distributed Lease Acquisition & Monotonic Fencing Unit Tests.

Tests monotonic fencing token increments, split-brain/zombie write defense,
lease renewals, batch renewals, double-acquisition rejection, and attempt immutability.
"""

from datetime import datetime, timedelta, timezone
import pytest

from core.fleet.types import (
    AttemptStatus,
    ExecutionAttemptRecord,
    FencingTokenMismatchError,
    LeaseState,
    WorkerRecord,
    WorkerStatus,
)
from core.repositories.in_memory_fleet import InMemoryFleetRepository


@pytest.fixture
def fleet_repo() -> InMemoryFleetRepository:
    repo = InMemoryFleetRepository()
    # Register test worker
    repo.register_worker(WorkerRecord(
        worker_id="wkr_01",
        instance_id="inst_01",
        hostname="node-1",
        process_id=1001,
        incarnation_token="inc_token_wkr1",
        status=WorkerStatus.HEALTHY,
        concurrency_limit=4,
    ))
    # Register secondary worker
    repo.register_worker(WorkerRecord(
        worker_id="wkr_02",
        instance_id="inst_02",
        hostname="node-2",
        process_id=1002,
        incarnation_token="inc_token_wkr2",
        status=WorkerStatus.HEALTHY,
        concurrency_limit=4,
    ))
    return repo


def test_lease_acquisition_initial_fencing_token(fleet_repo: InMemoryFleetRepository):
    """M55-F04: First lease acquisition initializes fencing token to 1."""
    lease = fleet_repo.acquire_lease(
        resource_type="task",
        resource_id="task_100",
        worker_id="wkr_01",
        incarnation_token="inc_token_wkr1",
        duration_seconds=30.0,
    )
    assert lease is not None
    assert lease.resource_type == "task"
    assert lease.resource_id == "task_100"
    assert lease.worker_id == "wkr_01"
    assert lease.fencing_token == 1
    assert lease.lease_state == LeaseState.ACTIVE
    assert not lease.is_expired()


def test_monotonic_fencing_token_on_lease_reassignment(fleet_repo: InMemoryFleetRepository):
    """M55-F04: Releasing and re-acquiring a lease monotonically increments fencing token."""
    # Worker 1 acquires lease
    l1 = fleet_repo.acquire_lease("task", "task_100", "wkr_01", "inc_token_wkr1", 30.0)
    assert l1 is not None
    assert l1.fencing_token == 1

    # Worker 1 releases lease
    rel = fleet_repo.release_lease("task", "task_100", "wkr_01", "inc_token_wkr1", l1.fencing_token)
    assert rel is True

    # Worker 2 acquires lease on same resource
    l2 = fleet_repo.acquire_lease("task", "task_100", "wkr_02", "inc_token_wkr2", 30.0)
    assert l2 is not None
    assert l2.worker_id == "wkr_02"
    assert l2.fencing_token == 2  # Monotonically incremented!


def test_double_acquisition_rejected_while_active(fleet_repo: InMemoryFleetRepository):
    """M55-F03: Two active workers cannot simultaneously hold a lease on the same resource."""
    l1 = fleet_repo.acquire_lease("task", "task_200", "wkr_01", "inc_token_wkr1", 30.0)
    assert l1 is not None

    # Worker 2 tries to steal active lease
    l2 = fleet_repo.acquire_lease("task", "task_200", "wkr_02", "inc_token_wkr2", 30.0)
    assert l2 is None  # Rejected!


def test_zombie_worker_write_fence_verification(fleet_repo: InMemoryFleetRepository):
    """M55-F05: Fencing verification rejects mutations from a superseded zombie worker."""
    # 1. Worker 1 acquires lease (fencing_token = 1)
    l1 = fleet_repo.acquire_lease("task", "task_300", "wkr_01", "inc_token_wkr1", 30.0)
    assert l1.fencing_token == 1

    # 2. Worker 1 suffers long GC pause, lease is expired/released
    fleet_repo.release_lease("task", "task_300", "wkr_01", "inc_token_wkr1", 1)

    # 3. Worker 2 claims task (fencing_token = 2)
    l2 = fleet_repo.acquire_lease("task", "task_300", "wkr_02", "inc_token_wkr2", 30.0)
    assert l2.fencing_token == 2

    # 4. Worker 1 unfreezes and attempts to verify fencing with stale token = 1
    w1_valid = fleet_repo.verify_fencing("task", "task_300", "wkr_01", "inc_token_wkr1", 1)
    assert w1_valid is False  # Zombie write blocked!

    # 5. Worker 2 verifies fencing with current token = 2
    w2_valid = fleet_repo.verify_fencing("task", "task_300", "wkr_02", "inc_token_wkr2", 2)
    assert w2_valid is True


def test_lease_renewal_extends_expiry(fleet_repo: InMemoryFleetRepository):
    """M55-F31: Lease renewal atomically updates expires_at."""
    l1 = fleet_repo.acquire_lease("task", "task_400", "wkr_01", "inc_token_wkr1", 10.0)
    assert l1 is not None
    orig_expiry = l1.expires_at

    # Renew with valid credentials
    renew_ok = fleet_repo.renew_lease("task", "task_400", "wkr_01", "inc_token_wkr1", l1.fencing_token, 60.0)
    assert renew_ok is True

    l_after = fleet_repo.get_lease("task", "task_400")
    assert l_after.expires_at > orig_expiry
    assert l_after.lease_state == LeaseState.RENEWED

    # Renew with invalid fencing token fails
    renew_fail = fleet_repo.renew_lease("task", "task_400", "wkr_01", "inc_token_wkr1", 999, 60.0)
    assert renew_fail is False


def test_batch_renew_worker_leases(fleet_repo: InMemoryFleetRepository):
    """Heartbeat manager batch-renews all active leases held by the worker."""
    fleet_repo.acquire_lease("task", "t1", "wkr_01", "inc_token_wkr1", 10.0)
    fleet_repo.acquire_lease("task", "t2", "wkr_01", "inc_token_wkr1", 10.0)
    fleet_repo.acquire_lease("task", "t3", "wkr_02", "inc_token_wkr2", 10.0)

    renewed_count = fleet_repo.batch_renew_worker_leases("wkr_01", "inc_token_wkr1", 60.0)
    assert renewed_count == 2


def test_execution_attempt_lifecycle_and_immutability(fleet_repo: InMemoryFleetRepository):
    """M55-F23: Terminal execution attempts are immutable."""
    attempt = ExecutionAttemptRecord(
        attempt_id="att_001",
        resource_type="task",
        resource_id="task_500",
        tenant_id="user_abc",
        worker_id="wkr_01",
        incarnation_token="inc_token_wkr1",
        fencing_token=1,
        attempt_number=1,
        status=AttemptStatus.RUNNING,
    )
    fleet_repo.record_attempt(attempt)

    # Transition to COMPLETED
    done = fleet_repo.update_attempt_status("att_001", AttemptStatus.COMPLETED)
    assert done is True

    # Attempting to mutate terminal attempt returns False
    mutate_again = fleet_repo.update_attempt_status("att_001", AttemptStatus.FAILED, error_detail="Late error")
    assert mutate_again is False

    attempts = fleet_repo.get_attempts_for_resource("task", "task_500")
    assert len(attempts) == 1
    assert attempts[0].status == AttemptStatus.COMPLETED
