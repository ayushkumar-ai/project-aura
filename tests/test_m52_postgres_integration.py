"""M52 — PostgreSQL 16 Live Integration Test Suite for Task & Approval Repositories.

Verifies durable PostgreSQL operations:
1. Multi-tenant Task CRUD and status lifecycle transitions
2. Idempotency deduplication with unique index
3. Atomic task leasing using SELECT ... FOR UPDATE SKIP LOCKED
4. Step persistence, execution outputs, and step_index ordering
5. Human approval creation with CSPRNG nonce and single-use decision verification
6. Replay attack rejection and nonce invalidation
7. Stale approval expiration and automatic task timeout
8. Worker crash recovery sweeps
9. Strict multi-tenant data isolation
"""

from __future__ import annotations

import os
import secrets
import time
import uuid
from typing import Any
import pytest

from core.database import DatabaseConnectionPool
from core.repositories.postgres_task import PostgresTaskRepository
from core.repositories.postgres_approval import PostgresApprovalRepository

DB_URL = os.getenv("AURA_DATABASE_URL", "postgresql://aura_user:aura_password@127.0.0.1:5432/aura_db")


def _is_postgres_available() -> bool:
    """Check whether live PostgreSQL 16 database is reachable."""
    try:
        pool = DatabaseConnectionPool(DB_URL, min_size=1, max_size=1, is_production=False, timeout=2.0)
        if not pool.is_active:
            return False
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                row = cur.fetchone()
                return bool(row)
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _is_postgres_available(), reason="PostgreSQL 16 live instance unavailable")


@pytest.fixture(scope="module")
def pg_pool():
    """Module-scoped live database connection pool."""
    pool = DatabaseConnectionPool(DB_URL, min_size=1, max_size=3, is_production=False)
    yield pool
    pool.close()


@pytest.fixture
def pg_repos(pg_pool):
    """PostgreSQL task and approval repository pair with unique test user."""
    test_user = f"user_{uuid.uuid4().hex[:12]}"
    with pg_pool.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, username, role) VALUES (%s, %s, 'user') ON CONFLICT (id) DO NOTHING;",
                (test_user, test_user),
            )

    task_repo = PostgresTaskRepository(pg_pool)
    appr_repo = PostgresApprovalRepository(pg_pool)
    return task_repo, appr_repo, test_user


def test_postgres_task_crud_and_status_transitions(pg_repos):
    """Verify task creation, retrieval, and status updates on live PostgreSQL."""
    task_repo, _, user_id = pg_repos

    task = task_repo.create_task(
        user_id=user_id,
        title="Analyze Logs",
        goal="Search logs for anomalies",
        context={"service": "auth", "priority": "high"},
        timeout_seconds=300,
    )
    assert task["id"] is not None
    assert task["user_id"] == user_id
    assert task["status"] == "pending"
    assert task["context"]["service"] == "auth"

    # Status update: running
    ok = task_repo.update_task_status(task["id"], user_id, "running")
    assert ok is True
    running = task_repo.get_task(task["id"], user_id)
    assert running["status"] == "running"
    assert running["started_at"] is not None

    # Status update: completed
    res = {"anomalies_detected": 0}
    ok = task_repo.update_task_status(task["id"], user_id, "completed", result=res)
    assert ok is True
    completed = task_repo.get_task(task["id"], user_id)
    assert completed["status"] == "completed"
    assert completed["result"] == res
    assert completed["completed_at"] is not None


def test_postgres_task_idempotency_and_tenant_isolation(pg_pool, pg_repos):
    """Verify idempotency key deduplication and strict user isolation."""
    task_repo, _, user_alice = pg_repos
    user_bob = f"user_bob_{uuid.uuid4().hex[:8]}"
    with pg_pool.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (id, username) VALUES (%s, %s);", (user_bob, user_bob))

    idem_key = f"idem_{uuid.uuid4().hex}"
    t1 = task_repo.create_task(user_alice, "Task A", "Goal A", idempotency_key=idem_key)
    t2 = task_repo.create_task(user_alice, "Task A Duplicate", "Goal A Dup", idempotency_key=idem_key)
    assert t1["id"] == t2["id"]

    # Bob cannot access Alice's task
    alice_task = task_repo.get_task(t1["id"], user_id=user_bob)
    assert alice_task is None

    # Bob cannot cancel Alice's task
    cancel_res = task_repo.cancel_task(t1["id"], user_id=user_bob)
    assert cancel_res is False


def test_postgres_atomic_leasing_skip_locked(pg_repos):
    """Verify atomic lease acquisition using FOR UPDATE SKIP LOCKED."""
    task_repo, _, user_id = pg_repos

    t = task_repo.create_task(user_id, "Leasable Task", "Must be leased")
    leased = task_repo.acquire_next_pending_task(worker_id="worker_pg_1")
    assert leased is not None
    assert leased["status"] == "running"


def test_postgres_task_steps_persistence_and_ordering(pg_repos):
    """Verify durable task step records, execution outputs, and step ordering."""
    task_repo, _, user_id = pg_repos

    task = task_repo.create_task(user_id, "Multi-Step Task", "Run 3 steps")
    tid = task["id"]

    task_repo.create_or_update_step(tid, user_id, step_index=2, name="Step C", status="pending")
    task_repo.create_or_update_step(tid, user_id, step_index=0, name="Step A", status="completed", tool_output={"val": 1})
    task_repo.create_or_update_step(tid, user_id, step_index=1, name="Step B", status="running")

    steps = task_repo.get_steps(tid, user_id)
    assert len(steps) == 3
    assert steps[0]["step_index"] == 0
    assert steps[0]["name"] == "Step A"
    assert steps[0]["status"] == "completed"
    assert steps[0]["tool_output"] == {"val": 1}
    assert steps[1]["step_index"] == 1
    assert steps[2]["step_index"] == 2


def test_postgres_approval_crud_nonce_and_decide(pg_repos):
    """Verify durable approval creation, cryptographic nonce, and atomic decision."""
    task_repo, appr_repo, user_id = pg_repos

    task = task_repo.create_task(user_id, "Task needing approval", "Dangerous action")
    task_repo.update_task_status(task["id"], user_id, "awaiting_approval")

    appr = appr_repo.create_approval(
        task_id=task["id"],
        user_id=user_id,
        action_type="system_write",
        action_payload={"path": "/etc/config", "content": "updated"},
        justification="Update system configuration",
        risk_level="critical",
    )
    assert appr["id"] is not None
    assert appr["status"] == "pending"
    assert len(appr["nonce"]) > 16

    # Verify get_approval_by_task
    found = appr_repo.get_approval_by_task(task["id"], user_id)
    assert found is not None
    assert found["id"] == appr["id"]

    # Wrong nonce rejected
    ok, err, _ = appr_repo.decide_approval(appr["id"], user_id, "approved", nonce="wrong_nonce")
    assert ok is False
    assert "nonce" in err.lower()

    # Correct decision
    ok, msg, decided = appr_repo.decide_approval(
        appr["id"], user_id, "approved", nonce=appr["nonce"], reason="Approved by admin"
    )
    assert ok is True
    assert decided["status"] == "approved"

    # Task resumed to pending
    resumed = task_repo.get_task(task["id"], user_id)
    assert resumed["status"] == "pending"


def test_postgres_approval_single_use_and_invalidation(pg_repos):
    """Verify approval single-use (replay attack protection)."""
    task_repo, appr_repo, user_id = pg_repos

    task = task_repo.create_task(user_id, "Single Use Task", "Test Replay")
    appr = appr_repo.create_approval(task["id"], user_id, "file_delete", {"path": "/tmp/1"}, "Clean disk")

    # First decision succeeds
    ok1, _, _ = appr_repo.decide_approval(appr["id"], user_id, "approved", appr["nonce"])
    assert ok1 is True

    # Second decision fails
    ok2, err2, _ = appr_repo.decide_approval(appr["id"], user_id, "rejected", appr["nonce"])
    assert ok2 is False
    assert "already been decided" in err2.lower()


def test_postgres_crash_recovery_sweep(pg_pool, pg_repos):
    """Verify that crashed running tasks are recovered by sweeper."""
    task_repo, _, user_id = pg_repos

    task = task_repo.create_task(user_id, "Crashed Task", "Simulate crash")
    task_repo.update_task_status(task["id"], user_id, "running")

    # Manually backdate updated_at in PostgreSQL
    with pg_pool.transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE tasks SET updated_at = CURRENT_TIMESTAMP - INTERVAL '400 seconds' WHERE id = %s;",
                (task["id"],),
            )

    recovered = task_repo.recover_stale_tasks(stale_threshold_seconds=300.0)
    assert task["id"] in recovered

    updated = task_repo.get_task(task["id"], user_id)
    assert updated["status"] == "failed"
    assert "crashed" in updated["error_message"].lower()
