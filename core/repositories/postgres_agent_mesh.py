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
                  EXTRACT(EPOCH FROM created_at),
                  EXTRACT(EPOCH FROM started_at),
                  EXTRACT(EPOCH FROM completed_at);
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
               EXTRACT(EPOCH FROM created_at),
               EXTRACT(EPOCH FROM started_at),
               EXTRACT(EPOCH FROM completed_at)
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
               EXTRACT(EPOCH FROM created_at),
               EXTRACT(EPOCH FROM started_at),
               EXTRACT(EPOCH FROM completed_at)
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
                  EXTRACT(EPOCH FROM created_at),
                  EXTRACT(EPOCH FROM started_at),
                  EXTRACT(EPOCH FROM completed_at);
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
                  EXTRACT(EPOCH FROM created_at),
                  EXTRACT(EPOCH FROM completed_at);
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
               EXTRACT(EPOCH FROM created_at),
               EXTRACT(EPOCH FROM completed_at)
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
                  EXTRACT(EPOCH FROM created_at),
                  EXTRACT(EPOCH FROM completed_at);
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
               EXTRACT(EPOCH FROM created_at),
               EXTRACT(EPOCH FROM completed_at)
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
                  EXTRACT(EPOCH FROM created_at);
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
                    event_id=row[0],
                    run_id=row[1],
                    tenant_id=row[2],
                    event_type=row[3],
                    phase=AgentPhase(row[4]),
                    data=_json_loads(row[5]),
                    created_at=_to_timestamp(row[6]),
                )

    def list_events(self, run_id: str, tenant_id: str) -> list[AgentMeshEvent]:
        sql = """
        SELECT event_id, run_id, tenant_id, event_type, phase, data,
               EXTRACT(EPOCH FROM created_at)
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
                        event_id=r[0],
                        run_id=r[1],
                        tenant_id=r[2],
                        event_type=r[3],
                        phase=AgentPhase(r[4]),
                        data=_json_loads(r[5]),
                        created_at=_to_timestamp(r[6]),
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
                  EXTRACT(EPOCH FROM created_at);
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
                    audit_id=row[0],
                    run_id=row[1],
                    tenant_id=row[2],
                    action=row[3],
                    principal_id=row[4],
                    risk_level=CapabilityRiskLevel(row[5]),
                    details=_json_loads(row[6]),
                    created_at=_to_timestamp(row[7]),
                )

    def list_audits(self, tenant_id: str, limit: int = 100) -> list[AgentMeshAudit]:
        sql = """
        SELECT audit_id, run_id, tenant_id, action, principal_id, risk_level, details,
               EXTRACT(EPOCH FROM created_at)
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
                        audit_id=r[0],
                        run_id=r[1],
                        tenant_id=r[2],
                        action=r[3],
                        principal_id=r[4],
                        risk_level=CapabilityRiskLevel(r[5]),
                        details=_json_loads(r[6]),
                        created_at=_to_timestamp(r[7]),
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
    def _row_to_run(self, row: Any) -> AgentRun:
        return AgentRun(
            run_id=row[0],
            tenant_id=row[1],
            user_id=row[2],
            parent_run_id=row[3],
            correlation_id=row[4],
            causation_id=row[5],
            intent=row[6],
            status=AgentRunStatus(row[7]),
            current_phase=AgentPhase(row[8]),
            depth=row[9],
            budget=AgentRunBudget.from_dict(_json_loads(row[10])),
            iteration_count=row[11],
            tool_call_count=row[12],
            provider_call_count=row[13],
            token_usage=_json_loads(row[14]),
            cost_estimate=float(row[15] or 0.0),
            final_outcome=_json_loads(row[16]),
            error_detail=row[17],
            created_at=_to_timestamp(row[18]),
            started_at=_to_timestamp(row[19]) if row[19] is not None else None,
            completed_at=_to_timestamp(row[20]) if row[20] is not None else None,
        )

    def _row_to_step(self, row: Any) -> AgentRunStep:
        return AgentRunStep(
            step_id=row[0],
            run_id=row[1],
            tenant_id=row[2],
            step_number=row[3],
            phase=AgentPhase(row[4]),
            plan_action=row[5],
            action_type=ActionType(row[6]),
            parameters=_json_loads(row[7]),
            result=_json_loads(row[8]),
            status=row[9],
            verification_status=VerificationStatus(row[10]),
            approval_token=row[11],
            duration_ms=float(row[12] or 0.0),
            created_at=_to_timestamp(row[13]),
            completed_at=_to_timestamp(row[14]) if row[14] is not None else None,
        )

    def _row_to_delegation(self, row: Any) -> AgentDelegation:
        return AgentDelegation(
            delegation_id=row[0],
            parent_run_id=row[1],
            child_run_id=row[2],
            tenant_id=row[3],
            role=AgentRole(row[4]),
            capabilities=list(_json_loads(row[5])),
            budget_allocated=AgentRunBudget.from_dict(_json_loads(row[6])),
            status=row[7],
            created_at=_to_timestamp(row[8]),
            completed_at=_to_timestamp(row[9]) if row[9] is not None else None,
        )
