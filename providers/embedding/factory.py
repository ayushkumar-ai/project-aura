"""M43 — Embedding Provider Factory for Project AURA.

Constructs and wires the appropriate embedding provider based on runtime configuration.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import Settings, settings
from interfaces.embedding import BaseEmbeddingProvider
from providers.embedding.mock_provider import DeterministicMockEmbeddingProvider
from providers.embedding.openai_provider import OpenAICompatibleEmbeddingProvider

logger = logging.getLogger("aura.providers.embedding.factory")


def create_embedding_provider(config: Settings | None = None) -> BaseEmbeddingProvider:
    """Construct configured embedding provider with proper endpoint and dimension resolution."""
    cfg = config or settings
    provider_type = getattr(cfg, "aura_embedding_provider", "mock").lower().strip()
    dimension = getattr(cfg, "aura_embedding_dimension", 1536)
    model_name = getattr(cfg, "aura_embedding_model_name", "gemini-embedding-001")
    batch_size = getattr(cfg, "aura_embedding_batch_size", 32)

    # Mock provider (used for offline development and deterministic unit tests)
    if provider_type == "mock":
        logger.info(f"Initializing DeterministicMockEmbeddingProvider (dimension={dimension}, model={model_name})")
        return DeterministicMockEmbeddingProvider(dimension=dimension, model_name=model_name)

    # Resolve API credentials and endpoint
    api_key = (
        getattr(cfg, "aura_embedding_api_key", "")
        or getattr(cfg, "aura_generic_model_api_key", "")
        or getattr(cfg, "aura_api_key", "")
    ).strip()

    endpoint_url = (
        getattr(cfg, "aura_embedding_endpoint_url", "")
        or getattr(cfg, "aura_generic_model_endpoint_url", "")
    ).strip()

    # Real OpenAI / Gemini provider
    if provider_type in ("generic", "openai", "gemini") or (provider_type == "auto" and api_key):
        if not api_key:
            if cfg.aura_env.lower() == "production":
                raise RuntimeError(
                    f"AURA_EMBEDDING_API_KEY is required for '{provider_type}' in production. Fail-closed."
                )
            logger.warning(
                f"No API key found for embedding provider '{provider_type}'. Falling back to mock provider for development."
            )
            return DeterministicMockEmbeddingProvider(dimension=dimension, model_name=model_name)

        logger.info(
            f"Initializing OpenAICompatibleEmbeddingProvider (model={model_name}, dimension={dimension}, endpoint={endpoint_url or 'default'})"
        )
        return OpenAICompatibleEmbeddingProvider(
            api_key=api_key,
            endpoint_url=endpoint_url,
            model_name=model_name,
            dimension=dimension,
            batch_size=batch_size,
        )

    # Fallback default
    logger.info("Defaulting to DeterministicMockEmbeddingProvider.")
    return DeterministicMockEmbeddingProvider(dimension=dimension, model_name=model_name)
