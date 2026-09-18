"""M58 — PostgreSQL Platform & Device Repository Integration Tests.

Validates live PostgreSQL 16 persistence for migration 009, device CRUD,
capabilities, sessions, execution history, audit logs, and tenant isolation.
"""

from __future__ import annotations

import os
import time
import uuid
import pytest

from core.database import DatabaseConnectionPool, MigrationRunner
from core.identity import UserIdentity, UserRole
from core.platform.types import (
    CapabilityAuthStatus,
    CapabilityRiskLevel,
    DeviceAuditEvent,
    DeviceCapabilityRecord,
    DeviceExecutionRecord,
    DeviceRecord,
    DeviceSessionRecord,
    DeviceTrustState,
    DeviceType,
    ExecutionMode,
    ExecutionStatus,
    PlatformType,
)
from core.repositories.postgres import PostgresUserRepository
from core.repositories.postgres_platform import PostgresPlatformRepository

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
    return PostgresPlatformRepository(db_pool)


@pytest.fixture
def test_users(db_pool):
    user_repo = PostgresUserRepository(db_pool)
    u1 = UserIdentity(user_id=f"usr_dev_pg1_{uuid.uuid4().hex[:8]}", username=f"u_dev1_{uuid.uuid4().hex[:6]}", roles=frozenset({UserRole.USER}))
    u2 = UserIdentity(user_id=f"usr_dev_pg2_{uuid.uuid4().hex[:8]}", username=f"u_dev2_{uuid.uuid4().hex[:6]}", roles=frozenset({UserRole.USER}))
    user_repo.save(u1)
    user_repo.save(u2)
    return u1, u2


class TestPostgresPlatformIntegration:
    def test_device_crud_in_postgres(self, repo, test_users):
        u1, _ = test_users
        device = DeviceRecord(
            device_id=f"dev_pg_{uuid.uuid4().hex[:12]}",
            tenant_id=u1.user_id,
            name="PG Workstation",
            device_type=DeviceType.DESKTOP,
            platform=PlatformType.WINDOWS,
            trust_state=DeviceTrustState.PENDING_VERIFICATION,
            hostname="host-pg-01",
        )
        saved = repo.save_device(device)
        assert saved.device_id == device.device_id

        fetched = repo.get_device(device.device_id, tenant_id=u1.user_id)
        assert fetched is not None
        assert fetched.name == "PG Workstation"

        # Update trust
        updated = repo.update_device_trust(device.device_id, tenant_id=u1.user_id, trust_state=DeviceTrustState.AUTHORIZED)
        assert updated is not None
        assert updated.trust_state == DeviceTrustState.AUTHORIZED

        # Delete
        deleted = repo.delete_device(device.device_id, tenant_id=u1.user_id)
        assert deleted is True
        assert repo.get_device(device.device_id, tenant_id=u1.user_id) is None

    def test_capabilities_and_executions_in_postgres(self, repo, test_users):
        u1, _ = test_users
        device = DeviceRecord(
            device_id=f"dev_pg_exec_{uuid.uuid4().hex[:12]}",
            tenant_id=u1.user_id,
            name="PG Exec Node",
            trust_state=DeviceTrustState.AUTHORIZED,
        )
        repo.save_device(device)

        # Save capability
        cap = DeviceCapabilityRecord(
            capability_id=f"cap_pg_{uuid.uuid4().hex[:12]}",
            device_id=device.device_id,
            tenant_id=u1.user_id,
            name="get_clock",
            risk_level=CapabilityRiskLevel.LOW,
            auth_status=CapabilityAuthStatus.AUTHORIZED,
        )
        saved_cap = repo.save_capability(cap)
        assert saved_cap.capability_id == cap.capability_id

        # Save execution
        exec_rec = DeviceExecutionRecord(
            execution_id=f"exec_pg_{uuid.uuid4().hex[:12]}",
            tenant_id=u1.user_id,
            device_id=device.device_id,
            capability_name="get_clock",
            risk_level=CapabilityRiskLevel.LOW,
            status=ExecutionStatus.SUCCEEDED,
            execution_mode=ExecutionMode.REAL,
            idempotency_key="pg_idemp_123",
            result={"status": "ok"},
            duration_ms=5.0,
        )
        saved_exec = repo.save_execution(exec_rec)
        assert saved_exec.execution_id == exec_rec.execution_id

        # Query by idempotency
        idemp_fetched = repo.get_execution_by_idempotency(tenant_id=u1.user_id, idempotency_key="pg_idemp_123")
        assert idemp_fetched is not None
        assert idemp_fetched.execution_id == exec_rec.execution_id

        # Save audit event
        audit = DeviceAuditEvent(
            event_id=f"evt_pg_{uuid.uuid4().hex[:12]}",
            tenant_id=u1.user_id,
            device_id=device.device_id,
            action="execute_get_clock",
            principal_id=u1.user_id,
            event_type="execution",
            risk_level=CapabilityRiskLevel.LOW,
        )
        repo.save_audit_event(audit)
        events = repo.list_audit_events(device.device_id, tenant_id=u1.user_id)
        assert len(events) >= 1

    def test_multi_tenant_isolation_in_postgres(self, repo, test_users):
        # Invariant M58-F02 & TEST-M58-SEC-01
        u1, u2 = test_users
        dev1 = DeviceRecord(
            device_id=f"dev_iso1_{uuid.uuid4().hex[:8]}",
            tenant_id=u1.user_id,
            name="Tenant 1 Machine",
        )
        repo.save_device(dev1)

        # Tenant 2 cannot access Tenant 1 device
        assert repo.get_device(dev1.device_id, tenant_id=u2.user_id) is None
        assert repo.delete_device(dev1.device_id, tenant_id=u2.user_id) is False

    def test_hard_purge_tenant_in_postgres(self, repo, test_users):
        u1, _ = test_users
        dev = DeviceRecord(
            device_id=f"dev_purge_{uuid.uuid4().hex[:8]}",
            tenant_id=u1.user_id,
            name="Purge Machine",
        )
        repo.save_device(dev)

        purged_count = repo.purge_tenant_data(tenant_id=u1.user_id)
        assert purged_count >= 1
        assert repo.get_device(dev.device_id, tenant_id=u1.user_id) is None
