import json
import urllib.error
from unittest.mock import MagicMock, patch
import pytest

from research.providers.factory import create_fetch_provider, create_search_provider
from research.providers.fake import FakeSearchProvider
from research.providers.generic_http import GenericHttpSearchProvider
from research.providers.http_fetch import HttpFetchProvider
from research.providers.tavily import TavilySearchProvider


def test_tavily_search_provider_successful_query():
    provider = TavilySearchProvider(api_key="tvly-test-key")

    canned_tavily_response = {
        "results": [
            {"title": "Quantum News", "url": "https://quantum.com/news", "content": "Qubit breakthrough"},
            {"title": "Science Daily", "url": "https://sciencedaily.com/article", "snippet": "New physics findings"},
        ]
    }
    body_bytes = json.dumps(canned_tavily_response).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = body_bytes
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        res = provider.search("quantum physics", max_results=5)

    assert len(res.items) == 2
    assert res.items[0].title == "Quantum News"
    assert res.items[0].url == "https://quantum.com/news"
    assert res.items[0].snippet == "Qubit breakthrough"
    assert res.metadata.get("provider") == "tavily"


def test_tavily_search_provider_validation_and_auth_failure():
    with pytest.raises(ValueError, match="Tavily API key must be a non-empty string"):
        TavilySearchProvider(api_key="   ")

    provider = TavilySearchProvider(api_key="invalid-key")
    http_err = urllib.error.HTTPError(
        url="https://api.tavily.com/search",
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=None,
    )

    with patch("urllib.request.urlopen", side_effect=http_err):
        with pytest.raises(RuntimeError, match="Tavily API authorization failed: Invalid API key"):
            provider.search("test")


def test_generic_http_search_provider():
    provider = GenericHttpSearchProvider(
        endpoint_url="https://searx.example.org/search",
        api_key="test-bearer-token",
    )

    canned_searx_response = {
        "results": [
            {"title": "OpenAI News", "url": "https://openai.com/news", "content": "GPT updates"}
        ]
    }
    body_bytes = json.dumps(canned_searx_response).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = body_bytes
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = provider.search("ai news")

    assert len(res.items) == 1
    assert res.items[0].title == "OpenAI News"
    assert res.items[0].url == "https://openai.com/news"


def test_provider_factories():
    fake_search = create_search_provider("fake")
    assert isinstance(fake_search, FakeSearchProvider)

    tavily = create_search_provider("tavily", api_key="tvly-secret")
    assert isinstance(tavily, TavilySearchProvider)

    generic = create_search_provider("generic", endpoint="https://search.example.com")
    assert isinstance(generic, GenericHttpSearchProvider)

    with pytest.raises(ValueError, match="Unsupported search provider"):
        create_search_provider("unknown_engine")

    http_fetch = create_fetch_provider("http")
    assert isinstance(http_fetch, HttpFetchProvider)
