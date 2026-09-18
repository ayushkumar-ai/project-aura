"""M57 — PostgreSQL Multimodal Repository Integration Tests.

Validates live PostgreSQL 16 persistence for migration 008, artifact CRUD,
processing jobs, structured results, derivations, and tenant isolation.
"""

from __future__ import annotations

import os
import time
import uuid
import pytest

from core.database import DatabaseConnectionPool, MigrationRunner
from core.identity import UserIdentity, UserRole
from core.multimodal.types import (
    ArtifactLifecycleState,
    JobStatus,
    MediaFormat,
    MultimodalArtifact,
    MultimodalCapabilityUsage,
    MultimodalDerivation,
    MultimodalMediaType,
    MultimodalProcessingJob,
    MultimodalProvenance,
    MultimodalResult,
    SecurityClassification,
)
from core.repositories.postgres import PostgresUserRepository
from core.repositories.postgres_multimodal import PostgresMultimodalRepository

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
    return PostgresMultimodalRepository(db_pool)


@pytest.fixture
def test_users(db_pool):
    user_repo = PostgresUserRepository(db_pool)
    u1 = UserIdentity(id=f"usr_mm_pg1_{uuid.uuid4().hex[:8]}", username=f"u_mm1_{uuid.uuid4().hex[:6]}", role=UserRole.USER)
    u2 = UserIdentity(id=f"usr_mm_pg2_{uuid.uuid4().hex[:8]}", username=f"u_mm2_{uuid.uuid4().hex[:6]}", role=UserRole.USER)
    user_repo.save(u1)
    user_repo.save(u2)
    return u1, u2


class TestPostgresMultimodalIntegration:
    def test_artifact_crud_in_postgres(self, repo, test_users):
        u1, _ = test_users
        art = MultimodalArtifact(
            artifact_id=f"art_pg_{uuid.uuid4().hex[:12]}",
            tenant_id=u1.id,
            media_type=MultimodalMediaType.IMAGE,
            format=MediaFormat.PNG,
            size_bytes=2048,
            checksum_sha256="pg_sha256_hash",
            storage_uri="file:///storage/tenant/art.png",
            lifecycle_state=ArtifactLifecycleState.ACCEPTED,
            filename="diagram.png",
        )
        saved = repo.save_artifact(art)
        assert saved.artifact_id == art.artifact_id

        fetched = repo.get_artifact(art.artifact_id, tenant_id=u1.id)
        assert fetched is not None
        assert fetched.size_bytes == 2048

        # Update state
        updated = repo.update_artifact_state(art.artifact_id, tenant_id=u1.id, lifecycle_state=ArtifactLifecycleState.PROCESSED)
        assert updated.lifecycle_state == ArtifactLifecycleState.PROCESSED

    def test_job_and_result_persistence(self, repo, test_users):
        u1, _ = test_users
        art = MultimodalArtifact(
            artifact_id=f"art_job_{uuid.uuid4().hex[:12]}",
            tenant_id=u1.id,
            media_type=MultimodalMediaType.DOCUMENT,
            format=MediaFormat.PDF,
            size_bytes=4096,
            checksum_sha256="job_sha256",
            storage_uri="file:///storage/doc.pdf",
        )
        repo.save_artifact(art)

        job = MultimodalProcessingJob(
            job_id=f"job_pg_{uuid.uuid4().hex[:12]}",
            tenant_id=u1.id,
            artifact_id=art.artifact_id,
            operation="extract_document",
            capability_id="document_extraction",
            status=JobStatus.RUNNING,
            idempotency_key="idemp_pg_1",
        )
        saved_job = repo.save_job(job)
        assert saved_job.status == JobStatus.RUNNING

        res = MultimodalResult(
            result_id=f"res_pg_{uuid.uuid4().hex[:12]}",
            tenant_id=u1.id,
            artifact_id=art.artifact_id,
            job_id=job.job_id,
            operation="extract_document",
            extracted_text="PostgreSQL parsed text",
            structured_data={"sections": 3},
        )
        saved_res = repo.save_result(res)
        assert saved_res.extracted_text == "PostgreSQL parsed text"

        results = repo.list_results(art.artifact_id, tenant_id=u1.id)
        assert len(results) >= 1

    def test_multi_tenant_isolation_in_postgres(self, repo, test_users):
        u1, u2 = test_users
        art = MultimodalArtifact(
            artifact_id=f"art_iso_{uuid.uuid4().hex[:12]}",
            tenant_id=u1.id,
            media_type=MultimodalMediaType.AUDIO,
            format=MediaFormat.WAV,
            size_bytes=1000,
            checksum_sha256="iso_sha",
            storage_uri="file:///audio.wav",
        )
        repo.save_artifact(art)

        # Tenant 2 cannot get Tenant 1's artifact
        assert repo.get_artifact(art.artifact_id, tenant_id=u2.id) is None
        assert repo.delete_artifact(art.artifact_id, tenant_id=u2.id) is False

    def test_hard_purge_tenant_in_postgres(self, repo, test_users):
        u1, _ = test_users
        art = MultimodalArtifact(
            artifact_id=f"art_purge_{uuid.uuid4().hex[:12]}",
            tenant_id=u1.id,
            media_type=MultimodalMediaType.IMAGE,
            format=MediaFormat.JPEG,
            size_bytes=500,
            checksum_sha256="purge_sha",
            storage_uri="file:///img.jpg",
        )
        repo.save_artifact(art)

        purged = repo.purge_tenant_data(tenant_id=u1.id)
        assert purged >= 1
        assert repo.get_artifact(art.artifact_id, tenant_id=u1.id) is None
