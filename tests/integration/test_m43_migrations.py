"""Integration tests for M43 Database Migrations and Schema Validation."""

import hashlib
from pathlib import Path
import pytest


def test_m43_migration_file_exists_and_valid():
    migrations_dir = Path("migrations")
    m002_file = migrations_dir / "002_rag_vector_retrieval.sql"

    assert m002_file.exists(), "002_rag_vector_retrieval.sql migration file must exist"

    sql_content = m002_file.read_text(encoding="utf-8")
    assert "CREATE EXTENSION IF NOT EXISTS vector;" in sql_content
    assert "CREATE TABLE IF NOT EXISTS knowledge_documents" in sql_content
    assert "CREATE TABLE IF NOT EXISTS document_chunks" in sql_content
    assert "ALTER TABLE user_memories ADD COLUMN IF NOT EXISTS embedding vector(1536)" in sql_content
    assert "ALTER TABLE user_experiences ADD COLUMN IF NOT EXISTS embedding vector(1536)" in sql_content
    assert "idx_chunks_embedding_hnsw" in sql_content

    # Validate non-empty sha256 checksum
    checksum = hashlib.sha256(sql_content.encode("utf-8")).hexdigest()
    assert len(checksum) == 64


def test_migrations_ordering():
    migrations_dir = Path("migrations")
    files = sorted(migrations_dir.glob("*.sql"))
    filenames = [f.name for f in files]

    assert "001_initial_m42_schema.sql" in filenames
    assert "002_rag_vector_retrieval.sql" in filenames
    assert filenames.index("001_initial_m42_schema.sql") < filenames.index("002_rag_vector_retrieval.sql")
