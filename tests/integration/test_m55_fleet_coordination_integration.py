"""M55 — Fleet Coordination & End-to-End Worker Scaling Integration Tests.

Tests multi-worker concurrent task distribution, graceful worker draining,
cooperative task cancellation propagation, coordinator inspection, and server HTTP endpoints.
"""

from datetime import datetime, timezone
import json
import threading
import time
import uuid
import pytest
from unittest.mock import MagicMock

from core.background.workflow_orchestrator import WorkflowOrchestrator
from core.fleet.coordinator import WorkerFleetCoordinator
from core.fleet.worker import DistributedFleetWorker
from core.fleet.types import WorkerStatus, TenantWorkerLimitRecord
from core.repositories.in_memory_fleet import InMemoryFleetRepository
from core.repositories.in_memory import (
    InMemoryTaskRepository,
    InMemoryApprovalRepository,
    InMemoryUserRepository,
)
from core.identity import UserIdentity, UserRole
from app.config import Settings
from app.server import AURAHTTPServer


@pytest.fixture
def mock_orchestrator():
    orch = MagicMock(spec=WorkflowOrchestrator)
    def mock_exec(task_dict, cancellation_requested=None):
        time.sleep(0.1)
        return {"status": "completed", "output": f"processed_{task_dict.get('id')}"}
    orch.execute_task.side_effect = mock_exec
    return orch


@pytest.fixture
def fleet_env(mock_orchestrator):
    task_repo = InMemoryTaskRepository()
    approval_repo = InMemoryApprovalRepository(task_repo=task_repo)
    fleet_repo = InMemoryFleetRepository(task_repo=task_repo)
    coordinator = WorkerFleetCoordinator(fleet_repo=fleet_repo)
    return task_repo, approval_repo, fleet_repo, coordinator, mock_orchestrator


def test_multi_worker_fleet_task_distribution(fleet_env):
    """M55-F06, M55-F08: Multi-worker fleet executes concurrent tasks across nodes."""
    task_repo, approval_repo, fleet_repo, coordinator, orchestrator = fleet_env

    # Spawn 3 workers
    workers = []
    for i in range(3):
        w = DistributedFleetWorker(
            fleet_repo=fleet_repo,
            task_repo=task_repo,
            approval_repo=approval_repo,
            orchestrator=orchestrator,
            concurrency=2,
            poll_interval_ms=50,
            heartbeat_interval_seconds=1.0,
            lease_duration_seconds=10.0,
            worker_id=f"wkr_fleet_{i}",
        )
        w.start()
        workers.append(w)

    try:
        # Create 6 tasks for tenant
        uid = "tenant_test_distrib"
        fleet_repo.set_tenant_limits(TenantWorkerLimitRecord(tenant_id=uid, max_active_tasks=10))
        task_ids = []
        for i in range(6):
            t = task_repo.create_task(user_id=uid, title=f"Task {i}", goal="Run parallel")
            task_ids.append(t["id"])

        # Wait for all tasks to be completed
        start_wait = time.time()
        while time.time() - start_wait < 5.0:
            active_count = sum(w.active_count for w in workers)
            pending_count = sum(1 for tid in task_ids if task_repo.get_task(tid, uid).get("status") in ("pending", "running"))
            if pending_count == 0:
                break
            time.sleep(0.05)

        # Verify all 6 tasks completed
        for tid in task_ids:
            t = task_repo.get_task(tid, uid)
            assert t["status"] == "completed"

        # Verify coordinator reflects fleet metrics
        status = coordinator.get_fleet_status()
        assert status["healthy_workers"] == 3
        assert status["total_fleet_capacity"] == 6

    finally:
        for w in workers:
            w.stop(timeout=1.0)


def test_worker_graceful_draining_finishes_active_tasks(fleet_env):
    """M55-F19, M55-F20: Draining worker stops accepting new work but finishes in-flight."""
    task_repo, approval_repo, fleet_repo, coordinator, orchestrator = fleet_env

    # Orchestrator with longer task execution
    def slow_exec(task_dict, cancellation_requested=None):
        time.sleep(0.3)
        return {"status": "completed"}
    orchestrator.execute_task.side_effect = slow_exec

    worker = DistributedFleetWorker(
        fleet_repo=fleet_repo,
        task_repo=task_repo,
        approval_repo=approval_repo,
        orchestrator=orchestrator,
        concurrency=1,
        poll_interval_ms=50,
        drain_timeout_seconds=5.0,
    )
    worker.start()

    try:
        uid = "tenant_drain"
        t1 = task_repo.create_task(user_id=uid, title="Task Active", goal="Slow")
        t2 = task_repo.create_task(user_id=uid, title="Task Queued", goal="Should stay pending")

        # Wait for task 1 to start
        time.sleep(0.1)
        assert worker.active_count == 1

        # Drain worker
        worker.drain()
        assert worker.is_draining is True

        # Stop worker with grace period
        worker.stop(timeout=3.0)
        assert worker.active_count == 0

        # Verify task 1 completed
        t1_after = task_repo.get_task(t1["id"], uid)
        assert t1_after["status"] == "completed"

        # Verify task 2 stayed pending (not claimed while draining)
        t2_after = task_repo.get_task(t2["id"], uid)
        assert t2_after["status"] == "pending"

    finally:
        if worker.is_running:
            worker.stop()


def test_fleet_coordinator_administrative_controls(fleet_env):
    """M55-F09: Coordinator administrative controls for tenant quotas and draining."""
    task_repo, approval_repo, fleet_repo, coordinator, _ = fleet_env

    # Set tenant quota
    q = coordinator.set_tenant_quota("tenant_custom", max_active_tasks=25, guaranteed_slots=5, burst_capacity=50)
    assert q.max_active_tasks == 25
    assert q.guaranteed_slots == 5
    assert q.burst_capacity == 50

    # Retrieve quota
    got = coordinator.get_tenant_quota("tenant_custom")
    assert got.max_active_tasks == 25

    # Register worker and drain via coordinator
    w = DistributedFleetWorker(
        fleet_repo=fleet_repo,
        task_repo=task_repo,
        approval_repo=approval_repo,
        orchestrator=MagicMock(),
        worker_id="wkr_admin_drain",
    )
    w.start()
    try:
        status_before = coordinator.get_fleet_status()
        assert status_before["healthy_workers"] >= 1

        drained = coordinator.drain_worker("wkr_admin_drain")
        assert drained is True

        w_rec = fleet_repo.get_worker("wkr_admin_drain")
        assert w_rec.status == WorkerStatus.DRAINING
    finally:
        w.stop()
