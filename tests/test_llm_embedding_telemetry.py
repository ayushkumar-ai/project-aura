"""Tests for LLM & Embedding Telemetry and Usage Extraction (M44)."""

import uuid
from unittest.mock import MagicMock
import pytest

from core.metrics import get_metrics_registry
from providers.generic_provider import GenericOpenAICompatibleProvider
from providers.embedding.openai_provider import OpenAICompatibleEmbeddingProvider


def test_llm_token_usage_extraction():
    metrics = get_metrics_registry()
    metrics.reset_all()

    # Mock client with OpenAI usage structure
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "Synthesized response"
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_resp.usage.prompt_tokens = 42
    mock_resp.usage.completion_tokens = 18
    mock_resp.usage.total_tokens = 60
    mock_client.chat.completions.create.return_value = mock_resp

    provider = GenericOpenAICompatibleProvider(
        base_url="http://127.0.0.1:8080/v1",
        model_name="test-llm-v1",
        client=mock_client,
    )

    req_id = uuid.uuid4()
    resp = provider.generate("Summarize system telemetry", req_id)

    assert resp.content == "Synthesized response"
    assert "usage" in resp.metadata
    assert resp.metadata["usage"]["prompt_tokens"] == 42
    assert resp.metadata["usage"]["completion_tokens"] == 18
    assert resp.metadata["usage"]["total_tokens"] == 60
    assert resp.metadata["duration_seconds"] >= 0.0

    # Verify metrics recorded
    req_count = metrics.get_counter("aura_llm_requests_total").get(
        {"provider": "generic", "model": "test-llm-v1", "status": "success"}
    )
    assert req_count == 1.0

    prompt_toks = metrics.get_counter("aura_llm_tokens_total").get(
        {"provider": "generic", "model": "test-llm-v1", "type": "prompt"}
    )
    assert prompt_toks == 42.0

    comp_toks = metrics.get_counter("aura_llm_tokens_total").get(
        {"provider": "generic", "model": "test-llm-v1", "type": "completion"}
    )
    assert comp_toks == 18.0


def test_embedding_batch_telemetry():
    metrics = get_metrics_registry()
    metrics.reset_all()

    provider = OpenAICompatibleEmbeddingProvider(
        api_key="test-key",
        endpoint_url="http://127.0.0.1:8080/v1",
        model_name="gemini-embedding-001",
        dimension=1536,
    )

    # Mock internal _call_embeddings_api to return valid 1536-dim vectors
    mock_vectors = [[0.1] * 1536, [0.2] * 1536, [0.3] * 1536]
    provider._call_embeddings_api = MagicMock(return_value=mock_vectors)

    texts = ["Document 1", "Document 2", "Document 3"]
    res = provider.embed_batch(texts)

    assert len(res) == 3
    assert len(res[0]) == 1536

    # Verify embedding metrics
    emb_reqs = metrics.get_counter("aura_embedding_requests_total").get(
        {"provider": "openai_compatible", "model": "gemini-embedding-001", "status": "success"}
    )
    assert emb_reqs == 1.0

    emb_vecs = metrics.get_counter("aura_embedding_vectors_total").get(
        {"provider": "openai_compatible", "model": "gemini-embedding-001"}
    )
    assert emb_vecs == 3.0
