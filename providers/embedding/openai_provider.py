"""M43 — OpenAI-Compatible Embedding Provider for Project AURA.

Supports Gemini (via OpenAI-compatible endpoint), OpenAI, and self-hosted vLLM/Ollama
with batching, exponential backoff retries, timeout management, and fixed dimension enforcement.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from interfaces.embedding import BaseEmbeddingProvider

logger = logging.getLogger("aura.providers.embedding.openai")

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OpenAI = None  # type: ignore
    OPENAI_AVAILABLE = False


class OpenAICompatibleEmbeddingProvider(BaseEmbeddingProvider):
    """Generates embeddings via OpenAI-compatible endpoints with batching and resilience."""

    def __init__(
        self,
        api_key: str,
        endpoint_url: str = "",
        model_name: str = "gemini-embedding-001",
        dimension: int = 1536,
        batch_size: int = 32,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        if not api_key:
            raise ValueError("API key must be provided for OpenAICompatibleEmbeddingProvider.")
        if not OPENAI_AVAILABLE:
            raise ImportError("openai package is required for OpenAICompatibleEmbeddingProvider.")

        self._api_key = api_key
        self._endpoint_url = endpoint_url.strip() if endpoint_url else None
        self._model_name = model_name
        self._dimension = dimension
        self._batch_size = max(1, batch_size)
        self._timeout = timeout
        self._max_retries = max(1, max_retries)

        self._client = OpenAI(
            api_key=self._api_key,
            base_url=self._endpoint_url,
            timeout=self._timeout,
        )

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def _call_embeddings_api(self, batch_texts: list[str]) -> list[list[float]]:
        """Call embedding API with retries and exponential backoff."""
        last_exception: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                # Try with dimensions argument first (supported by gemini-embedding-001 and text-embedding-3)
                try:
                    response = self._client.embeddings.create(
                        model=self._model_name,
                        input=batch_texts,
                        dimensions=self._dimension,
                    )
                except Exception as param_err:
                    # If dimensions parameter is not supported by legacy backend, retry without it
                    if "dimensions" in str(param_err).lower() or "unexpected keyword" in str(param_err).lower():
                        response = self._client.embeddings.create(
                            model=self._model_name,
                            input=batch_texts,
                        )
                    else:
                        raise param_err

                # Sort by index to preserve input ordering
                sorted_data = sorted(response.data, key=lambda d: d.index)
                vectors: list[list[float]] = []
                for item in sorted_data:
                    vec = item.embedding
                    if len(vec) != self._dimension:
                        if len(vec) > self._dimension:
                            vec = vec[: self._dimension]
                        else:
                            raise ValueError(
                                f"Model returned embedding of dimension {len(vec)}, "
                                f"expected {self._dimension}"
                            )
                    vectors.append(vec)

                return vectors

            except Exception as e:
                last_exception = e
                err_str = str(e).lower()
                is_rate_limit = "429" in err_str or "quota" in err_str or "rate limit" in err_str
                is_server_err = "500" in err_str or "503" in err_str or "504" in err_str or "timeout" in err_str

                if (is_rate_limit or is_server_err) and attempt < self._max_retries:
                    sleep_time = (2 ** (attempt - 1)) * 1.5
                    logger.warning(
                        f"Embedding API transient error on attempt {attempt}/{self._max_retries}: {e}. "
                        f"Retrying in {sleep_time:.1f}s..."
                    )
                    time.sleep(sleep_time)
                else:
                    logger.error(f"Embedding API failed on attempt {attempt}/{self._max_retries}: {e}")
                    break

        if last_exception:
            raise RuntimeError(f"Embedding generation failed after {self._max_retries} attempts: {last_exception}") from last_exception
        raise RuntimeError("Embedding generation failed with unknown error.")

    def embed_text(self, text: str) -> list[float]:
        """Generate embedding vector for a single text."""
        if not text or not text.strip():
            # Return zero-like unit vector for empty text
            vec = [0.0] * self._dimension
            vec[0] = 1.0
            return vec
        results = self.embed_batch([text])
        return results[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embedding vectors for a batch of texts."""
        if not texts:
            return []

        all_vectors: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            chunk = texts[i : i + self._batch_size]
            cleaned_chunk = [t if (t and t.strip()) else " " for t in chunk]
            sub_vectors = self._call_embeddings_api(cleaned_chunk)
            all_vectors.extend(sub_vectors)

        return all_vectors
