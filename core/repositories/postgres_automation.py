"""M53 — PostgreSQL Automation Repository Implementation."""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from uuid import uuid4

from core.database import DatabaseConnectionPool
from core.automations.types import (
    AutomationNotFoundError,
    AutomationQuotaExceededError,
    LeaseFencingError,
    LockTimeoutError,
    StatementTimeoutError,
    TransactionDeadlineExceededError,
)
from core.repositories.base_automation import BaseAutomationRepository

logger = logging.getLogger("aura.repositories.postgres_automation")


class BoundedTransactionContext:
    """Bounded transaction context with strict statement, lock, and application timeouts."""

    def __init__(
        self,
        conn: Any,
        lock_timeout_ms: int = 3000,
        statement_timeout_ms: int = 3000,
        deadline_seconds: float = 5.0,
    ) -> None:
        self.conn = conn
        self.lock_timeout_ms = lock_timeout_ms
        self.statement_timeout_ms = statement_timeout_ms
        self.deadline_seconds = deadline_seconds
        self.start_monotonic = 0.0
        self.deadline_monotonic = 0.0

    def __enter__(self) -> BoundedTransactionContext:
        self.start_monotonic = time.monotonic()
        self.deadline_monotonic = self.start_monotonic + self.deadline_seconds
        with self.conn.cursor() as cur:
            cur.execute(f"SET LOCAL lock_timeout = '{int(self.lock_timeout_ms)}ms';")
            cur.execute(f"SET LOCAL statement_timeout = '{int(self.statement_timeout_ms)}ms';")
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is not None:
            try:
                self.conn.rollback()
            except Exception:
                pass

    def check_deadline(self) -> None:
        now = time.monotonic()
        if now > self.deadline_monotonic:
            elapsed = now - self.start_monotonic
            self.conn.rollback()
            raise TransactionDeadlineExceededError(
                f"Fenced dispatch transaction exceeded hard deadline: {elapsed:.3f}s > {self.deadline_seconds}s"
            )

    def commit(self) -> None:
        self.check_deadline()
        self.conn.commit()


class PostgresAutomationRepository(BaseAutomationRepository):
    """Authoritative PostgreSQL implementation of BaseAutomationRepository."""

    def __init__(self, pool: DatabaseConnectionPool, task_repo: Any | None = None) -> None:
        self.pool = pool
        self.task_repo = task_repo

    def _format_automation_row(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        d = dict(row)
        for jcol in ("trigger_config", "condition_config", "action_template", "metadata"):
            if jcol in d and isinstance(d[jcol], str):
                try:
                    d[jcol] = json.loads(d[jcol])
                except Exception:
                    pass
        return d

    def _format_run_row(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if not row:
            return None
        d = dict(row)
        for jcol in ("condition_evaluation",):
            if jcol in d and isinstance(d[jcol], str):
                try:
                    d[jcol] = json.loads(d[jcol])
                except Exception:
                    pass
        return d

    def create_automation(
        self,
        user_id: str,
        name: str,
        trigger_type: str,
        trigger_config: dict[str, Any],
        condition_config: dict[str, Any] | None = None,
        action_template: dict[str, Any] | None = None,
        description: str = "",
        max_runs: int | None = None,
        cooldown_seconds: int = 60,
        metadata: dict[str, Any] | None = None,
        next_fire_at: float | None = None,
        automation_id: str | None = None,
    ) -> dict[str, Any]:
        eff_id = (automation_id or str(uuid4())).strip()
        eff_cond = condition_config or {}
        eff_action = action_template or {}
        eff_meta = metadata or {}

        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO automations (
                        id, user_id, name, description, status, trigger_type,
                        trigger_config, condition_config, action_template,
                        next_fire_at, max_runs, cooldown_seconds, metadata,
                        created_at, updated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, 'active', %s,
                        %s, %s, %s,
                        to_timestamp(%s::double precision),
                        %s, %s, %s,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    RETURNING id, user_id, name, description, status, trigger_type,
                              trigger_config, condition_config, action_template,
                              EXTRACT(EPOCH FROM next_fire_at) as next_fire_at,
                              EXTRACT(EPOCH FROM last_fired_at) as last_fired_at,
                              fire_count, max_runs, cooldown_seconds,
                              lease_owner, lease_token,
                              EXTRACT(EPOCH FROM claimed_at) as claimed_at,
                              EXTRACT(EPOCH FROM lease_expires_at) as lease_expires_at,
                              metadata,
                              EXTRACT(EPOCH FROM created_at) as created_at,
                              EXTRACT(EPOCH FROM updated_at) as updated_at;
                    """,
                    (
                        eff_id,
                        user_id,
                        name.strip(),
                        description.strip(),
                        trigger_type.strip(),
                        json.dumps(trigger_config),
                        json.dumps(eff_cond),
                        json.dumps(eff_action),
                        next_fire_at,
                        max_runs,
                        int(cooldown_seconds),
                        json.dumps(eff_meta),
                    ),
                )
                row = cur.fetchone()
                return self._format_automation_row(row)  # type: ignore

    def get_automation(self, automation_id: str, user_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, user_id, name, description, status, trigger_type,
                           trigger_config, condition_config, action_template,
                           EXTRACT(EPOCH FROM next_fire_at) as next_fire_at,
                           EXTRACT(EPOCH FROM last_fired_at) as last_fired_at,
                           fire_count, max_runs, cooldown_seconds,
                           lease_owner, lease_token,
                           EXTRACT(EPOCH FROM claimed_at) as claimed_at,
                           EXTRACT(EPOCH FROM lease_expires_at) as lease_expires_at,
                           metadata,
                           EXTRACT(EPOCH FROM created_at) as created_at,
                           EXTRACT(EPOCH FROM updated_at) as updated_at
                    FROM automations
                    WHERE id = %s AND user_id = %s;
                    """,
                    (automation_id, user_id),
                )
                return self._format_automation_row(cur.fetchone())

    def list_automations(
        self,
        user_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                if status:
                    cur.execute(
                        """
                        SELECT id, user_id, name, description, status, trigger_type,
                               trigger_config, condition_config, action_template,
                               EXTRACT(EPOCH FROM next_fire_at) as next_fire_at,
                               EXTRACT(EPOCH FROM last_fired_at) as last_fired_at,
                               fire_count, max_runs, cooldown_seconds,
                               lease_owner, lease_token,
                               EXTRACT(EPOCH FROM claimed_at) as claimed_at,
                               EXTRACT(EPOCH FROM lease_expires_at) as lease_expires_at,
                               metadata,
                               EXTRACT(EPOCH FROM created_at) as created_at,
                               EXTRACT(EPOCH FROM updated_at) as updated_at
                        FROM automations
                        WHERE user_id = %s AND status = %s
                        ORDER BY created_at DESC
                        LIMIT %s OFFSET %s;
                        """,
                        (user_id, status, limit, offset),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, user_id, name, description, status, trigger_type,
                               trigger_config, condition_config, action_template,
                               EXTRACT(EPOCH FROM next_fire_at) as next_fire_at,
                               EXTRACT(EPOCH FROM last_fired_at) as last_fired_at,
                               fire_count, max_runs, cooldown_seconds,
                               lease_owner, lease_token,
                               EXTRACT(EPOCH FROM claimed_at) as claimed_at,
                               EXTRACT(EPOCH FROM lease_expires_at) as lease_expires_at,
                               metadata,
                               EXTRACT(EPOCH FROM created_at) as created_at,
                               EXTRACT(EPOCH FROM updated_at) as updated_at
                        FROM automations
                        WHERE user_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s OFFSET %s;
                        """,
                        (user_id, limit, offset),
                    )
                return [self._format_automation_row(r) for r in cur.fetchall() if r]  # type: ignore

    def count_automations(self, user_id: str, status: str | None = None) -> int:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                if status:
                    cur.execute(
                        "SELECT COUNT(*) as count FROM automations WHERE user_id = %s AND status = %s;",
                        (user_id, status),
                    )
                else:
                    cur.execute(
                        "SELECT COUNT(*) as count FROM automations WHERE user_id = %s;",
                        (user_id,),
                    )
                row = cur.fetchone()
                return int(row["count"]) if row else 0

    def update_automation(
        self,
        automation_id: str,
        user_id: str,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        set_clauses: list[str] = []
        params: list[Any] = []

        allowed_fields = {
            "name": ("name", lambda x: str(x).strip()),
            "description": ("description", lambda x: str(x).strip()),
            "status": ("status", str),
            "trigger_config": ("trigger_config", json.dumps),
            "condition_config": ("condition_config", json.dumps),
            "action_template": ("action_template", json.dumps),
            "max_runs": ("max_runs", lambda x: int(x) if x is not None else None),
            "cooldown_seconds": ("cooldown_seconds", int),
            "metadata": ("metadata", json.dumps),
            "lease_owner": ("lease_owner", lambda x: x),
            "lease_token": ("lease_token", lambda x: x),
        }

        for k, v in kwargs.items():
            if k == "next_fire_at":
                set_clauses.append("next_fire_at = to_timestamp(%s::double precision)")
                params.append(v)
            elif k == "claimed_at":
                set_clauses.append("claimed_at = to_timestamp(%s::double precision)")
                params.append(v)
            elif k == "lease_expires_at":
                set_clauses.append("lease_expires_at = to_timestamp(%s::double precision)")
                params.append(v)
            elif k in allowed_fields:
                col, transform = allowed_fields[k]
                set_clauses.append(f"{col} = %s")
                params.append(transform(v))

        if not set_clauses:
            return self.get_automation(automation_id, user_id)

        set_clauses.append("updated_at = CURRENT_TIMESTAMP")
        params.extend([automation_id, user_id])

        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE automations
                    SET {", ".join(set_clauses)}
                    WHERE id = %s AND user_id = %s
                    RETURNING id, user_id, name, description, status, trigger_type,
                              trigger_config, condition_config, action_template,
                              EXTRACT(EPOCH FROM next_fire_at) as next_fire_at,
                              EXTRACT(EPOCH FROM last_fired_at) as last_fired_at,
                              fire_count, max_runs, cooldown_seconds,
                              lease_owner, lease_token,
                              EXTRACT(EPOCH FROM claimed_at) as claimed_at,
                              EXTRACT(EPOCH FROM lease_expires_at) as lease_expires_at,
                              metadata,
                              EXTRACT(EPOCH FROM created_at) as created_at,
                              EXTRACT(EPOCH FROM updated_at) as updated_at;
                    """,
                    params,
                )
                return self._format_automation_row(cur.fetchone())

    def delete_automation(self, automation_id: str, user_id: str) -> bool:
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM automations WHERE id = %s AND user_id = %s RETURNING id;",
                    (automation_id, user_id),
                )
                return cur.fetchone() is not None

    def pause_automation(self, automation_id: str, user_id: str) -> dict[str, Any] | None:
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE automations
                    SET status = 'paused',
                        next_fire_at = NULL,
                        lease_owner = NULL,
                        lease_token = NULL,
                        claimed_at = NULL,
                        lease_expires_at = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND user_id = %s
                    RETURNING id, user_id, name, description, status, trigger_type,
                              trigger_config, condition_config, action_template,
                              EXTRACT(EPOCH FROM next_fire_at) as next_fire_at,
                              EXTRACT(EPOCH FROM last_fired_at) as last_fired_at,
                              fire_count, max_runs, cooldown_seconds,
                              lease_owner, lease_token,
                              EXTRACT(EPOCH FROM claimed_at) as claimed_at,
                              EXTRACT(EPOCH FROM lease_expires_at) as lease_expires_at,
                              metadata,
                              EXTRACT(EPOCH FROM created_at) as created_at,
                              EXTRACT(EPOCH FROM updated_at) as updated_at;
                    """,
                    (automation_id, user_id),
                )
                return self._format_automation_row(cur.fetchone())

    def resume_automation(
        self,
        automation_id: str,
        user_id: str,
        next_fire_at: float | None = None,
    ) -> dict[str, Any] | None:
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE automations
                    SET status = 'active',
                        next_fire_at = COALESCE(to_timestamp(%s::double precision), next_fire_at),
                        lease_owner = NULL,
                        lease_token = NULL,
                        claimed_at = NULL,
                        lease_expires_at = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND user_id = %s
                    RETURNING id, user_id, name, description, status, trigger_type,
                              trigger_config, condition_config, action_template,
                              EXTRACT(EPOCH FROM next_fire_at) as next_fire_at,
                              EXTRACT(EPOCH FROM last_fired_at) as last_fired_at,
                              fire_count, max_runs, cooldown_seconds,
                              lease_owner, lease_token,
                              EXTRACT(EPOCH FROM claimed_at) as claimed_at,
                              EXTRACT(EPOCH FROM lease_expires_at) as lease_expires_at,
                              metadata,
                              EXTRACT(EPOCH FROM created_at) as created_at,
                              EXTRACT(EPOCH FROM updated_at) as updated_at;
                    """,
                    (next_fire_at, automation_id, user_id),
                )
                return self._format_automation_row(cur.fetchone())

    def claim_due_automations(
        self,
        worker_id: str,
        limit: int = 10,
        lease_ttl_seconds: int = 120,
    ) -> list[dict[str, Any]]:
        claimed_records: list[dict[str, Any]] = []
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id
                    FROM automations
                    WHERE status = 'active'
                      AND next_fire_at IS NOT NULL
                      AND next_fire_at <= clock_timestamp()
                      AND (lease_expires_at IS NULL OR lease_expires_at < clock_timestamp())
                    ORDER BY next_fire_at ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED;
                    """,
                    (limit,),
                )
                due_ids = [row["id"] for row in cur.fetchall()]
                if not due_ids:
                    return []

                for auto_id in due_ids:
                    lease_token = str(uuid4())
                    cur.execute(
                        """
                        UPDATE automations
                        SET lease_owner = %s,
                            lease_token = %s,
                            claimed_at = clock_timestamp(),
                            lease_expires_at = clock_timestamp() + (%s || ' seconds')::interval,
                            updated_at = clock_timestamp()
                        WHERE id = %s
                        RETURNING id, user_id, name, description, status, trigger_type,
                                  trigger_config, condition_config, action_template,
                                  EXTRACT(EPOCH FROM next_fire_at) as next_fire_at,
                                  EXTRACT(EPOCH FROM last_fired_at) as last_fired_at,
                                  fire_count, max_runs, cooldown_seconds,
                                  lease_owner, lease_token,
                                  EXTRACT(EPOCH FROM claimed_at) as claimed_at,
                                  EXTRACT(EPOCH FROM lease_expires_at) as lease_expires_at,
                                  metadata,
                                  EXTRACT(EPOCH FROM created_at) as created_at,
                                  EXTRACT(EPOCH FROM updated_at) as updated_at;
                        """,
                        (worker_id, lease_token, lease_ttl_seconds, auto_id),
                    )
                    row = cur.fetchone()
                    if row:
                        claimed_records.append(self._format_automation_row(row))  # type: ignore

        return claimed_records

    def renew_lease(
        self,
        automation_id: str,
        lease_token: str,
        lease_ttl_seconds: int = 120,
    ) -> bool:
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE automations
                    SET lease_expires_at = clock_timestamp() + (%s || ' seconds')::interval,
                        updated_at = clock_timestamp()
                    WHERE id = %s AND lease_token = %s
                    RETURNING id;
                    """,
                    (lease_ttl_seconds, automation_id, lease_token),
                )
                return cur.fetchone() is not None

    def release_lease(self, automation_id: str, lease_token: str) -> bool:
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE automations
                    SET lease_owner = NULL,
                        lease_token = NULL,
                        claimed_at = NULL,
                        lease_expires_at = NULL,
                        updated_at = clock_timestamp()
                    WHERE id = %s AND lease_token = %s
                    RETURNING id;
                    """,
                    (automation_id, lease_token),
                )
                return cur.fetchone() is not None

    def recover_stale_leases(self, limit: int = 50) -> int:
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE automations
                    SET lease_owner = NULL,
                        lease_token = NULL,
                        claimed_at = NULL,
                        lease_expires_at = NULL,
                        updated_at = clock_timestamp()
                    WHERE status = 'active'
                      AND lease_expires_at IS NOT NULL
                      AND lease_expires_at < clock_timestamp()
                    RETURNING id;
                    """,
                )
                return len(cur.fetchall())

    def execute_fenced_dispatch(
        self,
        automation_id: str,
        user_id: str,
        lease_token: str,
        slot_timestamp: float,
        trigger_timestamp: float,
        action_template: dict[str, Any],
        next_fire_at: float | None,
        max_runs_per_hour: int = 60,
        timeout_seconds: float = 5.0,
        lock_timeout_ms: int = 3000,
        statement_timeout_ms: int = 3000,
    ) -> dict[str, Any]:
        """Execute the atomic, fenced dispatch transaction adhering strictly to M53 v3.3 specification."""
        run_id = str(uuid4())
        task_id = str(uuid4())
        idempotency_prefix = action_template.get("idempotency_prefix") or "m53"
        idempotency_key = f"{idempotency_prefix}_{automation_id}_{int(slot_timestamp)}"
        title = action_template.get("title") or "Scheduled Automation Task"
        goal = action_template.get("goal") or "Execute proactive automation"
        context_data = action_template.get("context") or {}
        context_data["automation_id"] = automation_id
        context_data["slot_timestamp"] = slot_timestamp
        task_timeout = int(action_template.get("timeout_seconds", 600))

        with self.pool.connection() as conn:
            with BoundedTransactionContext(
                conn,
                lock_timeout_ms=lock_timeout_ms,
                statement_timeout_ms=statement_timeout_ms,
                deadline_seconds=timeout_seconds,
            ) as btx:
                with conn.cursor() as cur:
                    btx.check_deadline()
                    # Step 1: Atomic Fencing Lock (Using clock_timestamp())
                    cur.execute(
                        """
                        SELECT id, user_id, status
                        FROM automations
                        WHERE id = %s
                          AND user_id = %s
                          AND lease_token = %s
                          AND lease_expires_at > clock_timestamp()
                          AND status = 'active'
                        FOR UPDATE;
                        """,
                        (automation_id, user_id, lease_token),
                    )
                    automation_row = cur.fetchone()
                    if not automation_row:
                        conn.rollback()
                        raise LeaseFencingError(
                            "Lease expired or invalid before lock acquisition at Step 1"
                        )

                    btx.check_deadline()
                    # Step 2: Query Existing Logical Run (FOR UPDATE)
                    cur.execute(
                        """
                        SELECT id, task_id, status, lease_token
                        FROM automation_runs
                        WHERE automation_id = %s
                          AND slot_timestamp = to_timestamp(%s::double precision)
                        FOR UPDATE;
                        """,
                        (automation_id, slot_timestamp),
                    )
                    existing_run = cur.fetchone()

                    # CASE A: Logical Run Already Exists
                    if existing_run and existing_run.get("task_id"):
                        linked_task_id = existing_run["task_id"]
                        cur.execute(
                            "SELECT status FROM tasks WHERE id = %s AND user_id = %s;",
                            (linked_task_id, user_id),
                        )
                        task_row = cur.fetchone()
                        if task_row:
                            task_st = task_row["status"]
                            if task_st in ("completed", "failed"):
                                cur.execute(
                                    """
                                    UPDATE automation_runs
                                    SET status = %s,
                                        reconciled_at = clock_timestamp(),
                                        completed_at = CASE WHEN %s = 'completed' THEN clock_timestamp() ELSE completed_at END
                                    WHERE id = %s;
                                    """,
                                    (task_st, task_st, existing_run["id"]),
                                )

                        cur.execute(
                            """
                            UPDATE automations
                            SET lease_owner = NULL,
                                lease_token = NULL,
                                claimed_at = NULL,
                                lease_expires_at = NULL,
                                last_fired_at = clock_timestamp(),
                                fire_count = fire_count + 1,
                                next_fire_at = to_timestamp(%s::double precision),
                                updated_at = clock_timestamp()
                            WHERE id = %s AND lease_token = %s;
                            """,
                            (next_fire_at, automation_id, lease_token),
                        )
                        btx.commit()
                        return {
                            "id": existing_run["id"],
                            "run_id": existing_run["id"],
                            "task_id": linked_task_id,
                            "status": existing_run["status"],
                            "quota_charged": 0,
                        }

                    btx.check_deadline()
                    # Check if task already exists via idempotency_key
                    cur.execute(
                        "SELECT id FROM tasks WHERE user_id = %s AND idempotency_key = %s;",
                        (user_id, idempotency_key),
                    )
                    existing_task = cur.fetchone()

                    if existing_task:
                        eff_task_id = existing_task["id"]
                        cur.execute(
                            """
                            INSERT INTO automation_runs (
                                id, automation_id, user_id, task_id, status,
                                trigger_timestamp, slot_timestamp, lease_token, created_at
                            )
                            VALUES (
                                %s, %s, %s, %s, 'enqueued',
                                to_timestamp(%s::double precision), to_timestamp(%s::double precision), %s, clock_timestamp()
                            )
                            ON CONFLICT (automation_id, slot_timestamp) DO UPDATE
                            SET task_id = EXCLUDED.task_id,
                                status = 'enqueued',
                                lease_token = EXCLUDED.lease_token,
                                reconciled_at = clock_timestamp()
                            WHERE automation_runs.status IN ('claimed', 'evaluating')
                              AND automation_runs.task_id IS NULL
                            RETURNING id, task_id, status;
                            """,
                            (
                                run_id,
                                automation_id,
                                user_id,
                                eff_task_id,
                                trigger_timestamp,
                                slot_timestamp,
                                lease_token,
                            ),
                        )
                        final_run = cur.fetchone()
                        cur.execute(
                            """
                            UPDATE automations
                            SET lease_owner = NULL,
                                lease_token = NULL,
                                claimed_at = NULL,
                                lease_expires_at = NULL,
                                last_fired_at = clock_timestamp(),
                                fire_count = fire_count + 1,
                                next_fire_at = to_timestamp(%s::double precision),
                                updated_at = clock_timestamp()
                            WHERE id = %s AND lease_token = %s;
                            """,
                            (next_fire_at, automation_id, lease_token),
                        )
                        btx.commit()
                        return {
                            "id": final_run["id"] if final_run else run_id,
                            "run_id": final_run["id"] if final_run else run_id,
                            "task_id": eff_task_id,
                            "status": "enqueued",
                            "quota_charged": 0,
                        }

                    btx.check_deadline()
                    # Step 3: Atomic Quota Reservation
                    cur.execute(
                        """
                        INSERT INTO tenant_hourly_usage (user_id, hour_bucket, run_count)
                        VALUES (%s, date_trunc('hour', clock_timestamp()), 1)
                        ON CONFLICT (user_id, hour_bucket) DO UPDATE
                        SET run_count = tenant_hourly_usage.run_count + 1,
                            updated_at = clock_timestamp()
                        WHERE tenant_hourly_usage.run_count < %s
                        RETURNING run_count;
                        """,
                        (user_id, max_runs_per_hour),
                    )
                    quota_row = cur.fetchone()
                    if not quota_row:
                        cur.execute(
                            """
                            INSERT INTO automation_runs (
                                id, automation_id, user_id, task_id, status,
                                trigger_timestamp, slot_timestamp, lease_token, error_message, created_at
                            )
                            VALUES (
                                %s, %s, %s, NULL, 'skipped',
                                to_timestamp(%s::double precision), to_timestamp(%s::double precision), %s, 'hourly_quota_exhausted', clock_timestamp()
                            )
                            ON CONFLICT (automation_id, slot_timestamp) DO UPDATE
                            SET status = 'skipped',
                                error_message = 'hourly_quota_exhausted',
                                reconciled_at = clock_timestamp()
                            WHERE automation_runs.status IN ('claimed', 'evaluating')
                              AND automation_runs.task_id IS NULL
                            RETURNING id;
                            """,
                            (
                                run_id,
                                automation_id,
                                user_id,
                                trigger_timestamp,
                                slot_timestamp,
                                lease_token,
                            ),
                        )
                        skipped_run = cur.fetchone()
                        cur.execute(
                            """
                            UPDATE automations
                            SET lease_owner = NULL,
                                lease_token = NULL,
                                claimed_at = NULL,
                                lease_expires_at = NULL,
                                last_fired_at = clock_timestamp(),
                                fire_count = fire_count + 1,
                                next_fire_at = to_timestamp(%s::double precision),
                                updated_at = clock_timestamp()
                            WHERE id = %s AND lease_token = %s;
                            """,
                            (next_fire_at, automation_id, lease_token),
                        )
                        btx.commit()
                        if max_runs_per_hour == 0:
                            raise AutomationQuotaExceededError("Hourly quota exhausted (limit=0)")
                        return {
                            "id": skipped_run["id"] if skipped_run else run_id,
                            "run_id": skipped_run["id"] if skipped_run else run_id,
                            "task_id": None,
                            "status": "skipped",
                            "error": "hourly_quota_exhausted",
                            "quota_charged": 0,
                        }

                    btx.check_deadline()
                    # Step 4: Insert M52 Task
                    cur.execute(
                        """
                        INSERT INTO tasks (
                            id, user_id, title, goal, context, status,
                            idempotency_key, timeout_seconds, created_at, updated_at
                        )
                        VALUES (
                            %s, %s, %s, %s, %s::jsonb, 'pending',
                            %s, %s, clock_timestamp(), clock_timestamp()
                        )
                        ON CONFLICT (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
                        RETURNING id;
                        """,
                        (
                            task_id,
                            user_id,
                            title,
                            goal,
                            json.dumps(context_data),
                            idempotency_key,
                            task_timeout,
                        ),
                    )
                    inserted_task = cur.fetchone()
                    eff_task_id = inserted_task["id"] if inserted_task else task_id

                    btx.check_deadline()
                    # Step 5: Fenced Run Finalization
                    cur.execute(
                        """
                        INSERT INTO automation_runs (
                            id, automation_id, user_id, task_id, status,
                            trigger_timestamp, slot_timestamp, lease_token, created_at
                        )
                        VALUES (
                            %s, %s, %s, %s, 'enqueued',
                            to_timestamp(%s::double precision), to_timestamp(%s::double precision), %s, clock_timestamp()
                        )
                        ON CONFLICT (automation_id, slot_timestamp) DO UPDATE
                        SET task_id = EXCLUDED.task_id,
                            status = 'enqueued',
                            lease_token = EXCLUDED.lease_token,
                            reconciled_at = clock_timestamp()
                        WHERE automation_runs.status IN ('claimed', 'evaluating')
                          AND automation_runs.task_id IS NULL
                        RETURNING id, task_id, status;
                        """,
                        (
                            run_id,
                            automation_id,
                            user_id,
                            eff_task_id,
                            trigger_timestamp,
                            slot_timestamp,
                            lease_token,
                        ),
                    )
                    final_run_row = cur.fetchone()

                    btx.check_deadline()
                    # Step 6: Advance Schedule and Release Lease
                    cur.execute(
                        """
                        UPDATE automations
                        SET lease_owner = NULL,
                            lease_token = NULL,
                            claimed_at = NULL,
                            lease_expires_at = NULL,
                            last_fired_at = clock_timestamp(),
                            fire_count = fire_count + 1,
                            next_fire_at = to_timestamp(%s::double precision),
                            updated_at = clock_timestamp()
                        WHERE id = %s AND lease_token = %s;
                        """,
                        (next_fire_at, automation_id, lease_token),
                    )

                    btx.commit()
                    return {
                        "id": final_run_row["id"] if final_run_row else run_id,
                        "run_id": final_run_row["id"] if final_run_row else run_id,
                        "task_id": eff_task_id,
                        "status": "enqueued",
                        "quota_charged": 1,
                    }

    def get_run(self, run_id: str, user_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, automation_id, user_id, task_id, status,
                           EXTRACT(EPOCH FROM trigger_timestamp) as trigger_timestamp,
                           EXTRACT(EPOCH FROM slot_timestamp) as slot_timestamp,
                           lease_token, condition_evaluation, error_message,
                           EXTRACT(EPOCH FROM reconciled_at) as reconciled_at,
                           EXTRACT(EPOCH FROM created_at) as created_at,
                           EXTRACT(EPOCH FROM completed_at) as completed_at
                    FROM automation_runs
                    WHERE id = %s AND user_id = %s;
                    """,
                    (run_id, user_id),
                )
                return self._format_run_row(cur.fetchone())

    def get_run_by_slot(self, automation_id: str, slot_timestamp: float) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, automation_id, user_id, task_id, status,
                           EXTRACT(EPOCH FROM trigger_timestamp) as trigger_timestamp,
                           EXTRACT(EPOCH FROM slot_timestamp) as slot_timestamp,
                           lease_token, condition_evaluation, error_message,
                           EXTRACT(EPOCH FROM reconciled_at) as reconciled_at,
                           EXTRACT(EPOCH FROM created_at) as created_at,
                           EXTRACT(EPOCH FROM completed_at) as completed_at
                    FROM automation_runs
                    WHERE automation_id = %s AND slot_timestamp = to_timestamp(%s::double precision);
                    """,
                    (automation_id, slot_timestamp),
                )
                return self._format_run_row(cur.fetchone())

    def list_runs(
        self,
        automation_id: str,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, automation_id, user_id, task_id, status,
                           EXTRACT(EPOCH FROM trigger_timestamp) as trigger_timestamp,
                           EXTRACT(EPOCH FROM slot_timestamp) as slot_timestamp,
                           lease_token, condition_evaluation, error_message,
                           EXTRACT(EPOCH FROM reconciled_at) as reconciled_at,
                           EXTRACT(EPOCH FROM created_at) as created_at,
                           EXTRACT(EPOCH FROM completed_at) as completed_at
                    FROM automation_runs
                    WHERE automation_id = %s AND user_id = %s
                    ORDER BY slot_timestamp DESC
                    LIMIT %s OFFSET %s;
                    """,
                    (automation_id, user_id, limit, offset),
                )
                return [self._format_run_row(r) for r in cur.fetchall() if r]  # type: ignore

    def record_run_terminal_state(
        self,
        run_id: str,
        status: str,
        error_message: str | None = None,
        condition_evaluation: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> bool:
        cond_json = json.dumps(condition_evaluation) if condition_evaluation else None
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE automation_runs
                    SET status = %s,
                        error_message = COALESCE(%s, error_message),
                        condition_evaluation = COALESCE(%s::jsonb, condition_evaluation),
                        task_id = COALESCE(%s, task_id),
                        reconciled_at = clock_timestamp(),
                        completed_at = CASE WHEN %s IN ('completed', 'failed', 'cancelled', 'skipped') THEN clock_timestamp() ELSE completed_at END
                    WHERE id = %s
                    RETURNING id;
                    """,
                    (status, error_message, cond_json, task_id, status, run_id),
                )
                return cur.fetchone() is not None

    def reconcile_run_task_status(
        self,
        run_id: str,
        task_status: str,
        error_message: str | None = None,
    ) -> bool:
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE automation_runs
                    SET status = %s,
                        error_message = COALESCE(%s, error_message),
                        reconciled_at = clock_timestamp(),
                        completed_at = CASE WHEN %s IN ('completed', 'failed', 'cancelled') THEN clock_timestamp() ELSE completed_at END
                    WHERE id = %s
                    RETURNING id;
                    """,
                    (task_status, error_message, task_status, run_id),
                )
                return cur.fetchone() is not None
