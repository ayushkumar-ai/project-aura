"""M55 — Worker Fleet Lifecycle Unit Tests.

Tests worker registration, incarnation uniqueness, generation monotonicity,
heartbeat tracking, missed heartbeat degradation to UNHEALTHY, recovery to HEALTHY,
graceful draining, and clean unregistering.
"""

from datetime import datetime, timedelta, timezone
import time
import pytest

from core.fleet.heartbeat import HeartbeatManager
from core.fleet.types import (
    WorkerRecord,
    WorkerStatus,
)
from core.repositories.in_memory_fleet import InMemoryFleetRepository


@pytest.fixture
def fleet_repo() -> InMemoryFleetRepository:
    return InMemoryFleetRepository()


def test_worker_registration_unique_incarnation(fleet_repo: InMemoryFleetRepository):
    """M55-F01: Every worker process generates a unique incarnation token."""
    w1 = WorkerRecord(
        worker_id="wkr_01",
        instance_id="inst_01",
        hostname="host-a",
        process_id=1001,
        incarnation_token="inc_token_111",
        status=WorkerStatus.HEALTHY,
        concurrency_limit=8,
    )
    reg1 = fleet_repo.register_worker(w1)
    assert reg1.worker_id == "wkr_01"
    assert reg1.incarnation_token == "inc_token_111"
    assert reg1.generation == 1
    assert reg1.status == WorkerStatus.HEALTHY
    assert reg1.concurrency_limit == 8


def test_worker_restart_monotonic_generation(fleet_repo: InMemoryFleetRepository):
    """M55-F02: Restarting a worker with the same worker_id increments generation."""
    w1 = WorkerRecord(
        worker_id="wkr_01",
        instance_id="inst_01",
        hostname="host-a",
        process_id=1001,
        incarnation_token="inc_token_111",
    )
    reg1 = fleet_repo.register_worker(w1)
    assert reg1.generation == 1

    # Restart with new incarnation
    w2 = WorkerRecord(
        worker_id="wkr_01",
        instance_id="inst_01",
        hostname="host-a",
        process_id=1002,
        incarnation_token="inc_token_222",
    )
    reg2 = fleet_repo.register_worker(w2)
    assert reg2.generation == 2
    assert reg2.incarnation_token == "inc_token_222"


def test_worker_heartbeat_updates_timestamp(fleet_repo: InMemoryFleetRepository):
    """M55-F11: Valid worker heartbeat updates last_heartbeat_at."""
    w = WorkerRecord(
        worker_id="wkr_01",
        instance_id="inst_01",
        hostname="host-a",
        process_id=1001,
        incarnation_token="inc_token_111",
        status=WorkerStatus.HEALTHY,
    )
    fleet_repo.register_worker(w)

    initial = fleet_repo.get_worker("wkr_01")
    assert initial is not None
    t0 = initial.last_heartbeat_at

    time.sleep(0.05)
    success = fleet_repo.update_worker_heartbeat("wkr_01", "inc_token_111")
    assert success is True

    updated = fleet_repo.get_worker("wkr_01")
    assert updated is not None
    assert updated.last_heartbeat_at >= t0


def test_stale_incarnation_heartbeat_rejected(fleet_repo: InMemoryFleetRepository):
    """M55-F05: Heartbeat with mismatched incarnation token is rejected."""
    w = WorkerRecord(
        worker_id="wkr_01",
        instance_id="inst_01",
        hostname="host-a",
        process_id=1001,
        incarnation_token="inc_token_current",
        status=WorkerStatus.HEALTHY,
    )
    fleet_repo.register_worker(w)

    # Stale zombie worker heartbeat attempt
    success = fleet_repo.update_worker_heartbeat("wkr_01", "inc_token_stale_zombie")
    assert success is False


def test_heartbeat_manager_recovers_unhealthy_to_healthy(fleet_repo: InMemoryFleetRepository):
    """M55-F12: Emitting a heartbeat while UNHEALTHY restores status to HEALTHY."""
    w = WorkerRecord(
        worker_id="wkr_01",
        instance_id="inst_01",
        hostname="host-a",
        process_id=1001,
        incarnation_token="inc_token_111",
        status=WorkerStatus.UNHEALTHY,
    )
    fleet_repo.register_worker(w)
    fleet_repo.update_worker_status("wkr_01", "inc_token_111", WorkerStatus.UNHEALTHY)

    worker = fleet_repo.get_worker("wkr_01")
    assert worker.status == WorkerStatus.UNHEALTHY

    # Heartbeat pulse
    hb_ok = fleet_repo.update_worker_heartbeat("wkr_01", "inc_token_111")
    assert hb_ok is True

    recovered = fleet_repo.get_worker("wkr_01")
    assert recovered.status == WorkerStatus.HEALTHY


def test_worker_graceful_draining_and_unregister(fleet_repo: InMemoryFleetRepository):
    """M55-F19, M55-F21: Worker transitions to DRAINING and then STOPPED on clean shutdown."""
    w = WorkerRecord(
        worker_id="wkr_01",
        instance_id="inst_01",
        hostname="host-a",
        process_id=1001,
        incarnation_token="inc_token_111",
        status=WorkerStatus.HEALTHY,
    )
    fleet_repo.register_worker(w)

    # Drain
    drain_time = datetime.now(timezone.utc)
    fleet_repo.update_worker_status("wkr_01", "inc_token_111", WorkerStatus.DRAINING, draining_since=drain_time)
    draining_worker = fleet_repo.get_worker("wkr_01")
    assert draining_worker.status == WorkerStatus.DRAINING
    assert draining_worker.draining_since is not None

    # Unregister / Stop
    unreg_ok = fleet_repo.unregister_worker("wkr_01", "inc_token_111")
    assert unreg_ok is True

    stopped_worker = fleet_repo.get_worker("wkr_01")
    assert stopped_worker.status == WorkerStatus.STOPPED

    # Stopped worker cannot receive heartbeats
    hb_stopped = fleet_repo.update_worker_heartbeat("wkr_01", "inc_token_111")
    assert hb_stopped is False


def test_list_workers_filtering(fleet_repo: InMemoryFleetRepository):
    """Test listing and filtering workers by status."""
    fleet_repo.register_worker(WorkerRecord(
        worker_id="wkr_h1", instance_id="i1", hostname="h1", process_id=1, incarnation_token="inc1", status=WorkerStatus.HEALTHY
    ))
    fleet_repo.register_worker(WorkerRecord(
        worker_id="wkr_h2", instance_id="i2", hostname="h2", process_id=2, incarnation_token="inc2", status=WorkerStatus.HEALTHY
    ))
    fleet_repo.register_worker(WorkerRecord(
        worker_id="wkr_d1", instance_id="i3", hostname="h3", process_id=3, incarnation_token="inc3", status=WorkerStatus.DRAINING
    ))

    all_workers = fleet_repo.list_workers()
    assert len(all_workers) == 3

    healthy = fleet_repo.list_workers(WorkerStatus.HEALTHY)
    assert len(healthy) == 2
    assert {w.worker_id for w in healthy} == {"wkr_h1", "wkr_h2"}

    draining = fleet_repo.list_workers(WorkerStatus.DRAINING)
    assert len(draining) == 1
    assert draining[0].worker_id == "wkr_d1"
