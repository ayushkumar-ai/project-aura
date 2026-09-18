-- =============================================================================
-- Migration 008: Multimodal Processing & Rich Interaction
-- Project AURA — Milestone 57
-- =============================================================================

-- 1. Multimodal Artifacts Table
CREATE TABLE IF NOT EXISTS multimodal_artifacts (
    artifact_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    media_type VARCHAR(32) NOT NULL,
    format VARCHAR(64) NOT NULL,
    size_bytes BIGINT NOT NULL DEFAULT 0,
    checksum_sha256 VARCHAR(64) NOT NULL,
    storage_uri VARCHAR(512) NOT NULL,
    lifecycle_state VARCHAR(32) NOT NULL DEFAULT 'uploaded',
    provenance VARCHAR(32) NOT NULL DEFAULT 'user_upload',
    security_classification VARCHAR(32) NOT NULL DEFAULT 'unrestricted',
    filename VARCHAR(256) NOT NULL DEFAULT '',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NULL,
    CONSTRAINT chk_media_type CHECK (media_type IN ('image', 'audio', 'document', 'structured_data', 'binary_artifact')),
    CONSTRAINT chk_lifecycle_state CHECK (lifecycle_state IN ('uploaded', 'validating', 'accepted', 'processing', 'processed', 'failed', 'quarantined', 'expired', 'deleted')),
    CONSTRAINT chk_security_class CHECK (security_classification IN ('unrestricted', 'confidential', 'restricted', 'quarantined')),
    CONSTRAINT chk_size_positive CHECK (size_bytes >= 0)
);

CREATE INDEX IF NOT EXISTS idx_mm_artifacts_tenant_state ON multimodal_artifacts(tenant_id, lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_mm_artifacts_tenant_checksum ON multimodal_artifacts(tenant_id, checksum_sha256);

-- 2. Multimodal Processing Jobs Table
CREATE TABLE IF NOT EXISTS multimodal_processing_jobs (
    job_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    artifact_id VARCHAR(128) NOT NULL REFERENCES multimodal_artifacts(artifact_id) ON DELETE CASCADE,
    operation VARCHAR(64) NOT NULL,
    capability_id VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    error_detail TEXT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    idempotency_key VARCHAR(128) NULL,
    started_at TIMESTAMPTZ NULL,
    completed_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_job_status CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS idx_mm_jobs_tenant_artifact ON multimodal_processing_jobs(tenant_id, artifact_id);
CREATE INDEX IF NOT EXISTS idx_mm_jobs_tenant_idemp ON multimodal_processing_jobs(tenant_id, idempotency_key);

-- 3. Multimodal Results Table
CREATE TABLE IF NOT EXISTS multimodal_results (
    result_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    artifact_id VARCHAR(128) NOT NULL REFERENCES multimodal_artifacts(artifact_id) ON DELETE CASCADE,
    job_id VARCHAR(128) NULL REFERENCES multimodal_processing_jobs(job_id) ON DELETE SET NULL,
    operation VARCHAR(64) NOT NULL,
    extracted_text TEXT NOT NULL DEFAULT '',
    structured_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    bounding_boxes JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mm_results_tenant_artifact ON multimodal_results(tenant_id, artifact_id);

-- 4. Multimodal Derivations Table
CREATE TABLE IF NOT EXISTS multimodal_derivations (
    derivation_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_artifact_id VARCHAR(128) NOT NULL REFERENCES multimodal_artifacts(artifact_id) ON DELETE CASCADE,
    derived_type VARCHAR(64) NOT NULL,
    derived_id VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mm_derivations_source ON multimodal_derivations(tenant_id, source_artifact_id);

-- 5. Multimodal Capability Usage Table
CREATE TABLE IF NOT EXISTS multimodal_capability_usage (
    usage_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    capability_id VARCHAR(64) NOT NULL,
    provider VARCHAR(64) NOT NULL DEFAULT 'gateway',
    model VARCHAR(128) NOT NULL DEFAULT 'default',
    input_size_bytes BIGINT NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    duration_ms DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    status VARCHAR(32) NOT NULL DEFAULT 'success',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mm_usage_tenant ON multimodal_capability_usage(tenant_id, created_at);
