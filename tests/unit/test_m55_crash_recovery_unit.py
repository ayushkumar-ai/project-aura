"""M55 — Fleet Crash Recovery & Orphan Lease Sweeper Unit Tests.

Tests dead worker reaping, orphan lease expiration, task retry increments,
attempt status transitions (RECOVERED), and tenant capacity reconciliation.
"""

from datetime import datetime, timedelta, timezone
import pytest

from core.fleet.recovery import FleetRecoveryService
from core.fleet.types import (
    AttemptStatus,
    ExecutionAttemptRecord,
    LeaseState,
    TenantWorkerLimitRecord,
    WorkerRecord,
    WorkerStatus,
)
from core.repositories.in_memory_fleet import InMemoryFleetRepository
from core.repositories.in_memory import InMemoryTaskRepository


@pytest.fixture
def test_setup():
    task_repo = InMemoryTaskRepository()
    fleet_repo = InMemoryFleetRepository(task_repo=task_repo)
    recovery_svc = FleetRecoveryService(fleet_repo=fleet_repo, worker_expiry_seconds=1.0, lease_expiry_seconds=2.0)
    return task_repo, fleet_repo, recovery_svc


def test_reap_expired_dead_workers(test_setup):
    """M55-F13: Workers that miss heartbeats beyond threshold are transitioned to EXPIRED."""
    task_repo, fleet_repo, recovery_svc = test_setup

    # Register worker with old heartbeat (5 seconds ago)
    old_time = datetime.now(timezone.utc) - timedelta(seconds=5)
    w = WorkerRecord(
        worker_id="wkr_dead",
        instance_id="inst_dead",
        hostname="node-crashed",
        process_id=9999,
        incarnation_token="inc_dead_token",
        status=WorkerStatus.HEALTHY,
        last_heartbeat_at=old_time,
    )
    fleet_repo.register_worker(w)
    # Manually backdate last_heartbeat_at
    fleet_repo.get_worker("wkr_dead").last_heartbeat_at = old_time

    reaped = fleet_repo.reap_expired_workers(threshold_seconds=1.0)
    assert "wkr_dead" in reaped

    worker_after = fleet_repo.get_worker("wkr_dead")
    assert worker_after.status == WorkerStatus.EXPIRED


def test_sweep_orphaned_leases_and_requeue_task(test_setup):
    """M55-F14, M55-F15: Orphaned leases on dead workers are fenced and tasks requeued."""
    task_repo, fleet_repo, recovery_svc = test_setup

    # 1. Create task in task_repo
    task_res = task_repo.create_task(user_id="user_123", title="Stale Task", goal="Process data")
    task_id = task_res["id"]

    # 2. Register initially healthy worker
    w = WorkerRecord(
        worker_id="wkr_crashed",
        instance_id="i1",
        hostname="h1",
        process_id=1,
        incarnation_token="inc_crashed",
        status=WorkerStatus.HEALTHY,
    )
    fleet_repo.register_worker(w)

    # 3. Acquire lease while healthy
    lease = fleet_repo.acquire_lease("task", task_id, "wkr_crashed", "inc_crashed", 30.0, metadata={"tenant_id": "user_123"})
    assert lease is not None

    # 4. Set task to running and record attempt
    task_repo._tasks[task_id]["status"] = "running"
    attempt = ExecutionAttemptRecord(
        attempt_id="att_crashed_1",
        resource_type="task",
        resource_id=task_id,
        tenant_id="user_123",
        worker_id="wkr_crashed",
        incarnation_token="inc_crashed",
        fencing_token=lease.fencing_token,
        status=AttemptStatus.RUNNING,
    )
    fleet_repo.record_attempt(attempt)
    fleet_repo.adjust_tenant_active_count("user_123", 1)

    # 5. Worker ungracefully crashes (transitioned to EXPIRED)
    fleet_repo.update_worker_status("wkr_crashed", "inc_crashed", WorkerStatus.EXPIRED)

    # 6. Run recovery sweep
    report = recovery_svc.sweep_once()
    assert task_id in report["recovered_leases"]

    # 7. Verify lease expired
    lease_after = fleet_repo.get_lease("task", task_id)
    assert lease_after.lease_state == LeaseState.EXPIRED

    # 8. Verify attempt marked RECOVERED
    attempts = fleet_repo.get_attempts_for_resource("task", task_id)
    assert len(attempts) == 1
    assert attempts[0].status == AttemptStatus.RECOVERED

    # 9. Verify task requeued to pending
    t_after = task_repo.get_task(task_id, "user_123")
    assert t_after["status"] == "pending"
    assert t_after.get("retry_count", 0) == 1


def test_tenant_capacity_reconciliation(test_setup):
    """M55-F34: Reconciliation repairs drift between tenant active counts and live leases."""
    task_repo, fleet_repo, recovery_svc = test_setup

    # Artificially set drifted count
    fleet_repo.set_tenant_limits(TenantWorkerLimitRecord(
        tenant_id="user_drifted",
        max_active_tasks=10,
        active_task_count=5,  # drifted: 5 in counter, but 0 actual active leases
    ))

    reconciled = fleet_repo.reconcile_tenant_capacities()
    assert "user_drifted" in reconciled
    assert reconciled["user_drifted"] == 0

    limits_after = fleet_repo.get_tenant_limits("user_drifted")
    assert limits_after.active_task_count == 0
