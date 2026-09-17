"""M56 — PostgreSQL Cognitive Memory Integration Tests.

Tests live PostgreSQL 16 persistence for migration 007, cognitive memory CRUD,
contradiction resolution, user cognitive profiles, experience patterns,
feedback events, and strict multi-tenant isolation.
"""

from __future__ import annotations

import os
import time
import uuid
import pytest

from core.cognitive_memory.types import (
    CognitiveMemory,
    CognitiveMemoryType,
    ContradictionStatus,
    ExperiencePattern,
    FeedbackType,
    LifecycleState,
    MemoryFeedbackEvent,
    ProvenanceType,
    ResolutionStrategy,
    UserCognitiveProfile,
)
from core.database import DatabaseConnectionPool, MigrationRunner
from core.identity import UserIdentity, UserRole
from core.repositories.postgres import PostgresUserRepository
from core.repositories.postgres_cognitive_memory import PostgresCognitiveMemoryRepository

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
    return PostgresCognitiveMemoryRepository(db_pool)


@pytest.fixture
def user_repo(db_pool):
    return PostgresUserRepository(db_pool)


@pytest.fixture
def test_tenants(user_repo):
    t1_id = f"test_tenant_a_{uuid.uuid4().hex[:8]}"
    t2_id = f"test_tenant_b_{uuid.uuid4().hex[:8]}"
    u1 = UserIdentity(user_id=t1_id, username=f"user_{t1_id}", roles=[UserRole.USER])
    u2 = UserIdentity(user_id=t2_id, username=f"user_{t2_id}", roles=[UserRole.USER])
    user_repo.save(u1)
    user_repo.save(u2)
    yield t1_id, t2_id
    user_repo.delete(t1_id)
    user_repo.delete(t2_id)


class TestPostgresCognitiveMemoryIntegration:
    """Integration test suite against live PostgreSQL 16 instance."""

    def test_record_and_get_cognitive_memory(self, repo, test_tenants):
        t1, _ = test_tenants
        m_id = str(uuid.uuid4())
        mem, contra = repo.record_memory(
            tenant_id=t1,
            memory_id=m_id,
            content="Preferred cloud provider is GCP.",
            memory_type=CognitiveMemoryType.PREFERENCE,
            category="cloud",
            key="cloud_provider",
            confidence=0.95,
            provenance_type=ProvenanceType.USER_EXPLICIT,
            tags=["cloud", "gcp"],
        )
        assert mem.memory_id == m_id
        assert contra is None

        fetched = repo.get_memory(m_id, tenant_id=t1)
        assert fetched is not None
        assert fetched.content == "Preferred cloud provider is GCP."
        assert fetched.access_count == 1
        assert "gcp" in fetched.tags

    def test_query_memories_with_filters(self, repo, test_tenants):
        t1, _ = test_tenants
        for i in range(5):
            repo.record_memory(
                tenant_id=t1,
                content=f"Semantic rule number {i}",
                memory_type=CognitiveMemoryType.SEMANTIC,
                category="rules",
                key=f"rule_{i}",
                confidence=0.8,
            )

        results = repo.query_memories(
            tenant_id=t1,
            memory_type=CognitiveMemoryType.SEMANTIC,
            category="rules",
            limit=10,
        )
        assert len(results) >= 5

    def test_contradiction_detection_and_resolution_in_postgres(self, repo, test_tenants):
        """Invariant M56-F05 & M56-F12: Automated contradiction detection and atomic resolution in DB."""
        t1, _ = test_tenants
        m1_id = str(uuid.uuid4())
        m2_id = str(uuid.uuid4())

        # 1. Inferred memory
        mem1, _ = repo.record_memory(
            tenant_id=t1,
            memory_id=m1_id,
            content="NodeJS 18",
            key="runtime_version",
            provenance_type=ProvenanceType.MODEL_INFERRED,
            confidence=0.7,
        )

        # 2. Explicit user memory conflicting with inferred memory
        mem2, contra = repo.record_memory(
            tenant_id=t1,
            memory_id=m2_id,
            content="NodeJS 20 LTS",
            key="runtime_version",
            provenance_type=ProvenanceType.USER_EXPLICIT,
            confidence=1.0,
        )

        assert contra is not None
        assert contra.resolution_strategy == ResolutionStrategy.USER_OVERRIDE

        # Verify old memory is superseded
        m1_updated = repo.get_memory(m1_id, tenant_id=t1)
        assert m1_updated is not None
        assert m1_updated.lifecycle_state == LifecycleState.SUPERSEDED

        # Verify new memory is active and supersedes old
        m2_active = repo.get_memory(m2_id, tenant_id=t1)
        assert m2_active is not None
        assert m2_active.lifecycle_state == LifecycleState.ACTIVE
        assert m2_active.supersedes_id == m1_id
        assert m2_active.version == 2

    def test_profile_upsert_and_versioning(self, repo, test_tenants):
        """Invariant M56-F15 & M56-F31: Profile upsert with incrementing version."""
        t1, _ = test_tenants
        p_init = UserCognitiveProfile(
            profile_id=str(uuid.uuid4()),
            tenant_id=t1,
            preferences={"theme": "dark"},
            inferred_traits={"pace": "fast"},
        )
        saved1 = repo.save_profile(p_init)
        assert saved1.preferences["theme"] == "dark"

        # Update profile
        saved1.preferences["theme"] = "system"
        saved2 = repo.save_profile(saved1)
        assert saved2.preferences["theme"] == "system"
        assert saved2.version > saved1.version

    def test_experience_pattern_persistence(self, repo, test_tenants):
        """Invariant M56-F14 & M56-F32: Experience pattern upsert."""
        t1, _ = test_tenants
        pat = ExperiencePattern(
            pattern_id=str(uuid.uuid4()),
            tenant_id=t1,
            context_key="k8s_deploy",
            success_count=5,
            failure_count=1,
            average_latency_ms=250.0,
            optimal_tools=["kubectl", "helm"],
        )
        saved = repo.save_experience_pattern(pat)
        assert saved.context_key == "k8s_deploy"
        assert saved.success_count == 5

        fetched = repo.get_experience_pattern(t1, "k8s_deploy")
        assert fetched is not None
        assert fetched.average_latency_ms == 250.0

    def test_multi_tenant_isolation(self, repo, test_tenants):
        """Invariant M56-F01: Tenant A cannot access Tenant B's memories or profile."""
        t1, t2 = test_tenants
        m1_id = str(uuid.uuid4())

        repo.record_memory(
            tenant_id=t1,
            memory_id=m1_id,
            content="Confidential Tenant A memory",
            key="secret_key",
        )

        # Tenant B cannot get or query Tenant A memory
        assert repo.get_memory(m1_id, tenant_id=t2) is None
        t2_mems = repo.query_memories(tenant_id=t2, key="secret_key")
        assert len(t2_mems) == 0

    def test_hard_purge_tenant_memories(self, repo, test_tenants):
        """Invariant M56-F16: GDPR complete tenant memory wipe."""
        t1, _ = test_tenants
        repo.record_memory(tenant_id=t1, content="To be purged", key="purge_me")
        repo.save_profile(UserCognitiveProfile(tenant_id=t1, preferences={"a": 1}))

        purged_count = repo.clear_tenant_memories(t1)
        assert purged_count >= 1

        assert repo.query_memories(tenant_id=t1) == []
        assert repo.get_profile(t1) is None
