"""M43 — Recursive Character Chunker for Project AURA RAG.

Splits documents into semantic, overlapping chunks while respecting sentence,
paragraph, and word boundaries with character-offset tracking and SHA-256 fingerprinting.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DocumentChunk:
    """Represents a discrete semantic chunk of an ingested knowledge document."""

    chunk_id: str
    doc_id: str
    chunk_index: int
    content: str
    char_start: int
    char_end: int
    sha256_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)


class RecursiveCharacterChunker:
    """Recursively splits text using hierarchical separators with sliding overlap."""

    def __init__(
        self,
        chunk_size: int = 600,
        chunk_overlap: int = 100,
        separators: list[str] | None = None,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError(f"chunk_size must be positive, got {chunk_size}")
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            raise ValueError(
                f"chunk_overlap must be non-negative and strictly less than chunk_size ({chunk_size}), got {chunk_overlap}"
            )

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.separators = separators or ["\n\n", "\n", ". ", "? ", "! ", " ", ""]

    def _split_text(self, text: str, separators: list[str]) -> list[str]:
        """Recursively split text using the first matching separator."""
        final_chunks: list[str] = []
        separator = separators[-1]
        new_separators = []

        for i, sep in enumerate(separators):
            if sep == "":
                separator = ""
                break
            if sep in text:
                separator = sep
                new_separators = separators[i + 1 :]
                break

        splits = text.split(separator) if separator else list(text)

        good_splits: list[str] = []
        for s in splits:
            if not s:
                continue
            if len(s) < self.chunk_size:
                good_splits.append(s)
            else:
                if new_separators:
                    other_splits = self._split_text(s, new_separators)
                    good_splits.extend(other_splits)
                else:
                    # Character hard cut if no more separators
                    for j in range(0, len(s), self.chunk_size):
                        good_splits.append(s[j : j + self.chunk_size])

        return good_splits

    def chunk_text(
        self,
        text: str,
        doc_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> list[DocumentChunk]:
        """Split text into overlapping DocumentChunk instances with character offsets."""
        if not text or not text.strip():
            return []

        base_meta = dict(metadata or {})
        raw_splits = self._split_text(text, self.separators)

        # Merge splits into chunks of size ~chunk_size with overlap
        chunks: list[str] = []
        current_chunk: list[str] = []
        current_len = 0

        for piece in raw_splits:
            piece_len = len(piece)
            if current_len + piece_len > self.chunk_size and current_chunk:
                merged = " ".join(current_chunk).strip()
                chunks.append(merged)

                # Keep overlap from tail of current_chunk
                overlap_pieces: list[str] = []
                overlap_len = 0
                for p in reversed(current_chunk):
                    if overlap_len + len(p) <= self.chunk_overlap:
                        overlap_pieces.insert(0, p)
                        overlap_len += len(p)
                    else:
                        break
                current_chunk = overlap_pieces
                current_len = overlap_len

            current_chunk.append(piece)
            current_len += piece_len

        if current_chunk:
            merged = " ".join(current_chunk).strip()
            if not chunks or chunks[-1] != merged:
                chunks.append(merged)

        # Build DocumentChunk objects with char offsets
        result_chunks: list[DocumentChunk] = []
        search_start = 0

        for idx, chunk_text_content in enumerate(chunks):
            # Locate chunk in original text for accurate offset
            # First look from search_start, if not found search from 0
            pos = text.find(chunk_text_content[: min(30, len(chunk_text_content))], search_start)
            if pos == -1:
                pos = text.find(chunk_text_content[: min(30, len(chunk_text_content))], 0)
            if pos == -1:
                pos = search_start

            char_start = pos
            char_end = char_start + len(chunk_text_content)
            search_start = max(search_start, char_start + 1)

            sha256_hash = hashlib.sha256(chunk_text_content.encode("utf-8")).hexdigest()
            chunk_id = f"{doc_id}_chk_{idx}" if doc_id else f"chk_{idx}"

            result_chunks.append(
                DocumentChunk(
                    chunk_id=chunk_id,
                    doc_id=doc_id,
                    chunk_index=idx,
                    content=chunk_text_content,
                    char_start=char_start,
                    char_end=char_end,
                    sha256_hash=sha256_hash,
                    metadata=dict(base_meta),
                )
            )

        return result_chunks
