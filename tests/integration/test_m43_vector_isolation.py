"""Integration tests for M43 Multi-Tenant Vector and Lexical Isolation.

Verifies that under both dense vector and sparse lexical retrieval modes,
User A can NEVER access User B's private documents, memories, or experiences.
"""

import pytest

from core.personal_state_types import MemoryCategory
from core.repositories.factory import create_in_memory_repositories
from core.retrieval_pipeline import AdvancedRetrievalPipeline
from core.retrieval_types import AuthorityTier, RetrievalQuery, RetrievalSourceType
from providers.embedding.factory import create_embedding_provider


def test_strict_multi_tenant_vector_and_lexical_isolation():
    repos = create_in_memory_repositories()
    prov = create_embedding_provider()

    pipeline = AdvancedRetrievalPipeline(
        embedding_provider=prov,
        knowledge_repo=repos.knowledge,
        vector_repo=repos.vectors,
        hybrid_alpha=0.7,
    )

    # 1. User Alice's private document
    pipeline.add_knowledge_document(
        doc_id="alice_doc_secret",
        title="Alice Confidential Strategy",
        content="Alice confidential financial planning and secret investments in quantum computing.",
        user_id="alice",
        visibility="private",
        authority=AuthorityTier.USER,
    )

    # 2. User Bob's private document
    pipeline.add_knowledge_document(
        doc_id="bob_doc_secret",
        title="Bob Confidential Research",
        content="Bob private biomedical research on CRISPR genetic sequence synthesis.",
        user_id="bob",
        visibility="private",
        authority=AuthorityTier.USER,
    )

    # 3. Global Public document
    pipeline.add_knowledge_document(
        doc_id="public_doc_general",
        title="Public Technology Encyclopedia",
        content="General overview of quantum computing, CRISPR genetic sequences, and modern artificial intelligence.",
        user_id=None,
        visibility="public",
        authority=AuthorityTier.VERIFIED,
    )

    # 4. User Alice's private memory
    mem_alice = repos.memories.record_memory(
        user_id="alice",
        category="semantic",
        content="Alice personal secret: bank account credentials and encryption passphrases.",
    )
    v_mem_a = prov.embed_text(mem_alice.content)
    repos.vectors.update_memory_embedding(mem_alice.record_id, "alice", v_mem_a)

    # 5. User Bob's private memory
    mem_bob = repos.memories.record_memory(
        user_id="bob",
        category="semantic",
        content="Bob personal secret: proprietary molecular formula for biochemical vaccine.",
    )
    v_mem_b = prov.embed_text(mem_bob.content)
    repos.vectors.update_memory_embedding(mem_bob.record_id, "bob", v_mem_b)

    # -------------------------------------------------------------
    # QUERY AS ALICE
    # -------------------------------------------------------------
    q_alice = RetrievalQuery(
        query_text="quantum computing financial investments and secret credentials",
        user_id="alice",
    )
    results_alice = pipeline.retrieve_candidates(q_alice)
    alice_doc_ids = [c.candidate_id for c in results_alice]
    alice_texts = " ".join(c.text for c in results_alice)

    # Alice SHOULD see her private doc, her memory, and the public doc
    assert any("alice_doc_secret" in cid for cid in alice_doc_ids) or "Alice confidential" in alice_texts
    # Alice MUST NOT see Bob's private doc or Bob's memory
    assert not any("bob_doc_secret" in cid for cid in alice_doc_ids)
    assert "molecular formula" not in alice_texts
    assert "biomedical research" not in alice_texts

    # -------------------------------------------------------------
    # QUERY AS BOB
    # -------------------------------------------------------------
    q_bob = RetrievalQuery(
        query_text="biomedical research CRISPR genetic sequence molecular formula",
        user_id="bob",
    )
    results_bob = pipeline.retrieve_candidates(q_bob)
    bob_doc_ids = [c.candidate_id for c in results_bob]
    bob_texts = " ".join(c.text for c in results_bob)

    # Bob SHOULD see his private doc, his memory, and the public doc
    assert any("bob_doc_secret" in cid for cid in bob_doc_ids) or "Bob private biomedical" in bob_texts
    # Bob MUST NOT see Alice's private doc or Alice's memory
    assert not any("alice_doc_secret" in cid for cid in bob_doc_ids)
    assert "financial planning" not in bob_texts
    assert "encryption passphrases" not in bob_texts

    # -------------------------------------------------------------
    # QUERY AS UNKNOWN / ANONYMOUS USER
    # -------------------------------------------------------------
    q_anon = RetrievalQuery(
        query_text="quantum computing CRISPR genetic sequence overview",
        user_id=None,
    )
    results_anon = pipeline.retrieve_candidates(q_anon)
    anon_doc_ids = [c.candidate_id for c in results_anon]
    anon_texts = " ".join(c.text for c in results_anon)

    # Anonymous user CANNOT see Alice's or Bob's private items
    assert not any("alice_doc_secret" in cid for cid in anon_doc_ids)
    assert not any("bob_doc_secret" in cid for cid in anon_doc_ids)
    assert "Alice confidential" not in anon_texts
    assert "Bob private" not in anon_texts
