"""M43 — Canonical Embedding Provider Interface for Project AURA.

Defines the abstract contract for generating vector embeddings, batching,
dimensionality constraints, and model versioning.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import time
from typing import Any


@dataclass(frozen=True)
class EmbeddingResult:
    """Encapsulates generated embedding vectors with model provenance metadata."""

    vectors: list[list[float]]
    model_name: str
    dimension: int
    version: str = "v1"
    token_count: int = 0
    duration_seconds: float = 0.0
    created_at: float = field(default_factory=time.time)


class BaseEmbeddingProvider(ABC):
    """Abstract contract for semantic embedding generation."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Name and version identifier of the active embedding model."""
        pass

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Fixed dimensionality of output embedding vectors (e.g. 1536)."""
        pass

    @abstractmethod
    def embed_text(self, text: str) -> list[float]:
        """Generate an embedding vector for a single text string."""
        pass

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for a batch of text strings."""
        pass

    def get_embedding_result(self, texts: list[str]) -> EmbeddingResult:
        """Generate embeddings and package into an EmbeddingResult with metadata."""
        t0 = time.time()
        vectors = self.embed_batch(texts)
        duration = time.time() - t0
        # Approximate token count (1 token ~= 4 characters)
        approx_tokens = sum(max(1, len(t) // 4) for t in texts)
        return EmbeddingResult(
            vectors=vectors,
            model_name=self.model_name,
            dimension=self.dimension,
            version="v1",
            token_count=approx_tokens,
            duration_seconds=duration,
        )
