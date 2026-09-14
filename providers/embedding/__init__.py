"""M43 — Embedding Providers for Project AURA."""

from interfaces.embedding import BaseEmbeddingProvider, EmbeddingResult
from providers.embedding.mock_provider import DeterministicMockEmbeddingProvider
from providers.embedding.openai_provider import OpenAICompatibleEmbeddingProvider
from providers.embedding.factory import create_embedding_provider

__all__ = [
    "BaseEmbeddingProvider",
    "EmbeddingResult",
    "DeterministicMockEmbeddingProvider",
    "OpenAICompatibleEmbeddingProvider",
    "create_embedding_provider",
]
