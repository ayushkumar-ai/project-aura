-- M42 — Initial Relational Schema for Project AURA
-- Provides persistent storage for users, auth tokens, conversations, memories, experiences, and checkpoints.
-- Enforces composite foreign keys for conversation ownership integrity and strict user isolation.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version VARCHAR(64) PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    checksum VARCHAR(64) NOT NULL
);

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(128) PRIMARY KEY,
    username VARCHAR(128) NOT NULL UNIQUE,
    role VARCHAR(32) NOT NULL DEFAULT 'user',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

-- User Preferences table
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id VARCHAR(128) PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    preferences JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- API & Bearer Tokens (One-way sha256 hash storage)
CREATE TABLE IF NOT EXISTS api_tokens (
    id VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    prefix VARCHAR(16) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    rate_limit_rpm INTEGER DEFAULT 60,
    rate_limit_tpm INTEGER DEFAULT 60000
);

CREATE INDEX IF NOT EXISTS idx_api_tokens_hash ON api_tokens(token_hash);
CREATE INDEX IF NOT EXISTS idx_api_tokens_user_id ON api_tokens(user_id);

-- Conversations table (with composite unique key for multi-tenant ownership integrity)
CREATE TABLE IF NOT EXISTS conversations (
    id VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(255) DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB DEFAULT '{}',
    CONSTRAINT uq_conversations_id_user UNIQUE (id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_conversations_created_at ON conversations(created_at);

-- Conversation Turns table (enforces composite ownership foreign key)
CREATE TABLE IF NOT EXISTS conversation_turns (
    id VARCHAR(128) PRIMARY KEY,
    conversation_id VARCHAR(128) NOT NULL,
    user_id VARCHAR(128) NOT NULL,
    turn_index INTEGER NOT NULL,
    role VARCHAR(32) NOT NULL,
    content TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    model VARCHAR(128) DEFAULT '',
    metadata JSONB DEFAULT '{}',
    CONSTRAINT fk_turns_conv_user FOREIGN KEY (conversation_id, user_id) REFERENCES conversations(id, user_id) ON DELETE CASCADE,
    CONSTRAINT uq_turns_conv_idx UNIQUE (conversation_id, turn_index)
);

CREATE INDEX IF NOT EXISTS idx_turns_conv_id ON conversation_turns(conversation_id);
CREATE INDEX IF NOT EXISTS idx_turns_user_id ON conversation_turns(user_id);

-- User Memories table
CREATE TABLE IF NOT EXISTS user_memories (
    id VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category VARCHAR(64) NOT NULL,
    content TEXT NOT NULL,
    confidence FLOAT NOT NULL DEFAULT 1.0,
    importance FLOAT NOT NULL DEFAULT 0.5,
    tags JSONB DEFAULT '[]',
    provenance JSONB DEFAULT '{}',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_accessed TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_memories_user_id ON user_memories(user_id);
CREATE INDEX IF NOT EXISTS idx_memories_category ON user_memories(user_id, category);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON user_memories(user_id, importance DESC);

-- User Experiences table
CREATE TABLE IF NOT EXISTS user_experiences (
    id VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_description TEXT NOT NULL,
    plan_summary TEXT NOT NULL,
    action_sequence JSONB DEFAULT '[]',
    outcome VARCHAR(64) NOT NULL DEFAULT 'success',
    reward_score FLOAT NOT NULL DEFAULT 1.0,
    lessons_learned JSONB DEFAULT '[]',
    provenance JSONB DEFAULT '{}',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_experiences_user_id ON user_experiences(user_id);
CREATE INDEX IF NOT EXISTS idx_experiences_outcome ON user_experiences(user_id, outcome);
CREATE INDEX IF NOT EXISTS idx_experiences_reward ON user_experiences(user_id, reward_score DESC);

-- Runtime Checkpoints (user_id is NULL for system-level checkpoints, set for user state checkpoints)
CREATE TABLE IF NOT EXISTS runtime_checkpoints (
    id VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(128) NULL REFERENCES users(id) ON DELETE CASCADE,
    checkpoint_type VARCHAR(64) NOT NULL,
    state_payload JSONB NOT NULL,
    checksum VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_checkpoints_user_id ON runtime_checkpoints(user_id);
CREATE INDEX IF NOT EXISTS idx_checkpoints_type ON runtime_checkpoints(checkpoint_type);
CREATE INDEX IF NOT EXISTS idx_checkpoints_created ON runtime_checkpoints(created_at DESC);
