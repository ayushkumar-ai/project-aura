"""M59 — PostgreSQL Agent Mesh Repository Integration Tests.

Validates live PostgreSQL 16 persistence for migration 010, AgentRun CRUD,
steps, delegations, events, audits, and tenant isolation.
"""

from __future__ import annotations

import os
import time
import uuid
import pytest

from core.database import DatabaseConnectionPool, MigrationRunner
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
from core.platform.types import CapabilityRiskLevel
from core.repositories.postgres_agent_mesh import PostgresAgentMeshRepository

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


@pytest.fixture
def repo(db_pool):
    return PostgresAgentMeshRepository(db_pool)


class TestPostgresAgentMeshIntegration:
    def test_run_crud_and_tenant_isolation(self, repo):
        # Invariants M59-F01, M59-F17, M59-F50
        tenant_a = f"t_pg_a_{uuid.uuid4().hex[:8]}"
        tenant_b = f"t_pg_b_{uuid.uuid4().hex[:8]}"

        run_a = AgentRun(
            run_id=f"run_pg_{uuid.uuid4().hex[:12]}",
            tenant_id=tenant_a,
            user_id="alice",
            intent="PostgreSQL persistence test run",
            status=AgentRunStatus.PENDING,
            current_phase=AgentPhase.RECEIVE,
            budget=AgentRunBudget(max_iterations=10),
        )
        saved = repo.save_run(run_a)
        assert saved.run_id == run_a.run_id

        # Fetch for tenant A
        fetched = repo.get_run(run_a.run_id, tenant_a)
        assert fetched is not None
        assert fetched.run_id == run_a.run_id
        assert fetched.intent == run_a.intent
        assert fetched.budget.max_iterations == 10

        # Tenant B cannot access
        assert repo.get_run(run_a.run_id, tenant_b) is None

        # Update status
        run_a.transition_to(AgentRunStatus.RUNNING)
        repo.save_run(run_a)
        updated = repo.get_run(run_a.run_id, tenant_a)
        assert updated.status == AgentRunStatus.RUNNING

    def test_steps_delegations_events_audits(self, repo):
        tenant = f"t_pg_{uuid.uuid4().hex[:8]}"
        run_id = f"run_{uuid.uuid4().hex[:12]}"

        run = AgentRun(
            run_id=run_id,
            tenant_id=tenant,
            user_id="alice",
            status=AgentRunStatus.RUNNING,
        )
        repo.save_run(run)

        # 1. Step
        step = AgentRunStep(
            run_id=run_id,
            tenant_id=tenant,
            step_number=1,
            phase=AgentPhase.EXECUTE,
            plan_action="calculate",
            action_type=ActionType.TOOL,
            parameters={"expr": "1+1"},
            result={"result": 2},
            status="completed",
            verification_status=VerificationStatus.VERIFIED_SUCCESS,
        )
        repo.save_step(step)
        steps = repo.list_steps(run_id, tenant)
        assert len(steps) == 1
        assert steps[0].plan_action == "calculate"

        # 2. Delegation
        delegation = AgentDelegation(
            parent_run_id=run_id,
            child_run_id=f"run_child_{uuid.uuid4().hex[:8]}",
            tenant_id=tenant,
            role=AgentRole.RESEARCH,
            capabilities=["web_search"],
        )
        repo.save_delegation(delegation)
        delegations = repo.list_delegations(run_id, tenant)
        assert len(delegations) == 1
        assert delegations[0].role == AgentRole.RESEARCH

        # 3. Event
        event = AgentMeshEvent(
            run_id=run_id,
            tenant_id=tenant,
            event_type="phase_started",
            phase=AgentPhase.EXECUTE,
            data={"step": 1},
        )
        repo.save_event(event)
        events = repo.list_events(run_id, tenant)
        assert len(events) == 1
        assert events[0].event_type == "phase_started"

        # 4. Audit
        audit = AgentMeshAudit(
            run_id=run_id,
            tenant_id=tenant,
            action="policy_evaluate",
            principal_id="alice",
            risk_level=CapabilityRiskLevel.LOW,
            details={"allowed": True},
        )
        repo.save_audit(audit)
        audits = repo.list_audits(run_id, tenant)
        assert len(audits) == 1
        assert audits[0].action == "policy_evaluate"

        # 5. Purge Tenant Data
        purged = repo.purge_tenant_data(tenant)
        assert purged >= 1
        assert repo.get_run(run_id, tenant) is None
        assert len(repo.list_steps(run_id, tenant)) == 0
