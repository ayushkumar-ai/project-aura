"""M55 — PostgreSQL Fleet Repository Implementation.

Production-grade implementation of BaseFleetRepository backed by PostgreSQL 16.
Enforces row-level locking (SELECT ... FOR UPDATE SKIP LOCKED), monotonic fencing tokens,
atomic lease claims, deficit fairness scheduling, and crash recovery.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional
import uuid

from core.database import DatabaseConnectionPool
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

logger = logging.getLogger("aura.repositories.postgres_fleet")


class PostgresFleetRepository(BaseFleetRepository):
    """PostgreSQL fleet coordination repository implementing all M55 contracts."""

    def __init__(self, db_pool: DatabaseConnectionPool) -> None:
        self.pool = db_pool

    def _row_to_worker(self, row: dict) -> WorkerRecord:
        caps = row["capabilities"]
        if isinstance(caps, str):
            try:
                caps = json.loads(caps)
            except Exception:
                caps = ["*"]
        return WorkerRecord(
            worker_id=row["worker_id"],
            instance_id=row["instance_id"],
            hostname=row["hostname"],
            process_id=row["process_id"],
            incarnation_token=row["incarnation_token"],
            generation=row["generation"],
            status=WorkerStatus(row["status"]),
            capabilities=caps if isinstance(caps, list) else ["*"],
            concurrency_limit=row["concurrency_limit"],
            active_task_count=row["active_task_count"],
            heartbeat_interval_seconds=float(row["heartbeat_interval_seconds"]),
            missed_heartbeats_threshold=int(row["missed_heartbeats_threshold"]),
            last_heartbeat_at=row["last_heartbeat_at"],
            draining_since=row.get("draining_since"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_lease(self, row: dict) -> WorkerLeaseRecord:
        meta = row["metadata"]
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        return WorkerLeaseRecord(
            lease_id=row["lease_id"],
            resource_type=row["resource_type"],
            resource_id=row["resource_id"],
            worker_id=row["worker_id"],
            incarnation_token=row["incarnation_token"],
            fencing_token=int(row["fencing_token"]),
            lease_state=LeaseState(row["lease_state"]),
            acquired_at=row["acquired_at"],
            expires_at=row["expires_at"],
            renewed_at=row["renewed_at"],
            metadata=meta if isinstance(meta, dict) else {},
        )

    def _row_to_attempt(self, row: dict) -> ExecutionAttemptRecord:
        return ExecutionAttemptRecord(
            attempt_id=row["attempt_id"],
            resource_type=row["resource_type"],
            resource_id=row["resource_id"],
            tenant_id=row["tenant_id"],
            worker_id=row["worker_id"],
            incarnation_token=row["incarnation_token"],
            fencing_token=int(row["fencing_token"]),
            attempt_number=int(row["attempt_number"]),
            status=AttemptStatus(row["status"]),
            started_at=row["started_at"],
            finished_at=row.get("finished_at"),
            error_detail=row.get("error_detail") or "",
        )

    def _row_to_tenant_limits(self, row: dict) -> TenantWorkerLimitRecord:
        return TenantWorkerLimitRecord(
            tenant_id=row["tenant_id"],
            max_active_tasks=int(row["max_active_tasks"]),
            guaranteed_slots=int(row["guaranteed_slots"]),
            burst_capacity=int(row["burst_capacity"]),
            active_task_count=int(row["active_task_count"]),
            updated_at=row["updated_at"],
        )

    # --- Worker Node Lifecycle ---

    def register_worker(self, worker: WorkerRecord) -> WorkerRecord:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO workers (
                            worker_id, instance_id, hostname, process_id, incarnation_token,
                            generation, status, capabilities, concurrency_limit, active_task_count,
                            heartbeat_interval_seconds, missed_heartbeats_threshold,
                            last_heartbeat_at, draining_since, created_at, updated_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                        ON CONFLICT (worker_id) DO UPDATE SET
                            instance_id = EXCLUDED.instance_id,
                            hostname = EXCLUDED.hostname,
                            process_id = EXCLUDED.process_id,
                            incarnation_token = EXCLUDED.incarnation_token,
                            generation = workers.generation + 1,
                            status = EXCLUDED.status,
                            capabilities = EXCLUDED.capabilities,
                            concurrency_limit = EXCLUDED.concurrency_limit,
                            heartbeat_interval_seconds = EXCLUDED.heartbeat_interval_seconds,
                            missed_heartbeats_threshold = EXCLUDED.missed_heartbeats_threshold,
                            last_heartbeat_at = CURRENT_TIMESTAMP,
                            draining_since = EXCLUDED.draining_since,
                            updated_at = CURRENT_TIMESTAMP
                        RETURNING *;
                        """,
                        (
                            worker.worker_id,
                            worker.instance_id,
                            worker.hostname,
                            worker.process_id,
                            worker.incarnation_token,
                            worker.generation,
                            worker.status.value if isinstance(worker.status, WorkerStatus) else worker.status,
                            json.dumps(worker.capabilities),
                            worker.concurrency_limit,
                            worker.active_task_count,
                            worker.heartbeat_interval_seconds,
                            worker.missed_heartbeats_threshold,
                            worker.draining_since,
                        ),
                    )
                    row = cur.fetchone()
                    return self._row_to_worker(row)

    def update_worker_heartbeat(self, worker_id: str, incarnation_token: str) -> bool:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE workers
                        SET last_heartbeat_at = CURRENT_TIMESTAMP,
                            status = CASE WHEN status = 'unhealthy' THEN 'healthy' ELSE status END,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE worker_id = %s
                          AND incarnation_token = %s
                          AND status NOT IN ('stopped', 'expired');
                        """,
                        (worker_id, incarnation_token),
                    )
                    return cur.rowcount > 0

    def update_worker_status(
        self,
        worker_id: str,
        incarnation_token: str,
        status: WorkerStatus,
        draining_since: Optional[datetime] = None,
    ) -> bool:
        stat_val = status.value if isinstance(status, WorkerStatus) else status
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE workers
                        SET status = %s,
                            draining_since = CASE
                                WHEN %s = 'draining' AND draining_since IS NULL THEN COALESCE(%s, CURRENT_TIMESTAMP)
                                ELSE draining_since
                            END,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE worker_id = %s AND incarnation_token = %s;
                        """,
                        (stat_val, stat_val, draining_since, worker_id, incarnation_token),
                    )
                    return cur.rowcount > 0

    def get_worker(self, worker_id: str) -> Optional[WorkerRecord]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM workers WHERE worker_id = %s;", (worker_id,))
                row = cur.fetchone()
                return self._row_to_worker(row) if row else None

    def list_workers(self, status: Optional[WorkerStatus] = None) -> List[WorkerRecord]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                if status is not None:
                    stat_val = status.value if isinstance(status, WorkerStatus) else status
                    cur.execute("SELECT * FROM workers WHERE status = %s ORDER BY created_at ASC;", (stat_val,))
                else:
                    cur.execute("SELECT * FROM workers ORDER BY created_at ASC;")
                rows = cur.fetchall()
                return [self._row_to_worker(r) for r in rows]

    def unregister_worker(self, worker_id: str, incarnation_token: str) -> bool:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE workers
                        SET status = 'stopped', updated_at = CURRENT_TIMESTAMP
                        WHERE worker_id = %s AND incarnation_token = %s;
                        """,
                        (worker_id, incarnation_token),
                    )
                    return cur.rowcount > 0

    # --- Distributed Leases & Monotonic Fencing ---

    def acquire_lease(
        self,
        resource_type: str,
        resource_id: str,
        worker_id: str,
        incarnation_token: str,
        duration_seconds: float,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[WorkerLeaseRecord]:
        dur = max(1.0, duration_seconds)
        lease_id = f"lse_{uuid.uuid4().hex[:12]}"
        meta_json = json.dumps(metadata or {})

        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    # Verify worker is healthy
                    cur.execute(
                        "SELECT status FROM workers WHERE worker_id = %s AND incarnation_token = %s;",
                        (worker_id, incarnation_token),
                    )
                    w_row = cur.fetchone()
                    if not w_row or w_row["status"] != "healthy":
                        return None

                    # Upsert lease with monotonic fencing token increment
                    cur.execute(
                        """
                        INSERT INTO worker_leases (
                            lease_id, resource_type, resource_id, worker_id, incarnation_token,
                            fencing_token, lease_state, acquired_at, expires_at, renewed_at, metadata
                        ) VALUES (
                            %s, %s, %s, %s, %s, 1, 'active', CURRENT_TIMESTAMP,
                            CURRENT_TIMESTAMP + (%s || ' seconds')::interval, CURRENT_TIMESTAMP, %s
                        )
                        ON CONFLICT (resource_type, resource_id) DO UPDATE SET
                            lease_id = EXCLUDED.lease_id,
                            worker_id = EXCLUDED.worker_id,
                            incarnation_token = EXCLUDED.incarnation_token,
                            fencing_token = worker_leases.fencing_token + 1,
                            lease_state = 'active',
                            acquired_at = CURRENT_TIMESTAMP,
                            expires_at = CURRENT_TIMESTAMP + (%s || ' seconds')::interval,
                            renewed_at = CURRENT_TIMESTAMP,
                            metadata = EXCLUDED.metadata
                        WHERE worker_leases.lease_state IN ('released', 'expired', 'fenced')
                           OR worker_leases.expires_at < CURRENT_TIMESTAMP
                           OR (worker_leases.worker_id = EXCLUDED.worker_id AND worker_leases.incarnation_token = EXCLUDED.incarnation_token)
                        RETURNING *;
                        """,
                        (
                            lease_id,
                            resource_type,
                            resource_id,
                            worker_id,
                            incarnation_token,
                            str(dur),
                            meta_json,
                            str(dur),
                        ),
                    )
                    row = cur.fetchone()
                    return self._row_to_lease(row) if row else None

    def renew_lease(
        self,
        resource_type: str,
        resource_id: str,
        worker_id: str,
        incarnation_token: str,
        fencing_token: int,
        duration_seconds: float,
    ) -> bool:
        dur = max(1.0, duration_seconds)
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE worker_leases
                        SET expires_at = CURRENT_TIMESTAMP + (%s || ' seconds')::interval,
                            renewed_at = CURRENT_TIMESTAMP,
                            lease_state = 'renewed'
                        WHERE resource_type = %s
                          AND resource_id = %s
                          AND worker_id = %s
                          AND incarnation_token = %s
                          AND fencing_token = %s
                          AND lease_state IN ('active', 'renewed');
                        """,
                        (str(dur), resource_type, resource_id, worker_id, incarnation_token, fencing_token),
                    )
                    return cur.rowcount > 0

    def release_lease(
        self,
        resource_type: str,
        resource_id: str,
        worker_id: str,
        incarnation_token: str,
        fencing_token: int,
    ) -> bool:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE worker_leases
                        SET lease_state = 'released'
                        WHERE resource_type = %s
                          AND resource_id = %s
                          AND worker_id = %s
                          AND incarnation_token = %s
                          AND fencing_token = %s;
                        """,
                        (resource_type, resource_id, worker_id, incarnation_token, fencing_token),
                    )
                    return cur.rowcount > 0

    def get_lease(self, resource_type: str, resource_id: str) -> Optional[WorkerLeaseRecord]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM worker_leases WHERE resource_type = %s AND resource_id = %s;",
                    (resource_type, resource_id),
                )
                row = cur.fetchone()
                return self._row_to_lease(row) if row else None

    def verify_fencing(
        self,
        resource_type: str,
        resource_id: str,
        worker_id: str,
        incarnation_token: str,
        fencing_token: int,
    ) -> bool:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) as cnt
                    FROM worker_leases
                    WHERE resource_type = %s
                      AND resource_id = %s
                      AND worker_id = %s
                      AND incarnation_token = %s
                      AND fencing_token = %s
                      AND lease_state IN ('active', 'renewed')
                      AND expires_at > CURRENT_TIMESTAMP;
                    """,
                    (resource_type, resource_id, worker_id, incarnation_token, fencing_token),
                )
                row = cur.fetchone()
                return bool(row and row["cnt"] > 0)

    def batch_renew_worker_leases(
        self,
        worker_id: str,
        incarnation_token: str,
        duration_seconds: float,
    ) -> int:
        dur = max(1.0, duration_seconds)
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE worker_leases
                        SET expires_at = CURRENT_TIMESTAMP + (%s || ' seconds')::interval,
                            renewed_at = CURRENT_TIMESTAMP,
                            lease_state = 'renewed'
                        WHERE worker_id = %s
                          AND incarnation_token = %s
                          AND lease_state IN ('active', 'renewed')
                          AND expires_at > CURRENT_TIMESTAMP;
                        """,
                        (str(dur), worker_id, incarnation_token),
                    )
                    return cur.rowcount

    # --- Execution Attempts Ledger ---

    def record_attempt(self, attempt: ExecutionAttemptRecord) -> ExecutionAttemptRecord:
        stat_val = attempt.status.value if isinstance(attempt.status, AttemptStatus) else attempt.status
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO execution_attempts (
                            attempt_id, resource_type, resource_id, tenant_id,
                            worker_id, incarnation_token, fencing_token, attempt_number,
                            status, started_at, finished_at, error_detail
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s, %s
                        ) RETURNING *;
                        """,
                        (
                            attempt.attempt_id,
                            attempt.resource_type,
                            attempt.resource_id,
                            attempt.tenant_id,
                            attempt.worker_id,
                            attempt.incarnation_token,
                            attempt.fencing_token,
                            attempt.attempt_number,
                            stat_val,
                            attempt.finished_at,
                            attempt.error_detail,
                        ),
                    )
                    row = cur.fetchone()
                    return self._row_to_attempt(row)

    def update_attempt_status(
        self,
        attempt_id: str,
        status: AttemptStatus,
        error_detail: str = "",
        finished_at: Optional[datetime] = None,
    ) -> bool:
        stat_val = status.value if isinstance(status, AttemptStatus) else status
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE execution_attempts
                        SET status = %s,
                            error_detail = CASE WHEN %s != '' THEN %s ELSE error_detail END,
                            finished_at = COALESCE(%s, CURRENT_TIMESTAMP)
                        WHERE attempt_id = %s
                          AND status NOT IN ('completed', 'failed', 'timed_out', 'fenced', 'recovered');
                        """,
                        (stat_val, error_detail, error_detail, finished_at, attempt_id),
                    )
                    return cur.rowcount > 0

    def get_attempts_for_resource(self, resource_type: str, resource_id: str) -> List[ExecutionAttemptRecord]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM execution_attempts
                    WHERE resource_type = %s AND resource_id = %s
                    ORDER BY started_at ASC;
                    """,
                    (resource_type, resource_id),
                )
                rows = cur.fetchall()
                return [self._row_to_attempt(r) for r in rows]

    # --- Tenant Concurrency Quotas & Fairness ---

    def get_tenant_limits(self, tenant_id: str) -> TenantWorkerLimitRecord:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO tenant_worker_limits (tenant_id)
                        VALUES (%s)
                        ON CONFLICT (tenant_id) DO NOTHING;
                        """,
                        (tenant_id,),
                    )
                    cur.execute("SELECT * FROM tenant_worker_limits WHERE tenant_id = %s;", (tenant_id,))
                    row = cur.fetchone()
                    return self._row_to_tenant_limits(row)

    def set_tenant_limits(self, limits: TenantWorkerLimitRecord) -> TenantWorkerLimitRecord:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO tenant_worker_limits (
                            tenant_id, max_active_tasks, guaranteed_slots, burst_capacity, active_task_count, updated_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, CURRENT_TIMESTAMP
                        )
                        ON CONFLICT (tenant_id) DO UPDATE SET
                            max_active_tasks = EXCLUDED.max_active_tasks,
                            guaranteed_slots = EXCLUDED.guaranteed_slots,
                            burst_capacity = EXCLUDED.burst_capacity,
                            updated_at = CURRENT_TIMESTAMP
                        RETURNING *;
                        """,
                        (
                            limits.tenant_id,
                            limits.max_active_tasks,
                            limits.guaranteed_slots,
                            limits.burst_capacity,
                            limits.active_task_count,
                        ),
                    )
                    row = cur.fetchone()
                    return self._row_to_tenant_limits(row)

    def adjust_tenant_active_count(self, tenant_id: str, delta: int) -> int:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    # Auto-provision if missing
                    cur.execute(
                        "INSERT INTO tenant_worker_limits (tenant_id) VALUES (%s) ON CONFLICT (tenant_id) DO NOTHING;",
                        (tenant_id,),
                    )
                    cur.execute(
                        """
                        UPDATE tenant_worker_limits
                        SET active_task_count = active_task_count + %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE tenant_id = %s
                          AND (active_task_count + %s) >= 0
                        RETURNING active_task_count;
                        """,
                        (delta, tenant_id, delta),
                    )
                    row = cur.fetchone()
                    if not row:
                        raise ValueError(f"Tenant {tenant_id} active_task_count cannot underflow below 0")
                    return int(row["active_task_count"])

    def reconcile_tenant_capacities(self) -> Dict[str, int]:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    # Compute actual active task leases per tenant
                    cur.execute(
                        """
                        WITH actual_active AS (
                            SELECT (metadata->>'tenant_id') AS tenant_id, COUNT(*) AS live_count
                            FROM worker_leases
                            WHERE resource_type = 'task'
                              AND lease_state IN ('active', 'renewed')
                              AND expires_at > CURRENT_TIMESTAMP
                              AND (metadata->>'tenant_id') IS NOT NULL
                            GROUP BY (metadata->>'tenant_id')
                        )
                        UPDATE tenant_worker_limits twl
                        SET active_task_count = COALESCE(aa.live_count, 0),
                            updated_at = CURRENT_TIMESTAMP
                        FROM tenant_worker_limits twl_orig
                        LEFT JOIN actual_active aa ON twl_orig.tenant_id = aa.tenant_id
                        WHERE twl.tenant_id = twl_orig.tenant_id
                          AND twl.active_task_count != COALESCE(aa.live_count, 0)
                        RETURNING twl.tenant_id, twl.active_task_count;
                        """
                    )
                    rows = cur.fetchall()
                    return {r["tenant_id"]: int(r["active_task_count"]) for r in rows}

    # --- Fair Task Claiming & Orchestration ---

    def claim_next_fair_task(
        self,
        worker_id: str,
        incarnation_token: str,
        lease_duration_seconds: float = 30.0,
    ) -> Optional[ClaimedTask]:
        dur = max(1.0, lease_duration_seconds)
        attempt_id = f"att_{uuid.uuid4().hex[:12]}"
        lease_id = f"lse_{uuid.uuid4().hex[:12]}"

        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    # 1. Lock and check worker capacity
                    cur.execute(
                        """
                        SELECT status, concurrency_limit, active_task_count
                        FROM workers
                        WHERE worker_id = %s AND incarnation_token = %s
                        FOR UPDATE;
                        """,
                        (worker_id, incarnation_token),
                    )
                    w_row = cur.fetchone()
                    if not w_row:
                        return None
                    if w_row["status"] != "healthy":
                        return None
                    if w_row["active_task_count"] >= w_row["concurrency_limit"]:
                        return None

                    # 2. Deficit Fairness Candidate Selection: Find pending task with lowest tenant utilization
                    cur.execute(
                        """
                        SELECT t.id, t.user_id, t.title, t.goal, t.context, t.timeout_seconds,
                               COALESCE(twl.active_task_count, 0) as tenant_active,
                               COALESCE(twl.max_active_tasks, 10) as tenant_max,
                               (COALESCE(twl.active_task_count, 0)::float / GREATEST(1, COALESCE(twl.max_active_tasks, 10))) as utilization_ratio
                        FROM tasks t
                        LEFT JOIN tenant_worker_limits twl ON t.user_id = twl.tenant_id
                        WHERE t.status = 'pending'
                          AND COALESCE(twl.active_task_count, 0) < COALESCE(twl.max_active_tasks, 10)
                        ORDER BY utilization_ratio ASC, t.created_at ASC
                        LIMIT 1
                        FOR UPDATE OF t SKIP LOCKED;
                        """
                    )
                    t_row = cur.fetchone()
                    if not t_row:
                        return None

                    task_id = str(t_row["id"])
                    user_id = str(t_row["user_id"])

                    # 3. Lock or insert tenant limit and increment active_task_count
                    cur.execute(
                        """
                        INSERT INTO tenant_worker_limits (tenant_id, active_task_count)
                        VALUES (%s, 1)
                        ON CONFLICT (tenant_id) DO UPDATE
                        SET active_task_count = tenant_worker_limits.active_task_count + 1,
                            updated_at = CURRENT_TIMESTAMP
                        RETURNING active_task_count;
                        """,
                        (user_id,),
                    )

                    # 4. Increment worker active_task_count
                    cur.execute(
                        """
                        UPDATE workers
                        SET active_task_count = active_task_count + 1,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE worker_id = %s AND incarnation_token = %s;
                        """,
                        (worker_id, incarnation_token),
                    )

                    # 5. Acquire lease with monotonic fencing token
                    meta_json = json.dumps({"tenant_id": user_id, "user_id": user_id})
                    cur.execute(
                        """
                        INSERT INTO worker_leases (
                            lease_id, resource_type, resource_id, worker_id, incarnation_token,
                            fencing_token, lease_state, acquired_at, expires_at, renewed_at, metadata
                        ) VALUES (
                            %s, 'task', %s, %s, %s, 1, 'active', CURRENT_TIMESTAMP,
                            CURRENT_TIMESTAMP + (%s || ' seconds')::interval, CURRENT_TIMESTAMP, %s
                        )
                        ON CONFLICT (resource_type, resource_id) DO UPDATE SET
                            lease_id = EXCLUDED.lease_id,
                            worker_id = EXCLUDED.worker_id,
                            incarnation_token = EXCLUDED.incarnation_token,
                            fencing_token = worker_leases.fencing_token + 1,
                            lease_state = 'active',
                            acquired_at = CURRENT_TIMESTAMP,
                            expires_at = CURRENT_TIMESTAMP + (%s || ' seconds')::interval,
                            renewed_at = CURRENT_TIMESTAMP,
                            metadata = EXCLUDED.metadata
                        RETURNING fencing_token, lease_id;
                        """,
                        (
                            lease_id,
                            task_id,
                            worker_id,
                            incarnation_token,
                            str(dur),
                            meta_json,
                            str(dur),
                        ),
                    )
                    lease_row = cur.fetchone()
                    fencing_token = int(lease_row["fencing_token"])

                    # 6. Record execution attempt
                    cur.execute(
                        """
                        SELECT COUNT(*) as attempt_count
                        FROM execution_attempts
                        WHERE resource_type = 'task' AND resource_id = %s;
                        """,
                        (task_id,),
                    )
                    att_count_row = cur.fetchone()
                    attempt_num = (int(att_count_row["attempt_count"]) if att_count_row else 0) + 1

                    cur.execute(
                        """
                        INSERT INTO execution_attempts (
                            attempt_id, resource_type, resource_id, tenant_id,
                            worker_id, incarnation_token, fencing_token, attempt_number,
                            status, started_at
                        ) VALUES (
                            %s, 'task', %s, %s, %s, %s, %s, %s, 'running', CURRENT_TIMESTAMP
                        );
                        """,
                        (
                            attempt_id,
                            task_id,
                            user_id,
                            worker_id,
                            incarnation_token,
                            fencing_token,
                            attempt_num,
                        ),
                    )

                    # 7. Update task status to running
                    cur.execute(
                        """
                        UPDATE tasks
                        SET status = 'running',
                            started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s;
                        """,
                        (task_id,),
                    )

                    return ClaimedTask(
                        task_id=task_id,
                        user_id=user_id,
                        lease_id=lease_id,
                        fencing_token=fencing_token,
                        incarnation_token=incarnation_token,
                        attempt_id=attempt_id,
                        attempt_number=attempt_num,
                        task_data={
                            "id": task_id,
                            "user_id": user_id,
                            "title": t_row["title"],
                            "goal": t_row["goal"],
                            "context": t_row["context"],
                            "timeout_seconds": t_row["timeout_seconds"],
                        },
                    )

    # --- Sweeping & Crash Recovery ---

    def reap_expired_workers(self, threshold_seconds: float = 15.0) -> List[str]:
        dur = max(1.0, threshold_seconds)
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE workers
                        SET status = 'expired', updated_at = CURRENT_TIMESTAMP
                        WHERE status IN ('healthy', 'unhealthy', 'draining')
                          AND last_heartbeat_at < CURRENT_TIMESTAMP - (%s || ' seconds')::interval
                        RETURNING worker_id;
                        """,
                        (str(dur),),
                    )
                    rows = cur.fetchall()
                    return [str(r["worker_id"]) for r in rows]

    def sweep_orphaned_leases(self, lease_expiry_seconds: float = 30.0) -> List[str]:
        with self.pool.connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    # 1. Select expired or dead worker leases
                    cur.execute(
                        """
                        SELECT wl.lease_id, wl.resource_type, wl.resource_id, wl.worker_id,
                               wl.incarnation_token, wl.fencing_token, wl.metadata,
                               w.status as worker_status
                        FROM worker_leases wl
                        LEFT JOIN workers w ON wl.worker_id = w.worker_id
                        WHERE wl.lease_state IN ('active', 'renewed')
                          AND (wl.expires_at < CURRENT_TIMESTAMP OR w.status IN ('expired', 'stopped', 'unhealthy') OR w.worker_id IS NULL)
                        FOR UPDATE OF wl;
                        """
                    )
                    stale_leases = cur.fetchall()
                    if not stale_leases:
                        return []

                    recovered_resources = []
                    for lease in stale_leases:
                        res_type = lease["resource_type"]
                        res_id = str(lease["resource_id"])
                        fencing_token = int(lease["fencing_token"])
                        meta = lease["metadata"]
                        if isinstance(meta, str):
                            try:
                                meta = json.loads(meta)
                            except Exception:
                                meta = {}
                        tenant_id = meta.get("tenant_id") or meta.get("user_id")

                        # Mark lease expired/fenced
                        cur.execute(
                            "UPDATE worker_leases SET lease_state = 'expired' WHERE lease_id = %s;",
                            (lease["lease_id"],),
                        )

                        # Mark attempt recovered
                        cur.execute(
                            """
                            UPDATE execution_attempts
                            SET status = 'recovered',
                                error_detail = 'Lease expired or worker ungracefully terminated',
                                finished_at = CURRENT_TIMESTAMP
                            WHERE resource_type = %s
                              AND resource_id = %s
                              AND fencing_token = %s
                              AND status = 'running';
                            """,
                            (res_type, res_id, fencing_token),
                        )

                        # Decrement tenant active count if task
                        if res_type == "task" and tenant_id:
                            cur.execute(
                                """
                                UPDATE tenant_worker_limits
                                SET active_task_count = GREATEST(0, active_task_count - 1),
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE tenant_id = %s;
                                """,
                                (tenant_id,),
                            )

                            # Re-queue task if running
                            cur.execute(
                                """
                                UPDATE tasks
                                SET status = 'pending',
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE id = %s AND status = 'running';
                                """,
                                (res_id,),
                            )

                        recovered_resources.append(res_id)

                    return recovered_resources
