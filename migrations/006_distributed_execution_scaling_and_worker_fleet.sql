-- M55 — Distributed Execution Scaling & Worker Fleet Coordination
-- Schema for worker registration, distributed leases, monotonic fencing tokens, execution attempts, and tenant concurrency quotas.

-- 1. Worker Fleet Nodes
CREATE TABLE IF NOT EXISTS workers (
    worker_id VARCHAR(128) PRIMARY KEY,
    instance_id VARCHAR(128) NOT NULL,
    hostname VARCHAR(255) NOT NULL,
    process_id INTEGER NOT NULL,
    incarnation_token VARCHAR(128) NOT NULL UNIQUE,
    generation INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(32) NOT NULL DEFAULT 'starting',
    capabilities JSONB NOT NULL DEFAULT '["*"]'::jsonb,
    concurrency_limit INTEGER NOT NULL DEFAULT 4,
    active_task_count INTEGER NOT NULL DEFAULT 0,
    heartbeat_interval_seconds FLOAT NOT NULL DEFAULT 5.0,
    missed_heartbeats_threshold INTEGER NOT NULL DEFAULT 3,
    last_heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    draining_since TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_worker_status CHECK (status IN ('starting', 'healthy', 'draining', 'unhealthy', 'stopped', 'expired')),
    CONSTRAINT chk_worker_counts CHECK (active_task_count >= 0 AND concurrency_limit > 0)
);

CREATE INDEX IF NOT EXISTS idx_workers_heartbeat ON workers(status, last_heartbeat_at);
CREATE INDEX IF NOT EXISTS idx_workers_instance ON workers(instance_id, status);

-- 2. Distributed Resource Leases & Monotonic Fencing
CREATE TABLE IF NOT EXISTS worker_leases (
    lease_id VARCHAR(128) PRIMARY KEY,
    resource_type VARCHAR(64) NOT NULL,
    resource_id VARCHAR(128) NOT NULL,
    worker_id VARCHAR(128) NOT NULL REFERENCES workers(worker_id) ON DELETE CASCADE,
    incarnation_token VARCHAR(128) NOT NULL,
    fencing_token BIGINT NOT NULL,
    lease_state VARCHAR(32) NOT NULL DEFAULT 'active',
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL,
    renewed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT chk_lease_state CHECK (lease_state IN ('active', 'renewed', 'released', 'expired', 'fenced')),
    CONSTRAINT uq_resource_lease UNIQUE (resource_type, resource_id)
);

CREATE INDEX IF NOT EXISTS idx_worker_leases_expiry ON worker_leases(lease_state, expires_at);
CREATE INDEX IF NOT EXISTS idx_worker_leases_worker ON worker_leases(worker_id, incarnation_token);
CREATE INDEX IF NOT EXISTS idx_worker_leases_resource ON worker_leases(resource_type, resource_id);

-- 3. Execution Attempts & Audit Ledger
CREATE TABLE IF NOT EXISTS execution_attempts (
    attempt_id VARCHAR(128) PRIMARY KEY,
    resource_type VARCHAR(64) NOT NULL DEFAULT 'task',
    resource_id VARCHAR(128) NOT NULL,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    worker_id VARCHAR(128) NOT NULL REFERENCES workers(worker_id) ON DELETE CASCADE,
    incarnation_token VARCHAR(128) NOT NULL,
    fencing_token BIGINT NOT NULL,
    attempt_number INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(32) NOT NULL DEFAULT 'running',
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ NULL,
    error_detail TEXT NOT NULL DEFAULT '',
    CONSTRAINT chk_attempt_status CHECK (status IN ('running', 'completed', 'failed', 'timed_out', 'fenced', 'recovered'))
);

CREATE INDEX IF NOT EXISTS idx_execution_attempts_resource ON execution_attempts(resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_execution_attempts_tenant ON execution_attempts(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_execution_attempts_worker ON execution_attempts(worker_id, status);

-- 4. Tenant Concurrency Limits & Live Capacities
CREATE TABLE IF NOT EXISTS tenant_worker_limits (
    tenant_id VARCHAR(128) PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    max_active_tasks INTEGER NOT NULL DEFAULT 10,
    guaranteed_slots INTEGER NOT NULL DEFAULT 2,
    burst_capacity INTEGER NOT NULL DEFAULT 20,
    active_task_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_tenant_worker_counts CHECK (active_task_count >= 0 AND max_active_tasks > 0)
);

CREATE INDEX IF NOT EXISTS idx_tenant_worker_limits_utilization ON tenant_worker_limits(active_task_count, max_active_tasks);
