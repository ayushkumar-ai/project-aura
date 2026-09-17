-- M54 — Enterprise Webhooks & Inbound/Outbound Event Gateway
-- Schema for inbound endpoints, encrypted signing keys, event persistence, capacities, and delivery queues.

-- 1. Tenant Webhook Capacities (Authoritative Admission Row Lock & Operational Counter)
CREATE TABLE IF NOT EXISTS tenant_webhook_capacities (
    user_id VARCHAR(128) PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    in_flight_count INTEGER NOT NULL DEFAULT 0,
    max_capacity INTEGER NOT NULL DEFAULT 100,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_in_flight_bounds CHECK (in_flight_count >= 0 AND in_flight_count <= max_capacity)
);

-- 2. Webhook Endpoints
CREATE TABLE IF NOT EXISTS webhook_endpoints (
    id VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    path_suffix VARCHAR(128) NOT NULL,
    allowed_event_types JSONB NOT NULL DEFAULT '["*"]'::jsonb,
    rate_limit_per_minute INTEGER NOT NULL DEFAULT 120,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_endpoint_status CHECK (status IN ('active', 'disabled', 'revoked')),
    CONSTRAINT uq_user_endpoint_path UNIQUE (user_id, path_suffix)
);

CREATE INDEX IF NOT EXISTS idx_webhook_endpoints_user ON webhook_endpoints(user_id, status);

-- 3. Webhook Signing Keys (Encrypted Secrets & Dual-Key Rotation)
CREATE TABLE IF NOT EXISTS webhook_signing_keys (
    id VARCHAR(128) PRIMARY KEY,
    endpoint_id VARCHAR(128) NOT NULL REFERENCES webhook_endpoints(id) ON DELETE CASCADE,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_version INTEGER NOT NULL DEFAULT 1,
    encrypted_secret JSONB NOT NULL,
    key_status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NULL,
    revoked_at TIMESTAMPTZ NULL,
    CONSTRAINT chk_key_status CHECK (key_status IN ('active', 'retiring', 'revoked', 'expired'))
);

CREATE INDEX IF NOT EXISTS idx_signing_keys_endpoint ON webhook_signing_keys(endpoint_id, key_status);

-- 4. Inbound Events (Durable Persistence, Retry State & Collision Defense)
CREATE TABLE IF NOT EXISTS inbound_events (
    id VARCHAR(128) PRIMARY KEY,
    endpoint_id VARCHAR(128) NOT NULL REFERENCES webhook_endpoints(id) ON DELETE CASCADE,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider_event_id VARCHAR(255) NOT NULL,
    payload_sha256 VARCHAR(64) NOT NULL,
    event_type VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'accepted',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_attempt_at TIMESTAMPTZ NULL,
    payload JSONB NOT NULL,
    headers JSONB NOT NULL DEFAULT '{}'::jsonb,
    signature VARCHAR(255) NOT NULL,
    lease_owner VARCHAR(128) NULL,
    lease_token VARCHAR(128) NULL,
    claimed_at TIMESTAMPTZ NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    task_id VARCHAR(128) NULL REFERENCES tasks(id) ON DELETE SET NULL,
    error_message TEXT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMPTZ NULL,
    CONSTRAINT chk_inbound_status CHECK (status IN ('accepted', 'processing', 'processed', 'failed', 'dead_lettered')),
    CONSTRAINT uq_inbound_events_dedup UNIQUE (endpoint_id, provider_event_id)
);

CREATE INDEX IF NOT EXISTS idx_inbound_events_user ON inbound_events(user_id, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_inbound_events_lease ON inbound_events(status, next_attempt_at ASC, lease_expires_at)
    WHERE status IN ('accepted', 'processing', 'failed');

-- 5. Event Subscriptions
CREATE TABLE IF NOT EXISTS event_subscriptions (
    id VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    event_type_filter VARCHAR(128) NOT NULL,
    target_type VARCHAR(32) NOT NULL,
    target_url TEXT NULL,
    signing_secret_encrypted JSONB NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_sub_target CHECK (target_type IN ('webhook', 'task', 'automation')),
    CONSTRAINT chk_sub_status CHECK (status IN ('active', 'paused', 'disabled'))
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_user_type ON event_subscriptions(user_id, event_type_filter, status);

-- 6. Event Deliveries (Outbound Queue, Lineage & Lease Fencing)
CREATE TABLE IF NOT EXISTS event_deliveries (
    id VARCHAR(128) PRIMARY KEY,
    subscription_id VARCHAR(128) NOT NULL REFERENCES event_subscriptions(id) ON DELETE CASCADE,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    event_id VARCHAR(128) NOT NULL,
    target_url TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_attempt_at TIMESTAMPTZ NULL,
    last_response_status INTEGER NULL,
    last_error TEXT NULL,
    lease_owner VARCHAR(128) NULL,
    lease_token VARCHAR(128) NULL,
    claimed_at TIMESTAMPTZ NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    payload JSONB NOT NULL,
    causation_id VARCHAR(128) NULL,
    replayed_from_dead_letter_id VARCHAR(128) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ NULL,
    CONSTRAINT chk_delivery_status CHECK (status IN ('pending', 'delivering', 'delivered', 'retrying', 'failed', 'dead_lettered'))
);

CREATE INDEX IF NOT EXISTS idx_deliveries_due_lease ON event_deliveries(status, next_attempt_at ASC, lease_expires_at)
    WHERE status IN ('pending', 'retrying');
CREATE INDEX IF NOT EXISTS idx_deliveries_user_created ON event_deliveries(user_id, created_at DESC);

-- 7. Dead Letter Events (Unified 100% Write-Once Immutable Historical Ledger)
CREATE TABLE IF NOT EXISTS dead_letter_events (
    id VARCHAR(128) PRIMARY KEY,
    dead_letter_type VARCHAR(32) NOT NULL,
    inbound_event_id VARCHAR(128) NULL REFERENCES inbound_events(id) ON DELETE CASCADE,
    delivery_id VARCHAR(128) NULL REFERENCES event_deliveries(id) ON DELETE CASCADE,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reason VARCHAR(64) NOT NULL,
    final_error TEXT NOT NULL,
    attempts INTEGER NOT NULL,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_dl_type CHECK (dead_letter_type IN ('inbound', 'outbound')),
    CONSTRAINT chk_dl_source CHECK (
        (dead_letter_type = 'inbound' AND inbound_event_id IS NOT NULL AND delivery_id IS NULL) OR
        (dead_letter_type = 'outbound' AND delivery_id IS NOT NULL AND inbound_event_id IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_dead_letter_user ON dead_letter_events(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dead_letter_type ON dead_letter_events(dead_letter_type, created_at DESC);

-- 8. Dead Letter Replays (Dedicated Replay Audit Trail)
CREATE TABLE IF NOT EXISTS dead_letter_replays (
    id VARCHAR(128) PRIMARY KEY,
    dead_letter_id VARCHAR(128) NOT NULL REFERENCES dead_letter_events(id) ON DELETE CASCADE,
    new_delivery_id VARCHAR(128) NOT NULL REFERENCES event_deliveries(id) ON DELETE CASCADE,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    replayed_by VARCHAR(128) NOT NULL,
    replayed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reason TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_dead_letter_replays_dl ON dead_letter_replays(dead_letter_id);
