-- M53 — Proactive Automations, Scheduled Triggers & Autonomous Supervisor
-- Introduces relational storage for automations, execution runs, and atomic hourly quotas.
-- Enforces tenant ownership, durable lease mechanics, and slot-based deduplication.

-- 1. Automations Table
CREATE TABLE IF NOT EXISTS automations (
    id VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    trigger_type VARCHAR(32) NOT NULL,
    trigger_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    condition_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    action_template JSONB NOT NULL DEFAULT '{}'::jsonb,
    next_fire_at TIMESTAMPTZ NULL,
    last_fired_at TIMESTAMPTZ NULL,
    fire_count INTEGER NOT NULL DEFAULT 0,
    max_runs INTEGER NULL,
    cooldown_seconds INTEGER NOT NULL DEFAULT 60,
    lease_owner VARCHAR(128) NULL,
    lease_token VARCHAR(128) NULL,
    claimed_at TIMESTAMPTZ NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_automation_status CHECK (
        status IN ('active', 'paused', 'disabled', 'completed', 'expired', 'failed')
    ),
    CONSTRAINT chk_automation_trigger_type CHECK (
        trigger_type IN ('one_time', 'recurring', 'time_window', 'condition', 'event')
    )
);

CREATE INDEX IF NOT EXISTS idx_automations_user_status ON automations(user_id, status);
CREATE INDEX IF NOT EXISTS idx_automations_due_lease ON automations(status, next_fire_at ASC, lease_expires_at) 
    WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_automations_user_created ON automations(user_id, created_at DESC);

-- 2. Tenant Hourly Usage Table (Atomic Quota Enforcement)
CREATE TABLE IF NOT EXISTS tenant_hourly_usage (
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    hour_bucket TIMESTAMPTZ NOT NULL,
    run_count INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, hour_bucket)
);

-- 3. Automation Runs Table (Slot Deduplication & Linkage)
CREATE TABLE IF NOT EXISTS automation_runs (
    id VARCHAR(128) PRIMARY KEY,
    automation_id VARCHAR(128) NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_id VARCHAR(128) NULL REFERENCES tasks(id) ON DELETE SET NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'claimed',
    trigger_timestamp TIMESTAMPTZ NOT NULL,
    slot_timestamp TIMESTAMPTZ NOT NULL,
    lease_token VARCHAR(128) NULL,
    condition_evaluation JSONB NULL,
    error_message TEXT NULL,
    reconciled_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ NULL,
    CONSTRAINT chk_run_status CHECK (
        status IN ('claimed', 'evaluating', 'condition_failed', 'enqueued', 'completed', 'failed', 'skipped', 'policy_denied')
    ),
    CONSTRAINT uq_automation_runs_dedup UNIQUE (automation_id, slot_timestamp)
);

CREATE INDEX IF NOT EXISTS idx_automation_runs_user_status ON automation_runs(user_id, status);
CREATE INDEX IF NOT EXISTS idx_automation_runs_auto_created ON automation_runs(automation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_automation_runs_task ON automation_runs(task_id);
