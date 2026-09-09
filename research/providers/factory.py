from research.interfaces import FetchProvider, SearchProvider
from research.providers.fake import FakeFetchProvider, FakeSearchProvider, FakeWebProvider
from research.providers.generic_http import GenericHttpSearchProvider
from research.providers.http_fetch import HttpFetchProvider
from research.providers.tavily import TavilySearchProvider


def create_search_provider(
    provider_name: str | None = None,
    api_key: str | None = None,
    endpoint: str | None = None,
    timeout: float = 10.0,
) -> SearchProvider:
    """Factory creating configured SearchProvider instances."""
    p_name = (provider_name or "fake").strip().lower()

    if p_name in ("fake", "mock", "dummy", "test"):
        return FakeSearchProvider()
    elif p_name == "tavily":
        if not api_key:
            raise ValueError("Tavily search provider requires an API key.")
        return TavilySearchProvider(
            api_key=api_key,
            endpoint=endpoint or "https://api.tavily.com/search",
            timeout=timeout,
        )
    elif p_name in ("generic", "http", "rest", "searxng"):
        if not endpoint:
            raise ValueError("Generic HTTP search provider requires an endpoint URL.")
        return GenericHttpSearchProvider(
            endpoint_url=endpoint,
            api_key=api_key,
            timeout=timeout,
        )
    else:
        raise ValueError(f"Unsupported search provider: '{provider_name}'.")


def create_fetch_provider(
    provider_name: str | None = None,
    timeout: float = 10.0,
    max_response_bytes: int = 1_000_000,
    max_document_chars: int = 10000,
    allow_local: bool = False,
) -> FetchProvider:
    """Factory creating configured FetchProvider instances."""
    p_name = (provider_name or "http").strip().lower()

    if p_name in ("fake", "mock", "dummy", "test"):
        return FakeFetchProvider()
    elif p_name in ("http", "live", "standard"):
        return HttpFetchProvider(
            timeout=timeout,
            max_response_bytes=max_response_bytes,
            max_document_chars=max_document_chars,
            allow_local=allow_local,
        )
    else:
        raise ValueError(f"Unsupported fetch provider: '{provider_name}'.")
