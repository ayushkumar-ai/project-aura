"""M43 — Document Ingestion & Chunking Module for Project AURA."""

from core.ingestion.chunker import DocumentChunk, RecursiveCharacterChunker
from core.ingestion.pipeline import DocumentIngestionPipeline, IngestionResult

__all__ = [
    "DocumentChunk",
    "RecursiveCharacterChunker",
    "DocumentIngestionPipeline",
    "IngestionResult",
]
