"""M57 — PostgreSQL Multimodal Repository Implementation.

Provides production PostgreSQL 16 persistence for multimodal artifacts,
processing jobs, structured results, derivations, and usage metrics.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from core.database import DatabaseConnectionPool
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
from core.repositories.base_multimodal import BaseMultimodalRepository

logger = logging.getLogger("aura.repositories.postgres_multimodal")


def _to_timestamp(dt: Any) -> float:
    if dt is None:
        return time.time()
    if isinstance(dt, (int, float)):
        return float(dt)
    if isinstance(dt, datetime):
        return dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp()
    return time.time()


def _json_dumps(val: Any) -> str:
    return json.dumps(val, default=str)


def _json_loads(val: Any) -> Any:
    if val is None:
        return {}
    if isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except Exception:
        return {}


class PostgresMultimodalRepository(BaseMultimodalRepository):
    """PostgreSQL 16 repository for multimodal processing data."""

    def __init__(self, db_pool: DatabaseConnectionPool):
        self.db_pool = db_pool

    def save_artifact(self, artifact: MultimodalArtifact) -> MultimodalArtifact:
        sql = """
        INSERT INTO multimodal_artifacts (
            artifact_id, tenant_id, media_type, format, size_bytes,
            checksum_sha256, storage_uri, lifecycle_state, provenance,
            security_classification, filename, metadata, created_at, updated_at, expires_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, TO_TIMESTAMP(%s), CURRENT_TIMESTAMP,
            CASE WHEN %s::double precision IS NOT NULL THEN TO_TIMESTAMP(%s) ELSE NULL END
        )
        ON CONFLICT (artifact_id) DO UPDATE SET
            media_type = EXCLUDED.media_type,
            format = EXCLUDED.format,
            size_bytes = EXCLUDED.size_bytes,
            checksum_sha256 = EXCLUDED.checksum_sha256,
            storage_uri = EXCLUDED.storage_uri,
            lifecycle_state = EXCLUDED.lifecycle_state,
            provenance = EXCLUDED.provenance,
            security_classification = EXCLUDED.security_classification,
            filename = EXCLUDED.filename,
            metadata = EXCLUDED.metadata,
            updated_at = CURRENT_TIMESTAMP,
            expires_at = EXCLUDED.expires_at
        RETURNING artifact_id, tenant_id, media_type, format, size_bytes,
                  checksum_sha256, storage_uri, lifecycle_state, provenance,
                  security_classification, filename, metadata,
                  EXTRACT(EPOCH FROM created_at) AS created_at,
                  EXTRACT(EPOCH FROM updated_at) AS updated_at,
                  EXTRACT(EPOCH FROM expires_at) AS expires_at;
        """
        params = (
            artifact.artifact_id,
            artifact.tenant_id,
            artifact.media_type.value if isinstance(artifact.media_type, MultimodalMediaType) else str(artifact.media_type),
            artifact.format.value if isinstance(artifact.format, MediaFormat) else str(artifact.format),
            artifact.size_bytes,
            artifact.checksum_sha256,
            artifact.storage_uri,
            artifact.lifecycle_state.value if isinstance(artifact.lifecycle_state, ArtifactLifecycleState) else str(artifact.lifecycle_state),
            artifact.provenance.value if isinstance(artifact.provenance, MultimodalProvenance) else str(artifact.provenance),
            artifact.security_classification.value if isinstance(artifact.security_classification, SecurityClassification) else str(artifact.security_classification),
            artifact.filename,
            _json_dumps(artifact.metadata),
            artifact.created_at,
            artifact.expires_at,
            artifact.expires_at,
        )

        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                if row:
                    return self._row_to_artifact(row)
        return artifact

    def _row_to_artifact(self, row: dict[str, Any]) -> MultimodalArtifact:
        return MultimodalArtifact(
            artifact_id=str(row["artifact_id"]),
            tenant_id=str(row["tenant_id"]),
            media_type=MultimodalMediaType(row["media_type"]),
            format=MediaFormat(row["format"]) if row["format"] in [f.value for f in MediaFormat] else row["format"],
            size_bytes=int(row["size_bytes"]),
            checksum_sha256=str(row["checksum_sha256"]),
            storage_uri=str(row["storage_uri"]),
            lifecycle_state=ArtifactLifecycleState(row["lifecycle_state"]),
            provenance=MultimodalProvenance(row["provenance"]),
            security_classification=SecurityClassification(row["security_classification"]),
            filename=str(row.get("filename", "")),
            metadata=_json_loads(row.get("metadata")),
            created_at=_to_timestamp(row.get("created_at")),
            updated_at=_to_timestamp(row.get("updated_at")),
            expires_at=_to_timestamp(row["expires_at"]) if row.get("expires_at") is not None else None,
        )

    def get_artifact(self, artifact_id: str, tenant_id: str) -> MultimodalArtifact | None:
        sql = """
        SELECT artifact_id, tenant_id, media_type, format, size_bytes,
               checksum_sha256, storage_uri, lifecycle_state, provenance,
               security_classification, filename, metadata,
               EXTRACT(EPOCH FROM created_at) AS created_at,
               EXTRACT(EPOCH FROM updated_at) AS updated_at,
               EXTRACT(EPOCH FROM expires_at) AS expires_at
        FROM multimodal_artifacts
        WHERE artifact_id = %s AND tenant_id = %s;
        """
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (artifact_id, tenant_id))
                row = cur.fetchone()
                if row:
                    return self._row_to_artifact(row)
        return None

    def list_artifacts(
        self,
        tenant_id: str,
        lifecycle_state: ArtifactLifecycleState | str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MultimodalArtifact]:
        conditions = ["tenant_id = %s"]
        params: list[Any] = [tenant_id]

        if lifecycle_state is not None:
            state_val = lifecycle_state.value if isinstance(lifecycle_state, ArtifactLifecycleState) else str(lifecycle_state)
            conditions.append("lifecycle_state = %s")
            params.append(state_val)

        where_clause = " AND ".join(conditions)
        sql = f"""
        SELECT artifact_id, tenant_id, media_type, format, size_bytes,
               checksum_sha256, storage_uri, lifecycle_state, provenance,
               security_classification, filename, metadata,
               EXTRACT(EPOCH FROM created_at) AS created_at,
               EXTRACT(EPOCH FROM updated_at) AS updated_at,
               EXTRACT(EPOCH FROM expires_at) AS expires_at
        FROM multimodal_artifacts
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s;
        """
        params.extend([limit, offset])

        artifacts = []
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                for row in cur.fetchall():
                    artifacts.append(self._row_to_artifact(row))
        return artifacts

    def update_artifact_state(
        self,
        artifact_id: str,
        tenant_id: str,
        lifecycle_state: ArtifactLifecycleState | str,
        reason: str = "",
    ) -> MultimodalArtifact | None:
        state_val = lifecycle_state.value if isinstance(lifecycle_state, ArtifactLifecycleState) else str(lifecycle_state)
        sql = """
        UPDATE multimodal_artifacts
        SET lifecycle_state = %s,
            updated_at = CURRENT_TIMESTAMP,
            metadata = CASE
                WHEN %s <> '' THEN metadata || jsonb_build_object('state_change_reason', %s::text)
                ELSE metadata
            END
        WHERE artifact_id = %s AND tenant_id = %s
        RETURNING artifact_id, tenant_id, media_type, format, size_bytes,
                  checksum_sha256, storage_uri, lifecycle_state, provenance,
                  security_classification, filename, metadata,
                  EXTRACT(EPOCH FROM created_at) AS created_at,
                  EXTRACT(EPOCH FROM updated_at) AS updated_at,
                  EXTRACT(EPOCH FROM expires_at) AS expires_at;
        """
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (state_val, reason, reason, artifact_id, tenant_id))
                row = cur.fetchone()
                if row:
                    return self._row_to_artifact(row)
        return None

    def delete_artifact(self, artifact_id: str, tenant_id: str) -> bool:
        sql = "DELETE FROM multimodal_artifacts WHERE artifact_id = %s AND tenant_id = %s;"
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (artifact_id, tenant_id))
                return cur.rowcount > 0

    def save_job(self, job: MultimodalProcessingJob) -> MultimodalProcessingJob:
        sql = """
        INSERT INTO multimodal_processing_jobs (
            job_id, tenant_id, artifact_id, operation, capability_id,
            status, error_detail, attempts, max_attempts, idempotency_key,
            started_at, completed_at, created_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            CASE WHEN %s::double precision IS NOT NULL THEN TO_TIMESTAMP(%s) ELSE NULL END,
            CASE WHEN %s::double precision IS NOT NULL THEN TO_TIMESTAMP(%s) ELSE NULL END,
            TO_TIMESTAMP(%s)
        )
        ON CONFLICT (job_id) DO UPDATE SET
            status = EXCLUDED.status,
            error_detail = EXCLUDED.error_detail,
            attempts = EXCLUDED.attempts,
            started_at = EXCLUDED.started_at,
            completed_at = EXCLUDED.completed_at
        RETURNING job_id, tenant_id, artifact_id, operation, capability_id,
                  status, error_detail, attempts, max_attempts, idempotency_key,
                  EXTRACT(EPOCH FROM started_at) AS started_at,
                  EXTRACT(EPOCH FROM completed_at) AS completed_at,
                  EXTRACT(EPOCH FROM created_at) AS created_at;
        """
        params = (
            job.job_id,
            job.tenant_id,
            job.artifact_id,
            job.operation,
            job.capability_id,
            job.status.value if isinstance(job.status, JobStatus) else str(job.status),
            job.error_detail,
            job.attempts,
            job.max_attempts,
            job.idempotency_key,
            job.started_at,
            job.started_at,
            job.completed_at,
            job.completed_at,
            job.created_at,
        )
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                if row:
                    return self._row_to_job(row)
        return job

    def _row_to_job(self, row: dict[str, Any]) -> MultimodalProcessingJob:
        return MultimodalProcessingJob(
            job_id=str(row["job_id"]),
            tenant_id=str(row["tenant_id"]),
            artifact_id=str(row["artifact_id"]),
            operation=str(row["operation"]),
            capability_id=str(row["capability_id"]),
            status=JobStatus(row["status"]),
            error_detail=str(row["error_detail"]) if row.get("error_detail") is not None else None,
            attempts=int(row.get("attempts", 0)),
            max_attempts=int(row.get("max_attempts", 3)),
            idempotency_key=str(row["idempotency_key"]) if row.get("idempotency_key") is not None else None,
            started_at=_to_timestamp(row["started_at"]) if row.get("started_at") is not None else None,
            completed_at=_to_timestamp(row["completed_at"]) if row.get("completed_at") is not None else None,
            created_at=_to_timestamp(row.get("created_at")),
        )

    def get_job(self, job_id: str, tenant_id: str) -> MultimodalProcessingJob | None:
        sql = """
        SELECT job_id, tenant_id, artifact_id, operation, capability_id,
               status, error_detail, attempts, max_attempts, idempotency_key,
               EXTRACT(EPOCH FROM started_at) AS started_at,
               EXTRACT(EPOCH FROM completed_at) AS completed_at,
               EXTRACT(EPOCH FROM created_at) AS created_at
        FROM multimodal_processing_jobs
        WHERE job_id = %s AND tenant_id = %s;
        """
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (job_id, tenant_id))
                row = cur.fetchone()
                if row:
                    return self._row_to_job(row)
        return None

    def get_job_by_idempotency(self, tenant_id: str, idempotency_key: str) -> MultimodalProcessingJob | None:
        sql = """
        SELECT job_id, tenant_id, artifact_id, operation, capability_id,
               status, error_detail, attempts, max_attempts, idempotency_key,
               EXTRACT(EPOCH FROM started_at) AS started_at,
               EXTRACT(EPOCH FROM completed_at) AS completed_at,
               EXTRACT(EPOCH FROM created_at) AS created_at
        FROM multimodal_processing_jobs
        WHERE tenant_id = %s AND idempotency_key = %s
        ORDER BY created_at DESC
        LIMIT 1;
        """
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (tenant_id, idempotency_key))
                row = cur.fetchone()
                if row:
                    return self._row_to_job(row)
        return None

    def save_result(self, result: MultimodalResult) -> MultimodalResult:
        sql = """
        INSERT INTO multimodal_results (
            result_id, tenant_id, artifact_id, job_id, operation,
            extracted_text, structured_data, confidence, bounding_boxes, metadata, created_at
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, TO_TIMESTAMP(%s)
        )
        ON CONFLICT (result_id) DO UPDATE SET
            extracted_text = EXCLUDED.extracted_text,
            structured_data = EXCLUDED.structured_data,
            confidence = EXCLUDED.confidence,
            bounding_boxes = EXCLUDED.bounding_boxes,
            metadata = EXCLUDED.metadata
        RETURNING result_id, tenant_id, artifact_id, job_id, operation,
                  extracted_text, structured_data, confidence, bounding_boxes, metadata,
                  EXTRACT(EPOCH FROM created_at) AS created_at;
        """
        params = (
            result.result_id,
            result.tenant_id,
            result.artifact_id,
            result.job_id,
            result.operation,
            result.extracted_text,
            _json_dumps(result.structured_data),
            result.confidence,
            _json_dumps(result.bounding_boxes),
            _json_dumps(result.metadata),
            result.created_at,
        )
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                if row:
                    return self._row_to_result(row)
        return result

    def _row_to_result(self, row: dict[str, Any]) -> MultimodalResult:
        return MultimodalResult(
            result_id=str(row["result_id"]),
            tenant_id=str(row["tenant_id"]),
            artifact_id=str(row["artifact_id"]),
            job_id=str(row["job_id"]) if row.get("job_id") is not None else None,
            operation=str(row["operation"]),
            extracted_text=str(row.get("extracted_text", "")),
            structured_data=_json_loads(row.get("structured_data")),
            confidence=float(row.get("confidence", 1.0)),
            bounding_boxes=_json_loads(row.get("bounding_boxes")),
            metadata=_json_loads(row.get("metadata")),
            created_at=_to_timestamp(row.get("created_at")),
        )

    def get_result(self, result_id: str, tenant_id: str) -> MultimodalResult | None:
        sql = """
        SELECT result_id, tenant_id, artifact_id, job_id, operation,
               extracted_text, structured_data, confidence, bounding_boxes, metadata,
               EXTRACT(EPOCH FROM created_at) AS created_at
        FROM multimodal_results
        WHERE result_id = %s AND tenant_id = %s;
        """
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (result_id, tenant_id))
                row = cur.fetchone()
                if row:
                    return self._row_to_result(row)
        return None

    def list_results(self, artifact_id: str, tenant_id: str) -> list[MultimodalResult]:
        sql = """
        SELECT result_id, tenant_id, artifact_id, job_id, operation,
               extracted_text, structured_data, confidence, bounding_boxes, metadata,
               EXTRACT(EPOCH FROM created_at) AS created_at
        FROM multimodal_results
        WHERE artifact_id = %s AND tenant_id = %s
        ORDER BY created_at ASC;
        """
        results = []
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (artifact_id, tenant_id))
                for row in cur.fetchall():
                    results.append(self._row_to_result(row))
        return results

    def save_derivation(self, derivation: MultimodalDerivation) -> MultimodalDerivation:
        sql = """
        INSERT INTO multimodal_derivations (
            derivation_id, tenant_id, source_artifact_id, derived_type, derived_id, created_at
        ) VALUES (%s, %s, %s, %s, %s, TO_TIMESTAMP(%s))
        ON CONFLICT (derivation_id) DO NOTHING;
        """
        params = (
            derivation.derivation_id,
            derivation.tenant_id,
            derivation.source_artifact_id,
            derivation.derived_type,
            derivation.derived_id,
            derivation.created_at,
        )
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
        return derivation

    def list_derivations(self, source_artifact_id: str, tenant_id: str) -> list[MultimodalDerivation]:
        sql = """
        SELECT derivation_id, tenant_id, source_artifact_id, derived_type, derived_id,
               EXTRACT(EPOCH FROM created_at) AS created_at
        FROM multimodal_derivations
        WHERE source_artifact_id = %s AND tenant_id = %s;
        """
        derivations = []
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (source_artifact_id, tenant_id))
                for row in cur.fetchall():
                    derivations.append(
                        MultimodalDerivation(
                            derivation_id=str(row["derivation_id"]),
                            tenant_id=str(row["tenant_id"]),
                            source_artifact_id=str(row["source_artifact_id"]),
                            derived_type=str(row["derived_type"]),
                            derived_id=str(row["derived_id"]),
                            created_at=_to_timestamp(row.get("created_at")),
                        )
                    )
        return derivations

    def save_usage(self, usage: MultimodalCapabilityUsage) -> MultimodalCapabilityUsage:
        sql = """
        INSERT INTO multimodal_capability_usage (
            usage_id, tenant_id, capability_id, provider, model,
            input_size_bytes, output_tokens, duration_ms, status, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, TO_TIMESTAMP(%s));
        """
        params = (
            usage.usage_id,
            usage.tenant_id,
            usage.capability_id,
            usage.provider,
            usage.model,
            usage.input_size_bytes,
            usage.output_tokens,
            usage.duration_ms,
            usage.status,
            usage.created_at,
        )
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
        return usage

    def purge_tenant_data(self, tenant_id: str) -> int:
        sql = "DELETE FROM multimodal_artifacts WHERE tenant_id = %s;"
        with self.db_pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (tenant_id,))
                return cur.rowcount
