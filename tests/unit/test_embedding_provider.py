"""Unit tests for M43 Embedding Providers and Factory."""

import math
from unittest.mock import MagicMock, patch
import pytest

from app.config import Settings
from interfaces.embedding import BaseEmbeddingProvider, EmbeddingResult
from providers.embedding.factory import create_embedding_provider
from providers.embedding.mock_provider import DeterministicMockEmbeddingProvider
from providers.embedding.openai_provider import OpenAICompatibleEmbeddingProvider


def _cosine(v1: list[float], v2: list[float]) -> float:
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a * a for a in v1))
    n2 = math.sqrt(sum(b * b for b in v2))
    return dot / (n1 * n2)


def test_mock_embedding_provider_dimension_and_norm():
    provider = DeterministicMockEmbeddingProvider(dimension=1536, model_name="gemini-embedding-001")
    assert provider.dimension == 1536
    assert provider.model_name == "gemini-embedding-001"

    vec = provider.embed_text("Project AURA autonomous agent")
    assert len(vec) == 1536

    # Verify L2 normalization
    l2_norm = math.sqrt(sum(x * x for x in vec))
    assert math.isclose(l2_norm, 1.0, rel_tol=1e-5)


def test_mock_embedding_provider_determinism():
    provider = DeterministicMockEmbeddingProvider(dimension=1536)
    text = "Deterministic vector generation for Project AURA."

    vec1 = provider.embed_text(text)
    vec2 = provider.embed_text(text)
    assert vec1 == vec2


def test_mock_embedding_provider_semantic_similarity():
    provider = DeterministicMockEmbeddingProvider(dimension=1536)

    doc_python_1 = "Python programming language with async asyncio coroutines"
    doc_python_2 = "Asynchronous coroutines in Python language"
    doc_cooking = "French onion soup recipe with caramelized onions and beef broth"

    v_p1 = provider.embed_text(doc_python_1)
    v_p2 = provider.embed_text(doc_python_2)
    v_cook = provider.embed_text(doc_cooking)

    sim_related = _cosine(v_p1, v_p2)
    sim_unrelated = _cosine(v_p1, v_cook)

    assert sim_related > sim_unrelated
    assert sim_related > 0.3


def test_mock_embedding_provider_batch():
    provider = DeterministicMockEmbeddingProvider(dimension=1536)
    texts = ["First text snippet", "Second text snippet", "Third text snippet"]

    batch_vecs = provider.embed_batch(texts)
    assert len(batch_vecs) == 3
    for i, t in enumerate(texts):
        assert batch_vecs[i] == provider.embed_text(t)


def test_mock_embedding_provider_empty_text():
    provider = DeterministicMockEmbeddingProvider(dimension=1536)
    vec = provider.embed_text("")
    assert len(vec) == 1536
    assert math.isclose(math.sqrt(sum(x * x for x in vec)), 1.0, rel_tol=1e-5)


def test_mock_embedding_result_metadata():
    provider = DeterministicMockEmbeddingProvider(dimension=1536, model_name="gemini-embedding-001")
    result = provider.get_embedding_result(["Short text query", "Another query"])

    assert isinstance(result, EmbeddingResult)
    assert len(result.vectors) == 2
    assert result.dimension == 1536
    assert result.model_name == "gemini-embedding-001"
    assert result.version == "v1"
    assert result.token_count > 0
    assert result.duration_seconds >= 0.0


def test_openai_embedding_provider_init_validation():
    with pytest.raises(ValueError, match="API key must be provided"):
        OpenAICompatibleEmbeddingProvider(api_key="")


def test_openai_embedding_provider_batching_and_dimension_mock():
    mock_item1 = MagicMock()
    mock_item1.index = 0
    mock_item1.embedding = [0.1] * 1536

    mock_item2 = MagicMock()
    mock_item2.index = 1
    mock_item2.embedding = [0.2] * 1536

    mock_response = MagicMock()
    mock_response.data = [mock_item2, mock_item1]  # out-of-order data to test re-sorting

    with patch("providers.embedding.openai_provider.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.embeddings.create.return_value = mock_response

        provider = OpenAICompatibleEmbeddingProvider(
            api_key="test_key",
            model_name="gemini-embedding-001",
            dimension=1536,
            batch_size=2,
        )

        vectors = provider.embed_batch(["text1", "text2"])
        assert len(vectors) == 2
        assert vectors[0] == [0.1] * 1536
        assert vectors[1] == [0.2] * 1536
        assert mock_client.embeddings.create.call_count == 1


def test_embedding_factory():
    # Test explicit mock provider
    cfg_mock = Settings(aura_embedding_provider="mock", aura_embedding_dimension=1536)
    prov_mock = create_embedding_provider(cfg_mock)
    assert isinstance(prov_mock, DeterministicMockEmbeddingProvider)
    assert prov_mock.dimension == 1536

    # Test explicit generic provider with api key
    cfg_generic = Settings(
        aura_embedding_provider="generic",
        aura_generic_model_api_key="fake_key_123",
        aura_embedding_dimension=1536,
    )
    with patch("providers.embedding.openai_provider.OpenAI"):
        prov_generic = create_embedding_provider(cfg_generic)
        assert isinstance(prov_generic, OpenAICompatibleEmbeddingProvider)
        assert prov_generic.dimension == 1536
