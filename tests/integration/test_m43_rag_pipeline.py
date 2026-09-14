"""Integration tests for M43 End-to-End Production RAG Pipeline."""

import pytest

from core.personal_state_types import MemoryCategory
from core.repositories.factory import create_in_memory_repositories
from core.retrieval_pipeline import AdvancedRetrievalPipeline
from core.retrieval_types import AuthorityTier, RetrievalQuery, RetrievalSourceType
from providers.embedding.factory import create_embedding_provider


def test_e2e_rag_pipeline_integration():
    repos = create_in_memory_repositories()
    prov = create_embedding_provider()

    pipeline = AdvancedRetrievalPipeline(
        embedding_provider=prov,
        knowledge_repo=repos.knowledge,
        vector_repo=repos.vectors,
        hybrid_alpha=0.7,
    )

    # 1. Ingest knowledge documents
    pipeline.add_knowledge_document(
        doc_id="arch_spec",
        title="AURA Architecture Specification",
        content="Project AURA delivers autonomous agent orchestration, durable memory, and hybrid RAG retrieval.",
        authority=AuthorityTier.VERIFIED,
        tags=["architecture", "rag", "system"],
    )

    pipeline.add_knowledge_document(
        doc_id="security_spec",
        title="Security & Isolation Model",
        content="User data isolation is enforced at the database layer with composite foreign keys and tenant filtering.",
        authority=AuthorityTier.SYSTEM,
        tags=["security", "isolation"],
    )

    # 2. Execute RAG query
    bundle = pipeline.execute_rag(
        query="hybrid RAG retrieval architecture specification",
        max_context_chars=2000,
    )

    assert bundle.query == "hybrid RAG retrieval architecture specification"
    assert len(bundle.candidates) >= 1
    assert "AURA Architecture Specification" in bundle.assembled_text
    assert "<retrieved_context>" in bundle.assembled_text
    assert "</retrieved_context>" in bundle.assembled_text
    assert bundle.retrieval_latency_ms >= 0.0


def test_rag_pipeline_with_memories_and_experiences():
    repos = create_in_memory_repositories()
    prov = create_embedding_provider()

    # Add memory for User 101
    mem = repos.memories.record_memory(
        user_id="user_101",
        category="semantic",
        content="User frequently develops in Rust and WebAssembly.",
    )
    v_mem = prov.embed_text(mem.content)
    repos.vectors.update_memory_embedding(mem.record_id, "user_101", v_mem)

    # Add experience for User 101
    exp = repos.experiences.record_experience(
        user_id="user_101",
        task_description="Build Rust WebAssembly module",
        plan_summary="wasm-pack build --target web",
        outcome="success",
        reward_score=1.0,
    )
    v_exp = prov.embed_text(f"{exp.task_description} {exp.plan_summary}")
    repos.vectors.update_experience_embedding(exp.experience_id, "user_101", v_exp)

    pipeline = AdvancedRetrievalPipeline(
        embedding_provider=prov,
        knowledge_repo=repos.knowledge,
        vector_repo=repos.vectors,
        hybrid_alpha=0.7,
    )

    # Query as User 101
    q_user = RetrievalQuery(
        query_text="Rust WebAssembly build wasm-pack",
        user_id="user_101",
        source_types=[RetrievalSourceType.PERSONAL_MEMORY, RetrievalSourceType.EXPERIENCES],
    )
    candidates = pipeline.retrieve_candidates(q_user)
    assert len(candidates) >= 2

    # Query as User 102 (different user -> 0 results)
    q_other = RetrievalQuery(
        query_text="Rust WebAssembly build wasm-pack",
        user_id="user_102",
        source_types=[RetrievalSourceType.PERSONAL_MEMORY, RetrievalSourceType.EXPERIENCES],
    )
    candidates_other = pipeline.retrieve_candidates(q_other)
    assert len(candidates_other) == 0
