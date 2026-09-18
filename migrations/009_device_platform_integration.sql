-- =============================================================================
-- Migration 009: Real-World Device, Desktop OS & Platform Integration
-- Project AURA — Milestone 58
-- =============================================================================

-- 1. Devices Table
CREATE TABLE IF NOT EXISTS devices (
    device_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(256) NOT NULL,
    device_type VARCHAR(32) NOT NULL,
    platform VARCHAR(32) NOT NULL,
    platform_version VARCHAR(128) NOT NULL DEFAULT '',
    trust_state VARCHAR(32) NOT NULL DEFAULT 'pending_verification',
    hostname VARCHAR(256) NOT NULL DEFAULT '',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_device_trust CHECK (trust_state IN ('unregistered', 'pending_verification', 'verified', 'authorized', 'suspended', 'revoked'))
);

CREATE INDEX IF NOT EXISTS idx_devices_tenant_trust ON devices(tenant_id, trust_state);

-- 2. Device Capabilities Table
CREATE TABLE IF NOT EXISTS device_capabilities (
    capability_id VARCHAR(128) PRIMARY KEY,
    device_id VARCHAR(128) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(128) NOT NULL,
    risk_level VARCHAR(32) NOT NULL DEFAULT 'low',
    auth_status VARCHAR(32) NOT NULL DEFAULT 'declared',
    description TEXT NOT NULL DEFAULT '',
    parameters_schema JSONB NOT NULL DEFAULT '{}'::jsonb,
    requires_approval BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_cap_risk CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    CONSTRAINT chk_cap_auth CHECK (auth_status IN ('declared', 'verified', 'authorized', 'disabled', 'revoked'))
);

CREATE INDEX IF NOT EXISTS idx_device_caps_tenant ON device_capabilities(tenant_id, device_id);

-- 3. Device Sessions Table
CREATE TABLE IF NOT EXISTS device_sessions (
    session_id VARCHAR(128) PRIMARY KEY,
    device_id VARCHAR(128) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    ip_address VARCHAR(64) NOT NULL DEFAULT '',
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NULL,
    last_heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_device_sessions_tenant ON device_sessions(tenant_id, device_id);

-- 4. Device Execution Records Table
CREATE TABLE IF NOT EXISTS device_execution_records (
    execution_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id VARCHAR(128) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    capability_name VARCHAR(128) NOT NULL,
    risk_level VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'requested',
    execution_mode VARCHAR(32) NOT NULL DEFAULT 'real',
    idempotency_key VARCHAR(128) NULL,
    approval_token VARCHAR(128) NULL,
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_detail TEXT NULL,
    duration_ms DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_device_exec_tenant_device ON device_execution_records(tenant_id, device_id);
CREATE INDEX IF NOT EXISTS idx_device_exec_idemp ON device_execution_records(tenant_id, idempotency_key);

-- 5. Device Audit Events Table
CREATE TABLE IF NOT EXISTS device_audit_events (
    event_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id VARCHAR(128) NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,
    action VARCHAR(128) NOT NULL,
    principal_id VARCHAR(128) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    risk_level VARCHAR(32) NOT NULL DEFAULT 'low',
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_device_audit_tenant ON device_audit_events(tenant_id, created_at);
