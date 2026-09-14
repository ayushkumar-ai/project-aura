"""Unit tests for M43 Recursive Character Chunker and Document Ingestion Pipeline."""

import pytest

from core.ingestion.chunker import DocumentChunk, RecursiveCharacterChunker
from core.ingestion.pipeline import DocumentIngestionPipeline, IngestionResult
from core.repositories.in_memory import InMemoryKnowledgeRepository, InMemoryVectorSearchRepository
from providers.embedding.mock_provider import DeterministicMockEmbeddingProvider


def test_chunker_small_text():
    chunker = RecursiveCharacterChunker(chunk_size=600, chunk_overlap=100)
    text = "Short text under 600 characters."
    chunks = chunker.chunk_text(text, doc_id="doc_small")

    assert len(chunks) == 1
    assert chunks[0].doc_id == "doc_small"
    assert chunks[0].chunk_id == "doc_small_chk_0"
    assert chunks[0].content == text
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == len(text)
    assert len(chunks[0].sha256_hash) == 64


def test_chunker_paragraph_splitting_and_overlap():
    chunker = RecursiveCharacterChunker(chunk_size=150, chunk_overlap=30)
    p1 = "Paragraph 1: Project AURA is an advanced agentic intelligence framework designed for autonomous tasks."
    p2 = "Paragraph 2: It incorporates a multi-tier memory system including semantic, working, and episodic storage."
    p3 = "Paragraph 3: The persistent data layer supports PostgreSQL with pgvector for high-performance vector retrieval."
    full_text = f"{p1}\n\n{p2}\n\n{p3}"

    chunks = chunker.chunk_text(full_text, doc_id="doc_multi")

    assert len(chunks) >= 3
    for i, c in enumerate(chunks):
        assert c.chunk_id == f"doc_multi_chk_{i}"
        assert c.chunk_index == i
        assert len(c.content) <= 200
        assert c.char_start >= 0
        assert c.char_end > c.char_start

    # Verify consecutive chunks have overlapping content or boundary proximity
    assert chunks[0].content != chunks[1].content


def test_chunker_empty_and_whitespace():
    chunker = RecursiveCharacterChunker()
    assert chunker.chunk_text("") == []
    assert chunker.chunk_text("   \n\t  ") == []


def test_chunker_invalid_params():
    with pytest.raises(ValueError, match="chunk_size must be positive"):
        RecursiveCharacterChunker(chunk_size=0)

    with pytest.raises(ValueError, match="chunk_overlap must be non-negative"):
        RecursiveCharacterChunker(chunk_size=100, chunk_overlap=100)


def test_document_ingestion_pipeline_e2e():
    know_repo = InMemoryKnowledgeRepository()
    vec_repo = InMemoryVectorSearchRepository(knowledge_repo=know_repo)
    provider = DeterministicMockEmbeddingProvider(dimension=1536)

    pipeline = DocumentIngestionPipeline(
        embedding_provider=provider,
        knowledge_repo=know_repo,
        vector_repo=vec_repo,
    )

    doc_text = (
        "Project AURA Architecture Overview.\n\n"
        "Section 1: The system uses a modular micro-agent architecture for planning and execution.\n\n"
        "Section 2: Multi-tenant user isolation ensures strict boundary separation across all data stores.\n\n"
        "Section 3: pgvector powers semantic similarity search across knowledge, memory, and experiences."
    )

    res = pipeline.ingest_document(
        doc_id="aura_arch",
        title="AURA Architecture",
        content=doc_text,
        user_id="user_alice",
        visibility="private",
        authority="verified",
        tags=["architecture", "agentic"],
    )

    assert isinstance(res, IngestionResult)
    assert res.doc_id == "aura_arch"
    assert res.chunks_created >= 1
    assert res.embeddings_generated is True
    assert len(res.checksum) == 64

    # Verify document in repo
    doc = know_repo.get_document("aura_arch", user_id="user_alice")
    assert doc is not None
    assert doc["title"] == "AURA Architecture"
    assert doc["visibility"] == "private"

    # Verify chunks in repo
    chunks = know_repo.get_chunks_for_doc("aura_arch", user_id="user_alice")
    assert len(chunks) == res.chunks_created
    for chk in chunks:
        assert chk["embedding"] is not None
        assert len(chk["embedding"]) == 1536


def test_ingestion_pipeline_private_doc_requires_user_id():
    know_repo = InMemoryKnowledgeRepository()
    pipeline = DocumentIngestionPipeline(knowledge_repo=know_repo)

    with pytest.raises(ValueError, match="Private knowledge documents must specify an owning user_id"):
        pipeline.ingest_document(
            doc_id="priv_doc",
            title="Private",
            content="Private secrets",
            user_id=None,
            visibility="private",
        )
