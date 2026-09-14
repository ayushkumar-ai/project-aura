"""M43 — Deterministic Mock Embedding Provider for Project AURA.

Generates reproducible, unit-normalized float vectors for testing and offline development.
Vectors are deterministic and reflect token overlap semantic similarity.
"""

from __future__ import annotations

import hashlib
import math
import re
from interfaces.embedding import BaseEmbeddingProvider


class DeterministicMockEmbeddingProvider(BaseEmbeddingProvider):
    """Deterministic embedding provider for offline testing and development."""

    def __init__(
        self,
        dimension: int = 1536,
        model_name: str = "gemini-embedding-001",
    ) -> None:
        self._dimension = dimension
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def _generate_vector(self, text: str) -> list[float]:
        """Generate a deterministic unit-normalized vector of fixed dimension."""
        if self._dimension <= 0:
            raise ValueError(f"Embedding dimension must be positive, got {self._dimension}")

        vec = [0.0] * self._dimension
        tokens = re.findall(r"\b\w+\b", text.lower())

        # Base hash from entire string for overall fingerprint
        text_hash = hashlib.sha256(text.encode("utf-8")).digest()
        for i in range(min(len(text_hash), self._dimension)):
            vec[i] += (text_hash[i] / 255.0) * 0.1

        # Semantic projection based on token hashing
        for token in tokens:
            h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
            idx1 = h % self._dimension
            idx2 = (h >> 16) % self._dimension
            idx3 = (h >> 32) % self._dimension
            vec[idx1] += 1.0
            vec[idx2] += 0.5
            vec[idx3] += 0.25

        # L2 normalize
        norm_sq = sum(v * v for v in vec)
        norm = math.sqrt(norm_sq)
        if norm > 1e-9:
            vec = [v / norm for v in vec]
        else:
            vec[0] = 1.0

        return vec

    def embed_text(self, text: str) -> list[float]:
        """Generate embedding vector for a single text."""
        return self._generate_vector(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for a batch of texts."""
        return [self._generate_vector(t) for t in texts]
