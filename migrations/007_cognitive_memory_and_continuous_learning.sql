-- =============================================================================
-- Migration 007: Cognitive Memory, Continuous Learning & Personalization
-- Project AURA — Milestone 56
-- =============================================================================

-- 1. Cognitive Memories Table
CREATE TABLE IF NOT EXISTS cognitive_memories (
    memory_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    memory_type VARCHAR(32) NOT NULL,
    category VARCHAR(64) NOT NULL DEFAULT 'general',
    key VARCHAR(256) NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    structured_data JSONB NOT NULL DEFAULT '{}'::jsonb,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    provenance_type VARCHAR(32) NOT NULL DEFAULT 'system_derived',
    lifecycle_state VARCHAR(32) NOT NULL DEFAULT 'active',
    version INTEGER NOT NULL DEFAULT 1,
    supersedes_id VARCHAR(128) NULL,
    taint_status BOOLEAN NOT NULL DEFAULT FALSE,
    source_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    embedding JSONB NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    access_count INTEGER NOT NULL DEFAULT 0,
    last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NULL,
    CONSTRAINT chk_memory_type CHECK (memory_type IN ('episodic', 'semantic', 'preference', 'experience', 'user_profile')),
    CONSTRAINT chk_provenance_type CHECK (provenance_type IN ('user_explicit', 'tool_observed', 'system_derived', 'model_inferred', 'external_imported')),
    CONSTRAINT chk_lifecycle_state CHECK (lifecycle_state IN ('active', 'stale', 'superseded', 'archived', 'deleted')),
    CONSTRAINT chk_confidence_bounds CHECK (confidence >= 0.0 AND confidence <= 1.0)
);

CREATE INDEX IF NOT EXISTS idx_cog_memories_tenant_type ON cognitive_memories(tenant_id, memory_type, lifecycle_state);
CREATE INDEX IF NOT EXISTS idx_cog_memories_tenant_key ON cognitive_memories(tenant_id, key);
CREATE INDEX IF NOT EXISTS idx_cog_memories_lifecycle_expiry ON cognitive_memories(lifecycle_state, expires_at);

-- 2. Memory Contradictions Table
CREATE TABLE IF NOT EXISTS memory_contradictions (
    contradiction_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    memory_a_id VARCHAR(128) NOT NULL REFERENCES cognitive_memories(memory_id) ON DELETE CASCADE,
    memory_b_id VARCHAR(128) NOT NULL REFERENCES cognitive_memories(memory_id) ON DELETE CASCADE,
    contradiction_type VARCHAR(64) NOT NULL DEFAULT 'fact_conflict',
    resolution_status VARCHAR(32) NOT NULL DEFAULT 'detected',
    resolution_strategy VARCHAR(32) NOT NULL DEFAULT 'provenance_precedence',
    resolved_by VARCHAR(128) NULL,
    resolution_details JSONB NOT NULL DEFAULT '{}'::jsonb,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMPTZ NULL,
    CONSTRAINT chk_contradiction_status CHECK (resolution_status IN ('detected', 'auto_resolved', 'manual_pending', 'resolved')),
    CONSTRAINT chk_contradiction_strategy CHECK (resolution_strategy IN ('provenance_precedence', 'user_override', 'recency', 'confidence_threshold', 'manual'))
);

CREATE INDEX IF NOT EXISTS idx_contradictions_tenant_status ON memory_contradictions(tenant_id, resolution_status);

-- 3. User Cognitive Profiles Table
CREATE TABLE IF NOT EXISTS user_cognitive_profiles (
    profile_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
    inferred_traits JSONB NOT NULL DEFAULT '{}'::jsonb,
    interaction_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_user_cognitive_profiles_tenant ON user_cognitive_profiles(tenant_id);

-- 4. Experience Patterns Table
CREATE TABLE IF NOT EXISTS experience_patterns (
    pattern_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    context_key VARCHAR(256) NOT NULL,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    average_latency_ms DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    optimal_tools JSONB NOT NULL DEFAULT '[]'::jsonb,
    failure_modes JSONB NOT NULL DEFAULT '[]'::jsonb,
    recommendations JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_tenant_context_pattern UNIQUE (tenant_id, context_key),
    CONSTRAINT chk_pattern_counts CHECK (success_count >= 0 AND failure_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_experience_patterns_tenant_context ON experience_patterns(tenant_id, context_key);

-- 5. Memory Feedback Events Table
CREATE TABLE IF NOT EXISTS memory_feedback_events (
    event_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_memory_id VARCHAR(128) NULL REFERENCES cognitive_memories(memory_id) ON DELETE CASCADE,
    feedback_type VARCHAR(32) NOT NULL,
    correction_content TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    applied BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_feedback_type CHECK (feedback_type IN ('positive', 'negative', 'correction', 'override'))
);

CREATE INDEX IF NOT EXISTS idx_feedback_events_tenant_target ON memory_feedback_events(tenant_id, target_memory_id);
