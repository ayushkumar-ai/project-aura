import os
from research.interfaces import BrowserProvider, FetchProvider, SearchProvider
from research.providers.browser import BrowserFetchProvider, FakeBrowserProvider
from research.providers.fake import FakeFetchProvider, FakeSearchProvider, FakeWebProvider
from research.providers.generic_http import GenericHttpSearchProvider
from research.providers.http_fetch import HttpFetchProvider
from research.providers.tavily import TavilySearchProvider


def create_search_provider(
    provider_type: str = "fake",
    api_key: str | None = None,
    endpoint: str | None = None,
    **kwargs,
) -> SearchProvider:
    """Factory creating configured SearchProvider instances."""
    ptype = (provider_type or "fake").strip().lower()

    if ptype in ("fake", "mock", "memory"):
        return FakeSearchProvider(**kwargs)
    elif ptype in ("tavily", "tavily_search"):
        effective_key = api_key or os.getenv("AURA_SEARCH_API_KEY", "")
        return TavilySearchProvider(
            api_key=effective_key,
            endpoint=endpoint or "https://api.tavily.com/search",
            **kwargs,
        )
    elif ptype in ("generic", "generic_http", "custom_http"):
        endpoint_url = endpoint or kwargs.pop("endpoint_url", None)
        if not endpoint_url:
            raise ValueError("Endpoint URL is required for generic_http search provider.")
        return GenericHttpSearchProvider(
            endpoint_url=endpoint_url,
            api_key=api_key,
            **kwargs,
        )
    else:
        raise ValueError(f"Unsupported search provider '{provider_type}'. Supported: 'fake', 'tavily', 'generic_http'.")


def create_fetch_provider(
    provider_type: str = "http",
    timeout: float = 10.0,
    max_document_chars: int = 10000,
    **kwargs,
) -> FetchProvider:
    """Factory creating configured FetchProvider instances."""
    ptype = (provider_type or "http").strip().lower()

    if ptype in ("fake", "mock", "memory"):
        return FakeFetchProvider(**kwargs)
    elif ptype in ("http", "http_fetch", "live"):
        return HttpFetchProvider(
            timeout=timeout,
            max_document_chars=max_document_chars,
            **kwargs,
        )
    else:
        raise ValueError(f"Unsupported fetch provider '{provider_type}'. Supported: 'http', 'fake'.")


def create_browser_provider(
    provider_type: str = "fake",
    timeout: float = 15.0,
    max_document_chars: int = 10000,
    render_wait: float = 1.0,
    **kwargs,
) -> BrowserProvider:
    """Factory creating configured BrowserProvider instances."""
    ptype = (provider_type or "fake").strip().lower()

    if ptype in ("fake", "mock", "memory"):
        return FakeBrowserProvider(**kwargs)
    elif ptype in ("browser", "dynamic", "http_browser", "live"):
        return BrowserFetchProvider(
            default_timeout=timeout,
            max_document_chars=max_document_chars,
            render_wait=render_wait,
            **kwargs,
        )
    else:
        raise ValueError(f"Unsupported browser provider '{provider_type}'. Supported: 'fake', 'browser'.")
