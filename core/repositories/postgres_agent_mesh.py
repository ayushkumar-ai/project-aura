"""M59 — PostgreSQL Agent Mesh Repository Implementation.

Provides production-grade PostgreSQL 16 persistence for migration 010.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from core.agent_mesh.types import (
    ActionType,
    AgentDelegation,
    AgentMeshAudit,
    AgentMeshEvent,
    AgentPhase,
    AgentRole,
    AgentRun,
    AgentRunBudget,
    AgentRunStatus,
    AgentRunStep,
    VerificationStatus,
)
from core.database import DatabaseConnectionPool
from core.platform.types import CapabilityRiskLevel
from core.repositories.base_agent_mesh import BaseAgentMeshRepository

logger = logging.getLogger("aura.repositories.postgres_agent_mesh")


def _to_timestamp(val: Any) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, datetime):
        return val.replace(tzinfo=timezone.utc).timestamp()
    return 0.0


def _json_dumps(val: Any) -> str:
    try:
        return json.dumps(val)
    except Exception:
        return "{}"


def _json_loads(val: Any) -> Any:
    if val is None:
        return {}
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return {}
    return {}


class PostgresAgentMeshRepository(BaseAgentMeshRepository):
    """PostgreSQL 16 persistence for agent runs, steps, delegations, events, and audits."""

    def __init__(self, db_pool: DatabaseConnectionPool):
        self.db_pool = db_pool

    # 1. Agent Runs
    def save_run(self, run: AgentRun) -> AgentRun:
        sql = """
        INSERT INTO agent_runs (
            run_id, tenant_id, user_id, parent_run_id, correlation_id,
            causation_id, intent, status, current_phase, depth,
            budget, iteration_count, tool_call_count, provider_call_count,
            token_usage, cost_estimate, final_outcome, error_detail,
            created_at, started_at, completed_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            TO_TIMESTAMP(%s),
            CASE WHEN %s::FLOAT IS NULL THEN NULL ELSE TO_TIMESTAMP(%s) END,
            CASE WHEN %s::FLOAT IS NULL THEN NULL ELSE TO_TIMESTAMP(%s) END
        )
        ON CONFLICT (run_id) DO UPDATE SET
            status = EXCLUDED.status,
            current_phase = EXCLUDED.current_phase,
            iteration_count = EXCLUDED.iteration_count,
            tool_call_count = EXCLUDED.tool_call_count,
            provider_call_count = EXCLUDED.provider_call_count,
            token_usage = EXCLUDED.token_usage,
            cost_estimate = EXCLUDED.cost_estimate,
            final_outcome = EXCLUDED.final_outcome,
            error_detail = EXCLUDED.error_detail,
            started_at = COALESCE(agent_runs.started_at, EXCLUDED.started_at),
            completed_at = EXCLUDED.completed_at
        RETURNING run_id, tenant_id, user_id, parent_run_id, correlation_id,
                  causation_id, intent, status, current_phase, depth,
                  budget, iteration_count, tool_call_count, provider_call_count,
                  token_usage, cost_estimate, final_outcome, error_detail,
                  EXTRACT(EPOCH FROM created_at) AS created_at,
                  EXTRACT(EPOCH FROM started_at) AS started_at,
                  EXTRACT(EPOCH FROM completed_at) AS completed_at;
        """
        params = (
            run.run_id,
            run.tenant_id,
            run.user_id,
            run.parent_run_id,
            run.correlation_id,
            run.causation_id,
            run.intent,
            run.status.value,
            run.current_phase.value,
            run.depth,
            _json_dumps(run.budget.to_dict()),
            run.iteration_count,
            run.tool_call_count,
            run.provider_call_count,
            _json_dumps(run.token_usage),
            run.cost_estimate,
            _json_dumps(run.final_outcome),
            run.error_detail,
            run.created_at,
            run.started_at, run.started_at,
            run.completed_at, run.completed_at,
        )
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                return self._row_to_run(row)

    def get_run(self, run_id: str, tenant_id: str) -> AgentRun | None:
        sql = """
        SELECT run_id, tenant_id, user_id, parent_run_id, correlation_id,
               causation_id, intent, status, current_phase, depth,
               budget, iteration_count, tool_call_count, provider_call_count,
               token_usage, cost_estimate, final_outcome, error_detail,
               EXTRACT(EPOCH FROM created_at) AS created_at,
               EXTRACT(EPOCH FROM started_at) AS started_at,
               EXTRACT(EPOCH FROM completed_at) AS completed_at
        FROM agent_runs
        WHERE run_id = %s AND tenant_id = %s;
        """
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (run_id, tenant_id))
                row = cur.fetchone()
                return self._row_to_run(row) if row else None

    def list_runs(self, tenant_id: str, limit: int = 50) -> list[AgentRun]:
        sql = """
        SELECT run_id, tenant_id, user_id, parent_run_id, correlation_id,
               causation_id, intent, status, current_phase, depth,
               budget, iteration_count, tool_call_count, provider_call_count,
               token_usage, cost_estimate, final_outcome, error_detail,
               EXTRACT(EPOCH FROM created_at) AS created_at,
               EXTRACT(EPOCH FROM started_at) AS started_at,
               EXTRACT(EPOCH FROM completed_at) AS completed_at
        FROM agent_runs
        WHERE tenant_id = %s
        ORDER BY created_at DESC
        LIMIT %s;
        """
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (tenant_id, limit))
                rows = cur.fetchall()
                return [self._row_to_run(r) for r in rows]

    def update_run_status(
        self,
        run_id: str,
        tenant_id: str,
        status: AgentRunStatus,
        error_detail: str | None = None,
    ) -> AgentRun | None:
        sql = """
        UPDATE agent_runs
        SET status = %s,
            error_detail = COALESCE(%s, error_detail),
            completed_at = CASE WHEN %s IN ('completed', 'failed', 'timed_out', 'cancelled', 'unknown') THEN CURRENT_TIMESTAMP ELSE completed_at END
        WHERE run_id = %s AND tenant_id = %s
        RETURNING run_id, tenant_id, user_id, parent_run_id, correlation_id,
                  causation_id, intent, status, current_phase, depth,
                  budget, iteration_count, tool_call_count, provider_call_count,
                  token_usage, cost_estimate, final_outcome, error_detail,
                  EXTRACT(EPOCH FROM created_at) AS created_at,
                  EXTRACT(EPOCH FROM started_at) AS started_at,
                  EXTRACT(EPOCH FROM completed_at) AS completed_at;
        """
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (status.value, error_detail, status.value, run_id, tenant_id))
                row = cur.fetchone()
                return self._row_to_run(row) if row else None

    def delete_run(self, run_id: str, tenant_id: str) -> bool:
        sql = "DELETE FROM agent_runs WHERE run_id = %s AND tenant_id = %s;"
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (run_id, tenant_id))
                return cur.rowcount > 0

    # 2. Agent Steps
    def save_step(self, step: AgentRunStep) -> AgentRunStep:
        sql = """
        INSERT INTO agent_run_steps (
            step_id, run_id, tenant_id, step_number, phase,
            plan_action, action_type, parameters, result, status,
            verification_status, approval_token, duration_ms,
            created_at, completed_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s,
            TO_TIMESTAMP(%s),
            CASE WHEN %s::FLOAT IS NULL THEN NULL ELSE TO_TIMESTAMP(%s) END
        )
        RETURNING step_id, run_id, tenant_id, step_number, phase,
                  plan_action, action_type, parameters, result, status,
                  verification_status, approval_token, duration_ms,
                  EXTRACT(EPOCH FROM created_at) AS created_at,
                  EXTRACT(EPOCH FROM completed_at) AS completed_at;
        """
        params = (
            step.step_id,
            step.run_id,
            step.tenant_id,
            step.step_number,
            step.phase.value,
            step.plan_action,
            step.action_type.value,
            _json_dumps(step.parameters),
            _json_dumps(step.result),
            step.status,
            step.verification_status.value,
            step.approval_token,
            step.duration_ms,
            step.created_at,
            step.completed_at, step.completed_at,
        )
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                return self._row_to_step(row)

    def list_steps(self, run_id: str, tenant_id: str) -> list[AgentRunStep]:
        sql = """
        SELECT step_id, run_id, tenant_id, step_number, phase,
               plan_action, action_type, parameters, result, status,
               verification_status, approval_token, duration_ms,
               EXTRACT(EPOCH FROM created_at) AS created_at,
               EXTRACT(EPOCH FROM completed_at) AS completed_at
        FROM agent_run_steps
        WHERE run_id = %s AND tenant_id = %s
        ORDER BY step_number ASC;
        """
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (run_id, tenant_id))
                rows = cur.fetchall()
                return [self._row_to_step(r) for r in rows]

    # 3. Delegations
    def save_delegation(self, delegation: AgentDelegation) -> AgentDelegation:
        sql = """
        INSERT INTO agent_delegations (
            delegation_id, parent_run_id, child_run_id, tenant_id,
            role, capabilities, budget_allocated, status,
            created_at, completed_at
        ) VALUES (
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            TO_TIMESTAMP(%s),
            CASE WHEN %s::FLOAT IS NULL THEN NULL ELSE TO_TIMESTAMP(%s) END
        )
        RETURNING delegation_id, parent_run_id, child_run_id, tenant_id,
                  role, capabilities, budget_allocated, status,
                  EXTRACT(EPOCH FROM created_at) AS created_at,
                  EXTRACT(EPOCH FROM completed_at) AS completed_at;
        """
        params = (
            delegation.delegation_id,
            delegation.parent_run_id,
            delegation.child_run_id,
            delegation.tenant_id,
            delegation.role.value,
            _json_dumps(delegation.capabilities),
            _json_dumps(delegation.budget_allocated.to_dict()),
            delegation.status,
            delegation.created_at,
            delegation.completed_at, delegation.completed_at,
        )
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                return self._row_to_delegation(row)

    def list_delegations(self, parent_run_id: str, tenant_id: str) -> list[AgentDelegation]:
        sql = """
        SELECT delegation_id, parent_run_id, child_run_id, tenant_id,
               role, capabilities, budget_allocated, status,
               EXTRACT(EPOCH FROM created_at) AS created_at,
               EXTRACT(EPOCH FROM completed_at) AS completed_at
        FROM agent_delegations
        WHERE parent_run_id = %s AND tenant_id = %s
        ORDER BY created_at ASC;
        """
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (parent_run_id, tenant_id))
                rows = cur.fetchall()
                return [self._row_to_delegation(r) for r in rows]

    # 4. Events
    def save_event(self, event: AgentMeshEvent) -> AgentMeshEvent:
        sql = """
        INSERT INTO agent_mesh_events (
            event_id, run_id, tenant_id, event_type, phase, data, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, TO_TIMESTAMP(%s))
        RETURNING event_id, run_id, tenant_id, event_type, phase, data,
                  EXTRACT(EPOCH FROM created_at) AS created_at;
        """
        params = (
            event.event_id,
            event.run_id,
            event.tenant_id,
            event.event_type,
            event.phase.value,
            _json_dumps(event.data),
            event.created_at,
        )
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                return AgentMeshEvent(
                    event_id=str(row["event_id"]),
                    run_id=str(row["run_id"]),
                    tenant_id=str(row["tenant_id"]),
                    event_type=str(row["event_type"]),
                    phase=AgentPhase(row["phase"]),
                    data=_json_loads(row.get("data")),
                    created_at=_to_timestamp(row.get("created_at")),
                )

    def list_events(self, run_id: str, tenant_id: str) -> list[AgentMeshEvent]:
        sql = """
        SELECT event_id, run_id, tenant_id, event_type, phase, data,
               EXTRACT(EPOCH FROM created_at) AS created_at
        FROM agent_mesh_events
        WHERE run_id = %s AND tenant_id = %s
        ORDER BY created_at ASC;
        """
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (run_id, tenant_id))
                rows = cur.fetchall()
                return [
                    AgentMeshEvent(
                        event_id=str(r["event_id"]),
                        run_id=str(r["run_id"]),
                        tenant_id=str(r["tenant_id"]),
                        event_type=str(r["event_type"]),
                        phase=AgentPhase(r["phase"]),
                        data=_json_loads(r.get("data")),
                        created_at=_to_timestamp(r.get("created_at")),
                    )
                    for r in rows
                ]

    # 5. Audits
    def save_audit(self, audit: AgentMeshAudit) -> AgentMeshAudit:
        sql = """
        INSERT INTO agent_mesh_audits (
            audit_id, run_id, tenant_id, action, principal_id, risk_level, details, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, TO_TIMESTAMP(%s))
        RETURNING audit_id, run_id, tenant_id, action, principal_id, risk_level, details,
                  EXTRACT(EPOCH FROM created_at) AS created_at;
        """
        params = (
            audit.audit_id,
            audit.run_id,
            audit.tenant_id,
            audit.action,
            audit.principal_id,
            audit.risk_level.value,
            _json_dumps(audit.details),
            audit.created_at,
        )
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                return AgentMeshAudit(
                    audit_id=str(row["audit_id"]),
                    run_id=str(row["run_id"]),
                    tenant_id=str(row["tenant_id"]),
                    action=str(row["action"]),
                    principal_id=str(row["principal_id"]),
                    risk_level=CapabilityRiskLevel(row["risk_level"]),
                    details=_json_loads(row.get("details")),
                    created_at=_to_timestamp(row.get("created_at")),
                )

    def list_audits(self, tenant_id: str, limit: int = 100) -> list[AgentMeshAudit]:
        sql = """
        SELECT audit_id, run_id, tenant_id, action, principal_id, risk_level, details,
               EXTRACT(EPOCH FROM created_at) AS created_at
        FROM agent_mesh_audits
        WHERE tenant_id = %s
        ORDER BY created_at DESC
        LIMIT %s;
        """
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (tenant_id, limit))
                rows = cur.fetchall()
                return [
                    AgentMeshAudit(
                        audit_id=str(r["audit_id"]),
                        run_id=str(r["run_id"]),
                        tenant_id=str(r["tenant_id"]),
                        action=str(r["action"]),
                        principal_id=str(r["principal_id"]),
                        risk_level=CapabilityRiskLevel(r["risk_level"]),
                        details=_json_loads(r.get("details")),
                        created_at=_to_timestamp(r.get("created_at")),
                    )
                    for r in rows
                ]

    # 6. Purge
    def purge_tenant_data(self, tenant_id: str) -> int:
        sql = "DELETE FROM agent_runs WHERE tenant_id = %s;"
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (tenant_id,))
                return cur.rowcount

    # Helpers
    def _row_to_run(self, row: dict[str, Any]) -> AgentRun:
        return AgentRun(
            run_id=str(row["run_id"]),
            tenant_id=str(row["tenant_id"]),
            user_id=str(row["user_id"]),
            parent_run_id=str(row["parent_run_id"]) if row.get("parent_run_id") is not None else None,
            correlation_id=str(row["correlation_id"]),
            causation_id=str(row["causation_id"]) if row.get("causation_id") is not None else None,
            intent=str(row["intent"]),
            status=AgentRunStatus(row["status"]),
            current_phase=AgentPhase(row["current_phase"]),
            depth=int(row.get("depth", 0)),
            budget=AgentRunBudget.from_dict(_json_loads(row.get("budget"))),
            iteration_count=int(row.get("iteration_count", 0)),
            tool_call_count=int(row.get("tool_call_count", 0)),
            provider_call_count=int(row.get("provider_call_count", 0)),
            token_usage=_json_loads(row.get("token_usage")),
            cost_estimate=float(row.get("cost_estimate") or 0.0),
            final_outcome=_json_loads(row.get("final_outcome")),
            error_detail=str(row["error_detail"]) if row.get("error_detail") is not None else None,
            created_at=_to_timestamp(row.get("created_at")),
            started_at=_to_timestamp(row["started_at"]) if row.get("started_at") is not None else None,
            completed_at=_to_timestamp(row["completed_at"]) if row.get("completed_at") is not None else None,
        )

    def _row_to_step(self, row: dict[str, Any]) -> AgentRunStep:
        return AgentRunStep(
            step_id=str(row["step_id"]),
            run_id=str(row["run_id"]),
            tenant_id=str(row["tenant_id"]),
            step_number=int(row["step_number"]),
            phase=AgentPhase(row["phase"]),
            plan_action=str(row["plan_action"]),
            action_type=ActionType(row["action_type"]),
            parameters=_json_loads(row.get("parameters")),
            result=_json_loads(row.get("result")),
            status=str(row.get("status", "requested")),
            verification_status=VerificationStatus(row["verification_status"]),
            approval_token=str(row["approval_token"]) if row.get("approval_token") is not None else None,
            duration_ms=float(row.get("duration_ms") or 0.0),
            created_at=_to_timestamp(row.get("created_at")),
            completed_at=_to_timestamp(row["completed_at"]) if row.get("completed_at") is not None else None,
        )

    def _row_to_delegation(self, row: dict[str, Any]) -> AgentDelegation:
        return AgentDelegation(
            delegation_id=str(row["delegation_id"]),
            parent_run_id=str(row["parent_run_id"]),
            child_run_id=str(row["child_run_id"]),
            tenant_id=str(row["tenant_id"]),
            role=AgentRole(row["role"]),
            capabilities=list(_json_loads(row.get("capabilities"))),
            budget_allocated=AgentRunBudget.from_dict(_json_loads(row.get("budget_allocated"))),
            status=str(row.get("status", "active")),
            created_at=_to_timestamp(row.get("created_at")),
            completed_at=_to_timestamp(row["completed_at"]) if row.get("completed_at") is not None else None,
        )
