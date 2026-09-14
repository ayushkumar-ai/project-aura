"""Unit tests for M43 In-Memory and Vector Search Repositories."""

import pytest

from core.personal_state_types import MemoryCategory
from core.repositories.in_memory import (
    InMemoryExperienceRepository,
    InMemoryKnowledgeRepository,
    InMemoryMemoryRepository,
    InMemoryVectorSearchRepository,
)
from providers.embedding.mock_provider import DeterministicMockEmbeddingProvider


def test_in_memory_knowledge_repository_crud():
    repo = InMemoryKnowledgeRepository()

    # Create doc
    doc = repo.save_document(
        doc_id="doc_1",
        title="Doc One",
        content="Content of document one.",
        doc_checksum="abc123hash",
        user_id="user_1",
        visibility="private",
        authority="verified",
        tags=["tag1", "tag2"],
    )
    assert doc["id"] == "doc_1"
    assert doc["visibility"] == "private"

    # Fetch doc as owner
    assert repo.get_document("doc_1", user_id="user_1") is not None

    # Fetch doc as other user -> None
    assert repo.get_document("doc_1", user_id="user_2") is None

    # Public doc
    repo.save_document(
        doc_id="doc_public",
        title="Public Doc",
        content="Public content",
        doc_checksum="def456hash",
        user_id=None,
        visibility="public",
    )
    assert repo.get_document("doc_public", user_id="user_2") is not None
    assert repo.get_document("doc_public", user_id=None) is not None

    # Save chunks
    chunks_saved = repo.save_chunks([
        {
            "id": "doc_1_chk_0",
            "doc_id": "doc_1",
            "user_id": "user_1",
            "chunk_index": 0,
            "content": "Content of document one.",
            "char_start": 0,
            "char_end": 25,
            "chunk_hash": "chk0hash",
            "embedding": [0.5] * 1536,
            "metadata": {},
        }
    ], user_id="user_1")
    assert chunks_saved == 1

    # Get chunks
    chunks = repo.get_chunks_for_doc("doc_1", user_id="user_1")
    assert len(chunks) == 1

    # Delete doc cascades chunks
    assert repo.delete_document("doc_1", user_id="user_1") is True
    assert repo.get_document("doc_1", user_id="user_1") is None
    assert len(repo.get_chunks_for_doc("doc_1", user_id="user_1")) == 0


def test_vector_search_knowledge_chunks():
    know_repo = InMemoryKnowledgeRepository()
    vec_repo = InMemoryVectorSearchRepository(knowledge_repo=know_repo)
    prov = DeterministicMockEmbeddingProvider(dimension=1536)

    # Ingest 2 docs
    v_ai = prov.embed_text("Deep neural networks and artificial intelligence")
    v_cook = prov.embed_text("Culinary arts and traditional pasta recipes")

    know_repo.save_document(
        doc_id="doc_ai",
        title="AI Overview",
        content="Deep neural networks and artificial intelligence",
        doc_checksum="hash_ai",
        user_id="user_alice",
        visibility="private",
    )
    know_repo.save_chunks([{
        "id": "doc_ai_chk_0",
        "doc_id": "doc_ai",
        "user_id": "user_alice",
        "chunk_index": 0,
        "content": "Deep neural networks and artificial intelligence",
        "embedding": v_ai,
    }])

    know_repo.save_document(
        doc_id="doc_cook",
        title="Cooking",
        content="Culinary arts and traditional pasta recipes",
        doc_checksum="hash_cook",
        user_id=None,
        visibility="public",
    )
    know_repo.save_chunks([{
        "id": "doc_cook_chk_0",
        "doc_id": "doc_cook",
        "user_id": None,
        "chunk_index": 0,
        "content": "Culinary arts and traditional pasta recipes",
        "embedding": v_cook,
    }])

    # Search with AI query as Alice
    q_ai = prov.embed_text("neural networks AI intelligence")
    results = vec_repo.search_knowledge_chunks(q_ai, user_id="user_alice", limit=5)
    assert len(results) >= 1
    assert results[0]["doc_id"] == "doc_ai"
    assert results[0]["similarity"] > 0.4

    # Search with AI query as Bob (cannot see Alice's private doc)
    results_bob = vec_repo.search_knowledge_chunks(q_ai, user_id="user_bob", limit=5)
    assert not any(r["doc_id"] == "doc_ai" for r in results_bob)


def test_vector_search_memories_and_experiences():
    mem_repo = InMemoryMemoryRepository()
    exp_repo = InMemoryExperienceRepository()
    vec_repo = InMemoryVectorSearchRepository(memory_repo=mem_repo, experience_repo=exp_repo)
    prov = DeterministicMockEmbeddingProvider(dimension=1536)

    # Create memory for User A
    mem_a = mem_repo.record_memory(
        user_id="user_a",
        category="semantic",
        content="User prefers PostgreSQL over MySQL for JSONB support.",
    )
    v_mem_a = prov.embed_text("User prefers PostgreSQL over MySQL for JSONB support.")
    assert vec_repo.update_memory_embedding(mem_a.record_id, "user_a", v_mem_a) is True

    # Search memories as User A
    q_vec = prov.embed_text("PostgreSQL database preference")
    results_a = vec_repo.search_memories(q_vec, user_id="user_a", limit=5)
    assert len(results_a) == 1
    assert results_a[0]["memory_id"] == mem_a.record_id
    assert results_a[0]["similarity"] > 0.3

    # Search memories as User B (must receive 0 results)
    results_b = vec_repo.search_memories(q_vec, user_id="user_b", limit=5)
    assert len(results_b) == 0

    # Create experience for User A
    exp_a = exp_repo.record_experience(
        user_id="user_a",
        task_description="Configure pgvector extension",
        plan_summary="Run CREATE EXTENSION vector",
        outcome="success",
        reward_score=0.95,
    )
    v_exp_a = prov.embed_text("Configure pgvector extension Run CREATE EXTENSION vector")
    assert vec_repo.update_experience_embedding(exp_a.experience_id, "user_a", v_exp_a) is True

    # Search experiences
    exp_res_a = vec_repo.search_experiences(q_vec, user_id="user_a", limit=5)
    assert len(exp_res_a) == 1
    assert exp_res_a[0]["experience_id"] == exp_a.experience_id

    exp_res_b = vec_repo.search_experiences(q_vec, user_id="user_b", limit=5)
    assert len(exp_res_b) == 0
