import time
from typing import Any
from research.interfaces import FetchProvider, SearchProvider, WebProvider
from research.models import SearchItem, SearchResult, WebDocument


class FakeSearchProvider(SearchProvider):
    """Deterministic in-memory search provider for testing and offline execution."""

    def __init__(
        self,
        results_by_query: dict[str, list[SearchItem]] | None = None,
        default_items: list[SearchItem] | None = None,
        simulate_error: Exception | None = None,
    ):
        self.results_by_query = results_by_query or {}
        self.default_items = default_items or []
        self.simulate_error = simulate_error
        self.recorded_queries: list[str] = []
        self.calls: int = 0

    def add_result(self, query: str, items: list[SearchItem]) -> None:
        """Register canned search items for a specific query."""
        self.results_by_query[query.strip().lower()] = items

    def search(
        self,
        query: str,
        max_results: int = 5,
        timeout: float | None = None,
    ) -> SearchResult:
        self.calls += 1
        self.recorded_queries.append(query)

        if self.simulate_error:
            raise self.simulate_error

        q_norm = query.strip().lower()
        items = self.results_by_query.get(q_norm, self.default_items)
        limited_items = items[:max_results]

        return SearchResult(
            query=query,
            items=tuple(limited_items),
            total_results=len(items),
            metadata={"provider": "fake_search"},
        )


class FakeFetchProvider(FetchProvider):
    """Deterministic in-memory document fetcher for testing and offline execution."""

    def __init__(
        self,
        documents_by_url: dict[str, WebDocument] | None = None,
        simulate_timeout_urls: set[str] | None = None,
        simulate_error_urls: dict[str, Exception] | None = None,
    ):
        self.documents_by_url = documents_by_url or {}
        self.simulate_timeout_urls = simulate_timeout_urls or set()
        self.simulate_error_urls = simulate_error_urls or {}
        self.recorded_fetches: list[str] = []
        self.calls: int = 0

    def add_document(self, url: str, doc: WebDocument) -> None:
        """Register a canned document for a specific URL."""
        self.documents_by_url[url.strip()] = doc

    def fetch(
        self,
        url: str,
        timeout: float | None = None,
    ) -> WebDocument:
        self.calls += 1
        url_clean = url.strip()
        self.recorded_fetches.append(url_clean)

        if url_clean in self.simulate_timeout_urls:
            raise TimeoutError(f"Fetch request for '{url_clean}' timed out.")

        if url_clean in self.simulate_error_urls:
            raise self.simulate_error_urls[url_clean]

        if url_clean in self.documents_by_url:
            return self.documents_by_url[url_clean]

        # Default fallback document if not explicitly registered
        return WebDocument(
            url=url_clean,
            title=f"Page from {url_clean}",
            content=f"Extracted content from {url_clean}",
            status_code=200,
            metadata={"provider": "fake_fetch"},
        )


class FakeWebProvider(FakeSearchProvider, FakeFetchProvider, WebProvider):
    """Combined search and fetch provider for unified testing."""

    def __init__(
        self,
        results_by_query: dict[str, list[SearchItem]] | None = None,
        documents_by_url: dict[str, WebDocument] | None = None,
        default_items: list[SearchItem] | None = None,
        simulate_search_error: Exception | None = None,
        simulate_timeout_urls: set[str] | None = None,
        simulate_error_urls: dict[str, Exception] | None = None,
    ):
        FakeSearchProvider.__init__(
            self,
            results_by_query=results_by_query,
            default_items=default_items,
            simulate_error=simulate_search_error,
        )
        FakeFetchProvider.__init__(
            self,
            documents_by_url=documents_by_url,
            simulate_timeout_urls=simulate_timeout_urls,
            simulate_error_urls=simulate_error_urls,
        )
