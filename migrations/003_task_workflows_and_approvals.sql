-- M52 — Asynchronous Background Task Execution, Persistent Workflow Lifecycle & Human Approval Gateway
-- Introduces durable relational storage for tasks, task execution steps, and human approval requests.
-- Enforces composite tenant ownership, status constraints, and idempotency uniqueness per user.

-- 1. Tasks Table
CREATE TABLE IF NOT EXISTS tasks (
    id VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    goal TEXT NOT NULL,
    context JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    result JSONB NULL,
    error_message TEXT NULL,
    idempotency_key VARCHAR(128) NULL,
    timeout_seconds INTEGER NOT NULL DEFAULT 600,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ NULL,
    completed_at TIMESTAMPTZ NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_task_status CHECK (
        status IN ('pending', 'running', 'awaiting_approval', 'completed', 'failed', 'cancelled', 'timed_out')
    )
);

CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON tasks(user_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_status_created ON tasks(status, created_at ASC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_user_idempotency ON tasks(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL;

-- 2. Task Steps Table
CREATE TABLE IF NOT EXISTS task_steps (
    id VARCHAR(128) PRIMARY KEY,
    task_id VARCHAR(128) NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL,
    name VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    tool_name VARCHAR(128) NULL,
    tool_input JSONB NULL,
    tool_output JSONB NULL,
    error_message TEXT NULL,
    started_at TIMESTAMPTZ NULL,
    completed_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_step_status CHECK (
        status IN ('pending', 'running', 'completed', 'failed', 'skipped', 'awaiting_approval')
    ),
    CONSTRAINT uq_task_step_index UNIQUE (task_id, step_index)
);

CREATE INDEX IF NOT EXISTS idx_task_steps_task_id ON task_steps(task_id, step_index);
CREATE INDEX IF NOT EXISTS idx_task_steps_user_id ON task_steps(user_id);

-- 3. Approval Requests Table
CREATE TABLE IF NOT EXISTS approval_requests (
    id VARCHAR(128) PRIMARY KEY,
    task_id VARCHAR(128) NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    step_id VARCHAR(128) NULL REFERENCES task_steps(id) ON DELETE SET NULL,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action_type VARCHAR(64) NOT NULL,
    action_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    risk_level VARCHAR(16) NOT NULL DEFAULT 'medium',
    justification TEXT NOT NULL DEFAULT '',
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    nonce VARCHAR(64) NOT NULL,
    decision_reason TEXT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    decided_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_approval_status CHECK (
        status IN ('pending', 'approved', 'rejected', 'expired')
    ),
    CONSTRAINT chk_risk_level CHECK (
        risk_level IN ('low', 'medium', 'high', 'critical')
    )
);

CREATE INDEX IF NOT EXISTS idx_approvals_user_status ON approval_requests(user_id, status);
CREATE INDEX IF NOT EXISTS idx_approvals_task_id ON approval_requests(task_id);
CREATE INDEX IF NOT EXISTS idx_approvals_expires_at ON approval_requests(status, expires_at);
