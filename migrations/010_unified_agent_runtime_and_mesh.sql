-- =============================================================================
-- Migration 010: Unified Autonomous Agent Runtime & Enterprise Intelligence Mesh
-- Project AURA — Milestone 59
-- =============================================================================

-- 1. Agent Runs Table
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id VARCHAR(128) PRIMARY KEY,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    user_id VARCHAR(128) NOT NULL,
    parent_run_id VARCHAR(128) NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    correlation_id VARCHAR(128) NOT NULL,
    causation_id VARCHAR(128) NULL,
    intent TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    current_phase VARCHAR(32) NOT NULL DEFAULT 'receive',
    depth INTEGER NOT NULL DEFAULT 0,
    budget JSONB NOT NULL DEFAULT '{}'::jsonb,
    iteration_count INTEGER NOT NULL DEFAULT 0,
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    provider_call_count INTEGER NOT NULL DEFAULT 0,
    token_usage JSONB NOT NULL DEFAULT '{}'::jsonb,
    cost_estimate DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    final_outcome JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_detail TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ NULL,
    completed_at TIMESTAMPTZ NULL,
    CONSTRAINT chk_agent_run_status CHECK (status IN (
        'pending', 'running', 'waiting_approval', 'waiting_external',
        'paused', 'completed', 'failed', 'timed_out', 'cancelled', 'unknown'
    ))
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_tenant_status ON agent_runs(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_agent_runs_parent ON agent_runs(parent_run_id);

-- 2. Agent Run Steps Table
CREATE TABLE IF NOT EXISTS agent_run_steps (
    step_id VARCHAR(128) PRIMARY KEY,
    run_id VARCHAR(128) NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    step_number INTEGER NOT NULL,
    phase VARCHAR(32) NOT NULL,
    plan_action VARCHAR(128) NOT NULL,
    action_type VARCHAR(64) NOT NULL,
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(32) NOT NULL DEFAULT 'requested',
    verification_status VARCHAR(32) NOT NULL DEFAULT 'unverified',
    approval_token VARCHAR(128) NULL,
    duration_ms DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_steps_run ON agent_run_steps(run_id, step_number);

-- 3. Agent Delegations Table
CREATE TABLE IF NOT EXISTS agent_delegations (
    delegation_id VARCHAR(128) PRIMARY KEY,
    parent_run_id VARCHAR(128) NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    child_run_id VARCHAR(128) NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(64) NOT NULL,
    capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    budget_allocated JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_delegations_parent ON agent_delegations(parent_run_id);

-- 4. Agent Mesh Events Table
CREATE TABLE IF NOT EXISTS agent_mesh_events (
    event_id VARCHAR(128) PRIMARY KEY,
    run_id VARCHAR(128) NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_type VARCHAR(64) NOT NULL,
    phase VARCHAR(32) NOT NULL,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_events_run ON agent_mesh_events(run_id, created_at);

-- 5. Agent Mesh Audits Table
CREATE TABLE IF NOT EXISTS agent_mesh_audits (
    audit_id VARCHAR(128) PRIMARY KEY,
    run_id VARCHAR(128) NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    tenant_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action VARCHAR(128) NOT NULL,
    principal_id VARCHAR(128) NOT NULL,
    risk_level VARCHAR(32) NOT NULL DEFAULT 'low',
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_audits_tenant ON agent_mesh_audits(tenant_id, created_at);
