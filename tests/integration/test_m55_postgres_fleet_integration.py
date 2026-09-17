"""M55 — PostgreSQL Fleet Coordination Integration Tests.

Tests live PostgreSQL 16 persistence for worker fleet registration,
distributed row-level locking (SELECT ... FOR UPDATE SKIP LOCKED),
monotonic fencing token increments, split-brain rejection, and crash recovery.
"""

from datetime import datetime, timedelta, timezone
import os
import threading
import time
import uuid
import pytest

from core.database import DatabaseConnectionPool, MigrationRunner
from core.fleet.types import (
    AttemptStatus,
    ExecutionAttemptRecord,
    LeaseState,
    TenantWorkerLimitRecord,
    WorkerRecord,
    WorkerStatus,
)
from core.repositories.postgres_fleet import PostgresFleetRepository
from core.repositories.postgres_task import PostgresTaskRepository
from core.repositories.postgres import PostgresUserRepository
from core.identity import UserIdentity, UserRole

DB_URL = os.getenv("AURA_DATABASE_URL", "postgresql://aura_user:aura_password@127.0.0.1:5432/aura_db")


def _is_postgres_available() -> bool:
    try:
        pool = DatabaseConnectionPool(DB_URL, min_size=1, max_size=1, is_production=False, timeout=2.0)
        if not pool.is_active:
            return False
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                return bool(cur.fetchone())
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _is_postgres_available(), reason="PostgreSQL 16 live instance unavailable")


@pytest.fixture(scope="module")
def db_pool():
    pool = DatabaseConnectionPool(
        connection_url=DB_URL,
        min_size=2,
        max_size=10,
        is_production=True,
    )
    if not pool.is_active:
        pytest.skip("PostgreSQL database is not available for integration testing")
    runner = MigrationRunner(pool)
    runner.run_migrations()
    yield pool
    pool.close()


@pytest.fixture(autouse=True)
def clean_fleet_tables(db_pool):
    """Clean fleet tables before each test to guarantee isolation."""
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("DELETE FROM worker_leases;")
                cur.execute("DELETE FROM execution_attempts;")
                cur.execute("DELETE FROM workers;")
                cur.execute("DELETE FROM tenant_worker_limits;")
                cur.execute("DELETE FROM tasks WHERE status = 'pending' OR user_id LIKE 'usr_%';")
    yield
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("DELETE FROM worker_leases;")
                cur.execute("DELETE FROM execution_attempts;")
                cur.execute("DELETE FROM workers;")
                cur.execute("DELETE FROM tenant_worker_limits;")
                cur.execute("DELETE FROM tasks WHERE status = 'pending' OR user_id LIKE 'usr_%';")


@pytest.fixture
def repos(db_pool):
    fleet_repo = PostgresFleetRepository(db_pool)
    task_repo = PostgresTaskRepository(db_pool)
    user_repo = PostgresUserRepository(db_pool)
    return fleet_repo, task_repo, user_repo


def test_postgres_worker_registration_and_generation(repos):
    """M55-F01, M55-F02: Worker registration and generation increment on PostgreSQL."""
    fleet_repo, _, _ = repos
    w_id = f"wkr_pg_{uuid.uuid4().hex[:8]}"

    w1 = WorkerRecord(
        worker_id=w_id,
        instance_id="inst_pg_1",
        hostname="pg-host-1",
        process_id=1100,
        incarnation_token=f"inc_{uuid.uuid4().hex}",
        status=WorkerStatus.HEALTHY,
        concurrency_limit=6,
    )
    reg1 = fleet_repo.register_worker(w1)
    assert reg1.worker_id == w_id
    assert reg1.generation == 1
    assert reg1.concurrency_limit == 6

    # Re-register with new incarnation (simulating restart)
    w2 = WorkerRecord(
        worker_id=w_id,
        instance_id="inst_pg_1",
        hostname="pg-host-1",
        process_id=1101,
        incarnation_token=f"inc_{uuid.uuid4().hex}",
        status=WorkerStatus.HEALTHY,
        concurrency_limit=8,
    )
    reg2 = fleet_repo.register_worker(w2)
    assert reg2.worker_id == w_id
    assert reg2.generation == 2
    assert reg2.concurrency_limit == 8


def test_postgres_monotonic_fencing_token_progression(repos):
    """M55-F04: Fencing token monotonically increments on PostgreSQL across reassignment."""
    fleet_repo, _, _ = repos
    w1_id = f"wkr_f1_{uuid.uuid4().hex[:8]}"
    w2_id = f"wkr_f2_{uuid.uuid4().hex[:8]}"
    inc1 = f"inc_{uuid.uuid4().hex}"
    inc2 = f"inc_{uuid.uuid4().hex}"
    res_id = f"res_{uuid.uuid4().hex[:8]}"

    fleet_repo.register_worker(WorkerRecord(worker_id=w1_id, instance_id="i1", hostname="h1", process_id=1, incarnation_token=inc1, status=WorkerStatus.HEALTHY))
    fleet_repo.register_worker(WorkerRecord(worker_id=w2_id, instance_id="i2", hostname="h2", process_id=2, incarnation_token=inc2, status=WorkerStatus.HEALTHY))

    # 1. First acquisition -> fencing_token = 1
    l1 = fleet_repo.acquire_lease("task", res_id, w1_id, inc1, 30.0)
    assert l1 is not None
    assert l1.fencing_token == 1

    # 2. Release lease
    rel = fleet_repo.release_lease("task", res_id, w1_id, inc1, 1)
    assert rel is True

    # 3. Second acquisition -> fencing_token = 2
    l2 = fleet_repo.acquire_lease("task", res_id, w2_id, inc2, 30.0)
    assert l2 is not None
    assert l2.fencing_token == 2

    # 4. Monotonic check & Fencing verification
    assert fleet_repo.verify_fencing("task", res_id, w1_id, inc1, 1) is False
    assert fleet_repo.verify_fencing("task", res_id, w2_id, inc2, 2) is True


def test_postgres_concurrent_lease_race(repos):
    """M55-F03, M55-F06: Concurrent lease acquisition race produces exactly one winner."""
    fleet_repo, _, _ = repos
    res_id = f"res_race_{uuid.uuid4().hex[:8]}"
    num_workers = 5
    workers = []

    for i in range(num_workers):
        wid = f"wkr_race_{i}_{uuid.uuid4().hex[:6]}"
        inc = f"inc_{uuid.uuid4().hex}"
        fleet_repo.register_worker(WorkerRecord(worker_id=wid, instance_id=f"i{i}", hostname="h", process_id=i, incarnation_token=inc, status=WorkerStatus.HEALTHY))
        workers.append((wid, inc))

    acquired_leases = []
    errors = []

    def try_acquire(wid, inc):
        try:
            lease = fleet_repo.acquire_lease("task", res_id, wid, inc, 30.0)
            if lease:
                acquired_leases.append((wid, lease))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=try_acquire, args=(wid, inc)) for wid, inc in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    # Exactly one worker wins the lease!
    assert len(acquired_leases) == 1
    winner_wid, winner_lease = acquired_leases[0]
    assert winner_lease.fencing_token == 1


def test_postgres_claim_next_fair_task_with_skip_locked(repos, db_pool):
    """M55-F06, M55-F10: Fair task claiming with FOR UPDATE SKIP LOCKED on PostgreSQL."""
    fleet_repo, task_repo, user_repo = repos

    # 1. Create test user
    uid = f"usr_fair_{uuid.uuid4().hex[:8]}"
    user_repo.save(UserIdentity(user_id=uid, username=f"u_{uuid.uuid4().hex[:12]}", roles=frozenset({UserRole.USER})))

    # 2. Create pending tasks for user
    t1 = task_repo.create_task(user_id=uid, title="Task 1", goal="Goal 1")
    t2 = task_repo.create_task(user_id=uid, title="Task 2", goal="Goal 2")

    # 3. Register worker
    wid = f"wkr_claim_{uuid.uuid4().hex[:8]}"
    inc = f"inc_{uuid.uuid4().hex}"
    fleet_repo.register_worker(WorkerRecord(worker_id=wid, instance_id="i", hostname="h", process_id=1, incarnation_token=inc, status=WorkerStatus.HEALTHY, concurrency_limit=4))

    # 4. Claim task
    claimed = fleet_repo.claim_next_fair_task(wid, inc, 30.0)
    assert claimed is not None
    assert claimed.user_id == uid
    assert claimed.task_id in (t1["id"], t2["id"])
    assert claimed.fencing_token == 1

    # 5. Verify tenant active task count incremented
    limits = fleet_repo.get_tenant_limits(uid)
    assert limits.active_task_count >= 1

    # 6. Verify task status is running in DB
    task_in_db = task_repo.get_task(claimed.task_id, uid)
    assert task_in_db["status"] == "running"


def test_postgres_dead_worker_and_orphan_lease_sweeping(repos, db_pool):
    """M55-F13, M55-F14: PostgreSQL sweeping of dead workers and orphan leases."""
    fleet_repo, task_repo, user_repo = repos

    uid = f"usr_sweep_{uuid.uuid4().hex[:8]}"
    user_repo.save(UserIdentity(user_id=uid, username=f"u_{uuid.uuid4().hex[:12]}", roles=frozenset({UserRole.USER})))

    task = task_repo.create_task(user_id=uid, title="Task To Orphan", goal="Will be abandoned")
    task_id = task["id"]

    wid = f"wkr_to_crash_{uuid.uuid4().hex[:8]}"
    inc = f"inc_{uuid.uuid4().hex}"
    fleet_repo.register_worker(WorkerRecord(worker_id=wid, instance_id="i", hostname="h", process_id=1, incarnation_token=inc, status=WorkerStatus.HEALTHY))

    # Claim task
    claimed = fleet_repo.claim_next_fair_task(wid, inc, 30.0)
    assert claimed is not None
    assert claimed.task_id == task_id

    # Manually backdate heartbeat on PostgreSQL
    with db_pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE workers SET last_heartbeat_at = CURRENT_TIMESTAMP - interval '30 seconds' WHERE worker_id = %s;",
                    (wid,),
                )

    # Reap expired worker
    reaped = fleet_repo.reap_expired_workers(threshold_seconds=5.0)
    assert wid in reaped

    # Sweep orphan lease
    recovered = fleet_repo.sweep_orphaned_leases(lease_expiry_seconds=1.0)
    assert task_id in recovered

    # Verify task requeued to pending in PostgreSQL
    t_after = task_repo.get_task(task_id, uid)
    assert t_after["status"] == "pending"
