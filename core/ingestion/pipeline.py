"""M43 — Document Ingestion Pipeline for Project AURA.

Coordinates document validation, checksum verification, recursive chunking,
batch embedding generation, and atomic persistence into repositories with strict user isolation.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any

from core.ingestion.chunker import DocumentChunk, RecursiveCharacterChunker
from interfaces.embedding import BaseEmbeddingProvider

logger = logging.getLogger("aura.ingestion.pipeline")


@dataclass(frozen=True)
class IngestionResult:
    """Outcome metadata for document ingestion."""

    doc_id: str
    chunks_created: int
    total_chars: int
    checksum: str
    embeddings_generated: bool
    duration_seconds: float


class DocumentIngestionPipeline:
    """Orchestrates end-to-end knowledge document ingestion, chunking, and embedding."""

    def __init__(
        self,
        chunker: RecursiveCharacterChunker | None = None,
        embedding_provider: BaseEmbeddingProvider | None = None,
        knowledge_repo: Any | None = None,
        vector_repo: Any | None = None,
    ) -> None:
        self.chunker = chunker or RecursiveCharacterChunker()
        self.embedding_provider = embedding_provider
        self.knowledge_repo = knowledge_repo
        self.vector_repo = vector_repo

    def ingest_document(
        self,
        doc_id: str,
        title: str,
        content: str,
        user_id: str | None = None,
        visibility: str = "public",
        authority: str = "verified",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        force_reindex: bool = False,
    ) -> IngestionResult:
        """Ingest, chunk, embed, and persist a knowledge document."""
        t0 = time.time()
        vis = visibility.lower().strip()
        if vis not in ("public", "private"):
            raise ValueError(f"Visibility must be 'public' or 'private', got '{visibility}'")

        if vis == "private" and not user_id:
            raise ValueError("Private knowledge documents must specify an owning user_id.")

        doc_checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
        tags_list = list(tags or [])
        meta_dict = dict(metadata or {})

        # Check existing document if repository is attached
        if self.knowledge_repo is not None and not force_reindex:
            existing = self.knowledge_repo.get_document(doc_id=doc_id, user_id=user_id)
            if existing and existing.get("doc_checksum") == doc_checksum:
                chunks = self.knowledge_repo.get_chunks_for_doc(doc_id=doc_id, user_id=user_id)
                if chunks:
                    logger.info(f"Document {doc_id} checksum unchanged ({doc_checksum[:8]}); skipping re-indexing.")
                    return IngestionResult(
                        doc_id=doc_id,
                        chunks_created=len(chunks),
                        total_chars=len(content),
                        checksum=doc_checksum,
                        embeddings_generated=True,
                        duration_seconds=time.time() - t0,
                    )

        # 1. Persist Document Header
        if self.knowledge_repo is not None:
            self.knowledge_repo.save_document(
                doc_id=doc_id,
                title=title,
                content=content,
                doc_checksum=doc_checksum,
                user_id=user_id,
                visibility=vis,
                authority=authority,
                tags=tags_list,
                metadata=meta_dict,
            )

        # 2. Chunk Document
        doc_meta = {**meta_dict, "title": title, "authority": authority, "tags": tags_list}
        chunks = self.chunker.chunk_text(text=content, doc_id=doc_id, metadata=doc_meta)

        # 3. Generate Vector Embeddings
        embeddings: list[list[float]] | None = None
        if self.embedding_provider is not None and chunks:
            try:
                chunk_texts = [c.content for c in chunks]
                embeddings = self.embedding_provider.embed_batch(chunk_texts)
            except Exception as e:
                logger.error(f"Failed to generate embeddings during ingestion for doc {doc_id}: {e}")
                embeddings = None

        # 4. Prepare and Save Chunk Records
        chunk_dicts: list[dict[str, Any]] = []
        for i, chk in enumerate(chunks):
            vec = embeddings[i] if embeddings and i < len(embeddings) else None
            chunk_dicts.append({
                "id": chk.chunk_id,
                "doc_id": doc_id,
                "user_id": user_id,
                "chunk_index": chk.chunk_index,
                "content": chk.content,
                "char_start": chk.char_start,
                "char_end": chk.char_end,
                "chunk_hash": chk.sha256_hash,
                "embedding": vec,
                "metadata": chk.metadata,
            })

        if self.knowledge_repo is not None:
            # Clear old chunks first if reindexing
            self.knowledge_repo.delete_chunks_for_doc(doc_id=doc_id, user_id=user_id)
            self.knowledge_repo.save_chunks(chunks=chunk_dicts, user_id=user_id)

        duration = time.time() - t0
        logger.info(f"Ingested doc {doc_id} into {len(chunks)} chunks in {duration:.3f}s (embedded={bool(embeddings)})")

        return IngestionResult(
            doc_id=doc_id,
            chunks_created=len(chunks),
            total_chars=len(content),
            checksum=doc_checksum,
            embeddings_generated=bool(embeddings),
            duration_seconds=duration,
        )
