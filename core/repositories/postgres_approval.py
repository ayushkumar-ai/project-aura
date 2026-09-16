"""M52 — PostgreSQL Approval Repository Implementation for Project AURA.

Provides durable human approval request persistence, cryptographic nonce validation,
atomic single-use decisions, and automatic task resumption gating.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any
from uuid import uuid4

from core.database import DatabaseConnectionPool
from core.repositories.base import BaseApprovalRepository

logger = logging.getLogger("aura.repositories.postgres_approval")


class PostgresApprovalRepository(BaseApprovalRepository):
    """PostgreSQL human approval repository with cryptographic nonce verification."""

    def __init__(self, pool: DatabaseConnectionPool) -> None:
        self.pool = pool

    def create_approval(
        self,
        task_id: str,
        user_id: str,
        action_type: str,
        action_payload: dict[str, Any],
        justification: str,
        step_id: str | None = None,
        risk_level: str = "medium",
        expires_in_seconds: int = 1800,
        nonce: str | None = None,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new human approval request with a cryptographic nonce."""
        eff_approval_id = (approval_id or str(uuid4())).strip()
        eff_nonce = nonce.strip() if nonce else secrets.token_urlsafe(32)
        eff_payload = json.dumps(action_payload or {})
        risk_norm = risk_level.strip().lower() if risk_level else "medium"

        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO approval_requests (
                        id, task_id, step_id, user_id, action_type,
                        action_payload, risk_level, justification, status,
                        nonce, expires_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s,
                        %s::jsonb, %s, %s, 'pending',
                        %s, CURRENT_TIMESTAMP + INTERVAL '1 second' * %s
                    )
                    RETURNING id, task_id, step_id, user_id, action_type,
                              action_payload, risk_level, justification, status,
                              nonce, decision_reason,
                              EXTRACT(EPOCH FROM expires_at) as expires_at,
                              EXTRACT(EPOCH FROM decided_at) as decided_at,
                              EXTRACT(EPOCH FROM created_at) as created_at;
                    """,
                    (
                        eff_approval_id,
                        task_id.strip(),
                        step_id.strip() if step_id else None,
                        user_id,
                        action_type.strip(),
                        eff_payload,
                        risk_norm,
                        justification.strip(),
                        eff_nonce,
                        int(expires_in_seconds),
                    ),
                )
                row = cur.fetchone()
                return self._format_approval_row(row)

    def get_approval(self, approval_id: str, user_id: str) -> dict[str, Any] | None:
        """Fetch approval request strictly owned by user_id."""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, task_id, step_id, user_id, action_type,
                           action_payload, risk_level, justification, status,
                           nonce, decision_reason,
                           EXTRACT(EPOCH FROM expires_at) as expires_at,
                           EXTRACT(EPOCH FROM decided_at) as decided_at,
                           EXTRACT(EPOCH FROM created_at) as created_at
                    FROM approval_requests
                    WHERE id = %s AND user_id = %s;
                    """,
                    (approval_id.strip(), user_id),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return self._format_approval_row(row)

    def list_pending_approvals(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """List active unexpired approval requests for a user."""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, task_id, step_id, user_id, action_type,
                           action_payload, risk_level, justification, status,
                           nonce, decision_reason,
                           EXTRACT(EPOCH FROM expires_at) as expires_at,
                           EXTRACT(EPOCH FROM decided_at) as decided_at,
                           EXTRACT(EPOCH FROM created_at) as created_at
                    FROM approval_requests
                    WHERE user_id = %s AND status = 'pending' AND expires_at > CURRENT_TIMESTAMP
                    ORDER BY created_at DESC
                    LIMIT %s;
                    """,
                    (user_id, limit),
                )
                rows = cur.fetchall()
                return [self._format_approval_row(r) for r in rows]

    def get_approval_by_task(
        self,
        task_id: str,
        user_id: str,
        step_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Find approval request associated with task and optional step."""
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                if step_id:
                    cur.execute(
                        """
                        SELECT id, task_id, step_id, user_id, action_type,
                               action_payload, risk_level, justification, status,
                               nonce, decision_reason,
                               EXTRACT(EPOCH FROM expires_at) as expires_at,
                               EXTRACT(EPOCH FROM decided_at) as decided_at,
                               EXTRACT(EPOCH FROM created_at) as created_at
                        FROM approval_requests
                        WHERE task_id = %s AND user_id = %s AND step_id = %s
                        ORDER BY created_at DESC LIMIT 1;
                        """,
                        (task_id.strip(), user_id, step_id.strip()),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, task_id, step_id, user_id, action_type,
                               action_payload, risk_level, justification, status,
                               nonce, decision_reason,
                               EXTRACT(EPOCH FROM expires_at) as expires_at,
                               EXTRACT(EPOCH FROM decided_at) as decided_at,
                               EXTRACT(EPOCH FROM created_at) as created_at
                        FROM approval_requests
                        WHERE task_id = %s AND user_id = %s
                        ORDER BY created_at DESC LIMIT 1;
                        """,
                        (task_id.strip(), user_id),
                    )
                row = cur.fetchone()
                if not row:
                    return None
                return self._format_approval_row(row)

    def decide_approval(
        self,
        approval_id: str,
        user_id: str,
        decision: str,
        nonce: str,
        reason: str = "",
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """Atomically record decision on approval request with nonce check."""
        dec_norm = decision.strip().lower()
        if dec_norm not in ("approved", "rejected"):
            return False, f"Invalid decision '{decision}'. Must be 'approved' or 'rejected'.", None

        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, task_id, step_id, user_id, action_type,
                           action_payload, risk_level, justification, status,
                           nonce, decision_reason,
                           EXTRACT(EPOCH FROM expires_at) as expires_at,
                           EXTRACT(EPOCH FROM decided_at) as decided_at,
                           EXTRACT(EPOCH FROM created_at) as created_at
                    FROM approval_requests
                    WHERE id = %s AND user_id = %s
                    FOR UPDATE;
                    """,
                    (approval_id.strip(), user_id),
                )
                row = cur.fetchone()
                if not row:
                    return False, "Approval request not found.", None

                if row["status"] != "pending":
                    return False, f"Approval request has already been decided ({row['status']}).", self._format_approval_row(row)

                if row["expires_at"] <= time.time():
                    cur.execute(
                        """
                        UPDATE approval_requests
                        SET status = 'expired'
                        WHERE id = %s;
                        """,
                        (approval_id.strip(),),
                    )
                    row["status"] = "expired"
                    return False, "Approval request has expired.", self._format_approval_row(row)

                # Validate cryptographic nonce
                if not secrets.compare_digest(row["nonce"], nonce.strip()):
                    logger.warning(f"Invalid nonce provided for approval {approval_id}")
                    return False, "Invalid approval nonce.", None

                # Apply decision
                cur.execute(
                    """
                    UPDATE approval_requests
                    SET status = %s,
                        decision_reason = %s,
                        decided_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    RETURNING id, task_id, step_id, user_id, action_type,
                              action_payload, risk_level, justification, status,
                              nonce, decision_reason,
                              EXTRACT(EPOCH FROM expires_at) as expires_at,
                              EXTRACT(EPOCH FROM decided_at) as decided_at,
                              EXTRACT(EPOCH FROM created_at) as created_at;
                    """,
                    (dec_norm, reason.strip(), approval_id.strip()),
                )
                updated_row = cur.fetchone()

                # Update associated task status
                task_id = updated_row["task_id"]
                if dec_norm == "approved":
                    cur.execute(
                        """
                        UPDATE tasks
                        SET status = 'pending',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s AND status = 'awaiting_approval';
                        """,
                        (task_id,),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE tasks
                        SET status = 'failed',
                            error_message = %s,
                            completed_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s AND status = 'awaiting_approval';
                        """,
                        (f"Approval rejected: {reason.strip()}", task_id),
                    )

                return True, f"Approval successfully {dec_norm}.", self._format_approval_row(updated_row)

    def expire_stale_approvals(self) -> list[str]:
        """Mark expired pending approvals as expired."""
        with self.pool.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE approval_requests
                    SET status = 'expired'
                    WHERE status = 'pending' AND expires_at <= CURRENT_TIMESTAMP
                    RETURNING id, task_id;
                    """
                )
                rows = cur.fetchall()
                expired = [r["id"] for r in rows]
                for r in rows:
                    cur.execute(
                        """
                        UPDATE tasks
                        SET status = 'timed_out',
                            error_message = 'Approval request expired without decision',
                            completed_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s AND status = 'awaiting_approval';
                        """,
                        (r["task_id"],),
                    )
                return expired

    @staticmethod
    def _format_approval_row(row: dict[str, Any]) -> dict[str, Any]:
        """Parse JSON fields and format standardized approval dictionary."""
        payload = row.get("action_payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        elif not isinstance(payload, dict):
            payload = {}

        return {
            "id": row["id"],
            "task_id": row["task_id"],
            "step_id": row.get("step_id"),
            "user_id": row["user_id"],
            "action_type": row["action_type"],
            "action_payload": payload,
            "risk_level": row.get("risk_level", "medium"),
            "justification": row.get("justification", ""),
            "status": row["status"],
            "nonce": row["nonce"],
            "decision_reason": row.get("decision_reason"),
            "expires_at": row.get("expires_at"),
            "decided_at": row.get("decided_at"),
            "created_at": row.get("created_at"),
        }
