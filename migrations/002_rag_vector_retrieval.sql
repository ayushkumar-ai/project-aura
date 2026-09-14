-- M43 — Production RAG & Vector Retrieval Schema for Project AURA
-- Introduces pgvector extension, knowledge documents, chunked embeddings,
-- and vector columns on user memories and experiences with HNSW cosine indexes.

CREATE EXTENSION IF NOT EXISTS vector;

-- Knowledge Documents table (canonical document storage with user isolation and visibility controls)
CREATE TABLE IF NOT EXISTS knowledge_documents (
    id VARCHAR(128) PRIMARY KEY,
    user_id VARCHAR(128) NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    doc_checksum VARCHAR(64) NOT NULL,
    visibility VARCHAR(32) NOT NULL DEFAULT 'public' CHECK (visibility IN ('public', 'private')),
    authority VARCHAR(32) NOT NULL DEFAULT 'verified',
    tags JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_know_doc_user_id ON knowledge_documents(user_id);
CREATE INDEX IF NOT EXISTS idx_know_doc_visibility ON knowledge_documents(visibility);
CREATE INDEX IF NOT EXISTS idx_know_doc_checksum ON knowledge_documents(doc_checksum);

-- Document Chunks table (semantic chunks with vector embeddings and tenant ownership)
CREATE TABLE IF NOT EXISTS document_chunks (
    id VARCHAR(128) PRIMARY KEY,
    doc_id VARCHAR(128) NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    user_id VARCHAR(128) NULL REFERENCES users(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    char_start INTEGER NOT NULL DEFAULT 0,
    char_end INTEGER NOT NULL DEFAULT 0,
    chunk_hash VARCHAR(64) NOT NULL,
    embedding vector(1536) NULL,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_chunks_doc_idx UNIQUE (doc_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON document_chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_user_id ON document_chunks(user_id);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw ON document_chunks USING hnsw (embedding vector_cosine_ops);

-- User Memories vector embedding extension
ALTER TABLE user_memories ADD COLUMN IF NOT EXISTS embedding vector(1536) NULL;
CREATE INDEX IF NOT EXISTS idx_memories_embedding_hnsw ON user_memories USING hnsw (embedding vector_cosine_ops);

-- User Experiences vector embedding extension
ALTER TABLE user_experiences ADD COLUMN IF NOT EXISTS embedding vector(1536) NULL;
CREATE INDEX IF NOT EXISTS idx_experiences_embedding_hnsw ON user_experiences USING hnsw (embedding vector_cosine_ops);
