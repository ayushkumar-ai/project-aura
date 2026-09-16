"""M52 — PostgreSQL Task Repository Implementation for Project AURA.

Provides durable relational task and step lifecycle persistence backed by PostgreSQL 16,
enforcing multi-tenant isolation, atomic task acquisition with SELECT ... FOR UPDATE SKIP LOCKED,
and idempotent submissions.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from uuid import uuid4

from core.database import DatabaseConnectionPool
from core.repositories.base import BaseTaskRepository

logger = logging.getLogger("aura.repositories.postgres_task")


class PostgresTaskRepository(BaseTaskRepository):
    """PostgreSQL task and step repository enforcing strict tenant isolation."""

    def __init__(self, pool: DatabaseConnectionPool) -> None:
        self.pool = pool

    def create_task(
        self,
        user_id: str,
        title: str,
        goal: str,
        context: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        timeout_seconds: int = 600,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new task record or return existing if idempotency key matches."""
        eff_task_id = (task_id or str(uuid4())).strip()
        eff_context = context or {}

        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                # Idempotency check if key provided
                if idempotency_key and idempotency_key.strip():
                    cur.execute(
                        """
                        SELECT id, user_id, title, goal, context, status, result, error_message,
                               idempotency_key, timeout_seconds,
                               EXTRACT(EPOCH FROM created_at) as created_at,
                               EXTRACT(EPOCH FROM started_at) as started_at,
                               EXTRACT(EPOCH FROM completed_at) as completed_at,
                               EXTRACT(EPOCH FROM updated_at) as updated_at
                        FROM tasks
                        WHERE user_id = %s AND idempotency_key = %s;
                        """,
                        (user_id, idempotency_key.strip()),
                    )
                    existing = cur.fetchone()
                    if existing:
                        return self._format_task_row(existing)

                cur.execute(
                    """
                    INSERT INTO tasks (
                        id, user_id, title, goal, context, status,
                        idempotency_key, timeout_seconds
                    )
                    VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s)
                    RETURNING id, user_id, title, goal, context, status, result, error_message,
                              idempotency_key, timeout_seconds,
                              EXTRACT(EPOCH FROM created_at) as created_at,
                              EXTRACT(EPOCH FROM started_at) as started_at,
                              EXTRACT(EPOCH FROM completed_at) as completed_at,
                              EXTRACT(EPOCH FROM updated_at) as updated_at;
                    """,
                    (
                        eff_task_id,
                        user_id,
                        title.strip(),
                        goal.strip(),
                        json.dumps(eff_context),
                        idempotency_key.strip() if idempotency_key else None,
                        int(timeout_seconds),
                    ),
                )
                row = cur.fetchone()
                return self._format_task_row(row)

    def get_task(self, task_id: str, user_id: str) -> dict[str, Any] | None:
        """Retrieve task by ID strictly owned by user_id."""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, user_id, title, goal, context, status, result, error_message,
                           idempotency_key, timeout_seconds,
                           EXTRACT(EPOCH FROM created_at) as created_at,
                           EXTRACT(EPOCH FROM started_at) as started_at,
                           EXTRACT(EPOCH FROM completed_at) as completed_at,
                           EXTRACT(EPOCH FROM updated_at) as updated_at
                    FROM tasks
                    WHERE id = %s AND user_id = %s;
                    """,
                    (task_id.strip(), user_id),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return self._format_task_row(row)

    def list_tasks(
        self,
        user_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List tasks owned by user_id with optional status filter."""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                if status and status.strip():
                    cur.execute(
                        """
                        SELECT id, user_id, title, goal, context, status, result, error_message,
                               idempotency_key, timeout_seconds,
                               EXTRACT(EPOCH FROM created_at) as created_at,
                               EXTRACT(EPOCH FROM started_at) as started_at,
                               EXTRACT(EPOCH FROM completed_at) as completed_at,
                               EXTRACT(EPOCH FROM updated_at) as updated_at
                        FROM tasks
                        WHERE user_id = %s AND status = %s
                        ORDER BY created_at DESC
                        LIMIT %s OFFSET %s;
                        """,
                        (user_id, status.strip(), limit, offset),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, user_id, title, goal, context, status, result, error_message,
                               idempotency_key, timeout_seconds,
                               EXTRACT(EPOCH FROM created_at) as created_at,
                               EXTRACT(EPOCH FROM started_at) as started_at,
                               EXTRACT(EPOCH FROM completed_at) as completed_at,
                               EXTRACT(EPOCH FROM updated_at) as updated_at
                        FROM tasks
                        WHERE user_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s OFFSET %s;
                        """,
                        (user_id, limit, offset),
                    )
                rows = cur.fetchall()
                return [self._format_task_row(r) for r in rows]

    def update_task_status(
        self,
        task_id: str,
        user_id: str,
        status: str,
        error_message: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> bool:
        """Update task lifecycle status, error, and result payload."""
        status_norm = status.strip().lower()
        res_json = json.dumps(result) if result is not None else None

        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                if status_norm == "running":
                    cur.execute(
                        """
                        UPDATE tasks
                        SET status = %s,
                            started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s AND user_id = %s;
                        """,
                        (status_norm, task_id.strip(), user_id),
                    )
                elif status_norm in ("completed", "failed", "cancelled", "timed_out"):
                    cur.execute(
                        """
                        UPDATE tasks
                        SET status = %s,
                            error_message = COALESCE(%s, error_message),
                            result = COALESCE(%s::jsonb, result),
                            completed_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s AND user_id = %s;
                        """,
                        (status_norm, error_message, res_json, task_id.strip(), user_id),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE tasks
                        SET status = %s,
                            error_message = COALESCE(%s, error_message),
                            result = COALESCE(%s::jsonb, result),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s AND user_id = %s;
                        """,
                        (status_norm, error_message, res_json, task_id.strip(), user_id),
                    )
                return cur.rowcount > 0

    def acquire_next_pending_task(
        self,
        worker_id: str = "default",
        lock_timeout_seconds: int = 600,
    ) -> dict[str, Any] | None:
        """Atomically acquire the next pending task using SELECT FOR UPDATE SKIP LOCKED."""
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH next_task AS (
                        SELECT id FROM tasks
                        WHERE status = 'pending'
                        ORDER BY created_at ASC
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE tasks
                    SET status = 'running',
                        started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                        updated_at = CURRENT_TIMESTAMP
                    FROM next_task
                    WHERE tasks.id = next_task.id
                    RETURNING tasks.id, tasks.user_id, tasks.title, tasks.goal, tasks.context,
                              tasks.status, tasks.result, tasks.error_message, tasks.idempotency_key,
                              tasks.timeout_seconds,
                              EXTRACT(EPOCH FROM tasks.created_at) as created_at,
                              EXTRACT(EPOCH FROM tasks.started_at) as started_at,
                              EXTRACT(EPOCH FROM tasks.completed_at) as completed_at,
                              EXTRACT(EPOCH FROM tasks.updated_at) as updated_at;
                    """
                )
                row = cur.fetchone()
                if not row:
                    return None
                return self._format_task_row(row)

    def create_or_update_step(
        self,
        task_id: str,
        user_id: str,
        step_index: int,
        name: str,
        status: str,
        tool_name: str | None = None,
        tool_input: dict[str, Any] | None = None,
        tool_output: dict[str, Any] | None = None,
        error_message: str | None = None,
        step_id: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a task execution step record."""
        eff_step_id = (step_id or str(uuid4())).strip()
        tin_json = json.dumps(tool_input) if tool_input is not None else None
        tout_json = json.dumps(tool_output) if tool_output is not None else None
        status_norm = status.strip().lower()

        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO task_steps (
                        id, task_id, user_id, step_index, name, status,
                        tool_name, tool_input, tool_output, error_message,
                        started_at, completed_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s::jsonb, %s::jsonb, %s,
                        CASE WHEN %s = 'running' THEN CURRENT_TIMESTAMP ELSE NULL END,
                        CASE WHEN %s IN ('completed', 'failed', 'skipped') THEN CURRENT_TIMESTAMP ELSE NULL END
                    )
                    ON CONFLICT (task_id, step_index) DO UPDATE
                    SET status = EXCLUDED.status,
                        tool_name = COALESCE(EXCLUDED.tool_name, task_steps.tool_name),
                        tool_input = COALESCE(EXCLUDED.tool_input, task_steps.tool_input),
                        tool_output = COALESCE(EXCLUDED.tool_output, task_steps.tool_output),
                        error_message = COALESCE(EXCLUDED.error_message, task_steps.error_message),
                        started_at = CASE 
                            WHEN EXCLUDED.status = 'running' THEN COALESCE(task_steps.started_at, CURRENT_TIMESTAMP)
                            ELSE task_steps.started_at 
                        END,
                        completed_at = CASE 
                            WHEN EXCLUDED.status IN ('completed', 'failed', 'skipped') THEN CURRENT_TIMESTAMP
                            ELSE task_steps.completed_at 
                        END
                    RETURNING id, task_id, user_id, step_index, name, status, tool_name,
                              tool_input, tool_output, error_message,
                              EXTRACT(EPOCH FROM started_at) as started_at,
                              EXTRACT(EPOCH FROM completed_at) as completed_at,
                              EXTRACT(EPOCH FROM created_at) as created_at;
                    """,
                    (
                        eff_step_id,
                        task_id.strip(),
                        user_id,
                        int(step_index),
                        name.strip(),
                        status_norm,
                        tool_name,
                        tin_json,
                        tout_json,
                        error_message,
                        status_norm,
                        status_norm,
                    ),
                )
                row = cur.fetchone()
                return self._format_step_row(row)

    def get_steps(self, task_id: str, user_id: str) -> list[dict[str, Any]]:
        """Retrieve all steps for a task in step_index order."""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, task_id, user_id, step_index, name, status, tool_name,
                           tool_input, tool_output, error_message,
                           EXTRACT(EPOCH FROM started_at) as started_at,
                           EXTRACT(EPOCH FROM completed_at) as completed_at,
                           EXTRACT(EPOCH FROM created_at) as created_at
                    FROM task_steps
                    WHERE task_id = %s AND user_id = %s
                    ORDER BY step_index ASC;
                    """,
                    (task_id.strip(), user_id),
                )
                rows = cur.fetchall()
                return [self._format_step_row(r) for r in rows]

    def cancel_task(self, task_id: str, user_id: str) -> bool:
        """Cancel an in-flight or pending task."""
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE tasks
                    SET status = 'cancelled',
                        completed_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND user_id = %s AND status IN ('pending', 'running', 'awaiting_approval');
                    """,
                    (task_id.strip(), user_id),
                )
                return cur.rowcount > 0

    def recover_stale_tasks(self, stale_threshold_seconds: float = 300.0) -> list[str]:
        """Identify and recover stale tasks stuck in running status."""
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE tasks
                    SET status = 'failed',
                        error_message = 'Worker crashed during execution',
                        completed_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE status = 'running'
                      AND updated_at < (CURRENT_TIMESTAMP - INTERVAL '1 second' * %s)
                    RETURNING id;
                    """,
                    (float(stale_threshold_seconds),),
                )
                rows = cur.fetchall()
                recovered = [r["id"] for r in rows]
                if recovered:
                    logger.warning(f"Recovered {len(recovered)} stale orphaned tasks: {recovered}")
                return recovered

    @staticmethod
    def _format_task_row(row: dict[str, Any]) -> dict[str, Any]:
        """Parse JSON fields and format standardized task dictionary."""
        ctx = row.get("context")
        if isinstance(ctx, str):
            try:
                ctx = json.loads(ctx)
            except Exception:
                ctx = {}
        elif not isinstance(ctx, dict):
            ctx = {}

        res = row.get("result")
        if isinstance(res, str):
            try:
                res = json.loads(res)
            except Exception:
                pass

        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "title": row["title"],
            "goal": row["goal"],
            "context": ctx,
            "status": row["status"],
            "result": res,
            "error_message": row.get("error_message"),
            "idempotency_key": row.get("idempotency_key"),
            "timeout_seconds": row.get("timeout_seconds", 600),
            "created_at": row.get("created_at"),
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
            "updated_at": row.get("updated_at"),
        }

    @staticmethod
    def _format_step_row(row: dict[str, Any]) -> dict[str, Any]:
        """Parse JSON fields and format standardized step dictionary."""
        tin = row.get("tool_input")
        if isinstance(tin, str):
            try:
                tin = json.loads(tin)
            except Exception:
                tin = {}
        tout = row.get("tool_output")
        if isinstance(tout, str):
            try:
                tout = json.loads(tout)
            except Exception:
                pass

        return {
            "id": row["id"],
            "task_id": row["task_id"],
            "user_id": row["user_id"],
            "step_index": row["step_index"],
            "name": row["name"],
            "status": row["status"],
            "tool_name": row.get("tool_name"),
            "tool_input": tin,
            "tool_output": tout,
            "error_message": row.get("error_message"),
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
            "created_at": row.get("created_at"),
        }
