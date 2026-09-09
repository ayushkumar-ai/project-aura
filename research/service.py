import logging
from collections.abc import Callable
from typing import Any

from research.interfaces import FetchProvider, SearchProvider
from research.models import ResearchReport, ResearchSource, SearchItem, SearchResult, WebDocument

logger = logging.getLogger("aura.research.service")


def default_text_extractor(raw_text: str, max_chars: int) -> str:
    """Sanitize and truncate extracted text to maximum length."""
    if not raw_text:
        return ""
    # Strip dangerous null bytes and excess whitespace
    clean = raw_text.replace("\x00", "").strip()
    if len(clean) > max_chars:
        return clean[:max_chars] + "... [truncated]"
    return clean


class ResearchService:
    """Coordinates search, fetch, content extraction, and structured research generation with bounded execution."""

    def __init__(
        self,
        search_provider: SearchProvider,
        fetch_provider: FetchProvider | None = None,
        max_search_results: int = 5,
        max_fetch_sources: int = 3,
        max_document_chars: int = 10000,
        default_timeout: float = 10.0,
        extract_text_fn: Callable[[str, int], str] | None = None,
    ):
        if not isinstance(search_provider, SearchProvider):
            raise TypeError("search_provider must be an instance of SearchProvider.")
        if fetch_provider is not None and not isinstance(fetch_provider, FetchProvider):
            raise TypeError("fetch_provider must be an instance of FetchProvider or None.")

        if not isinstance(max_search_results, int) or max_search_results <= 0:
            raise ValueError("max_search_results must be a positive integer.")
        if not isinstance(max_fetch_sources, int) or max_fetch_sources <= 0:
            raise ValueError("max_fetch_sources must be a positive integer.")
        if not isinstance(max_document_chars, int) or max_document_chars <= 0:
            raise ValueError("max_document_chars must be a positive integer.")
        if not isinstance(default_timeout, (int, float)) or default_timeout <= 0:
            raise ValueError("default_timeout must be a positive number.")

        self.search_provider = search_provider
        self.fetch_provider = fetch_provider
        self.max_search_results = max_search_results
        self.max_fetch_sources = max_fetch_sources
        self.max_document_chars = max_document_chars
        self.default_timeout = float(default_timeout)
        self.extract_text_fn = extract_text_fn or default_text_extractor

    def search(
        self,
        query: str,
        max_results: int | None = None,
        timeout: float | None = None,
    ) -> SearchResult:
        """Search the web for query, enforcing validation and bounded result limits."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Query must be a non-empty string.")

        q_clean = query.strip()
        effective_limit = min(max_results or self.max_search_results, self.max_search_results)
        if effective_limit <= 0:
            effective_limit = self.max_search_results

        effective_timeout = timeout if timeout is not None else self.default_timeout

        try:
            raw_result = self.search_provider.search(
                query=q_clean,
                max_results=effective_limit,
                timeout=effective_timeout,
            )
        except Exception as e:
            logger.warning("Search provider failed for query '%s': %s", q_clean, e)
            raise RuntimeError(f"Search provider error: {str(e)}") from e

        # Deduplicate results by URL while preserving order
        seen_urls: set[str] = set()
        deduped_items: list[SearchItem] = []
        for itm in raw_result.items:
            if itm.url not in seen_urls:
                seen_urls.add(itm.url)
                deduped_items.append(itm)

        return SearchResult(
            query=q_clean,
            items=tuple(deduped_items[:effective_limit]),
            total_results=raw_result.total_results or len(deduped_items),
            metadata=dict(raw_result.metadata),
        )

    def fetch(
        self,
        url: str,
        timeout: float | None = None,
    ) -> WebDocument:
        """Fetch a web document with bounded size and sanitized content extraction."""
        if not isinstance(url, str) or not url.strip():
            raise ValueError("URL must be a non-empty string.")

        url_clean = url.strip()
        if not (url_clean.startswith("http://") or url_clean.startswith("https://")):
            raise ValueError(f"Invalid URL scheme in '{url_clean}'. Must start with http:// or https://.")

        if self.fetch_provider is None:
            raise RuntimeError("Fetch provider is not configured.")

        effective_timeout = timeout if timeout is not None else self.default_timeout

        raw_doc = self.fetch_provider.fetch(
            url=url_clean,
            timeout=effective_timeout,
        )

        extracted_content = self.extract_text_fn(raw_doc.content, self.max_document_chars)

        return WebDocument(
            url=url_clean,
            title=raw_doc.title,
            content=extracted_content,
            status_code=raw_doc.status_code,
            error=raw_doc.error,
            metadata=dict(raw_doc.metadata),
        )

    def research(
        self,
        query: str,
        max_sources: int | None = None,
        fetch_content: bool = True,
        timeout: float | None = None,
    ) -> ResearchReport:
        """Perform end-to-end bounded web research with fault isolation."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Query must be a non-empty string.")

        effective_timeout = timeout if timeout is not None else self.default_timeout
        effective_max_sources = min(max_sources or self.max_fetch_sources, self.max_fetch_sources)
        if effective_max_sources <= 0:
            effective_max_sources = self.max_fetch_sources

        # 1. Execute bounded search
        search_res = self.search(
            query=query,
            max_results=self.max_search_results,
            timeout=effective_timeout,
        )

        if not search_res.items:
            return ResearchReport(
                query=query.strip(),
                sources=(),
                failed_sources=(),
                summary="No search results found.",
                metadata={"provider": self.search_provider.name},
            )

        successful_sources: list[ResearchSource] = []
        failed_sources: list[ResearchSource] = []
        seen_urls: set[str] = set()

        # 2. Fetch and extract content for each selected search item
        for item in search_res.items[:effective_max_sources]:
            if item.url in seen_urls:
                continue
            seen_urls.add(item.url)

            if fetch_content and self.fetch_provider is not None:
                try:
                    doc = self.fetch(item.url, timeout=effective_timeout)
                    if doc.is_success:
                        src = ResearchSource(
                            url=item.url,
                            title=doc.title or item.title,
                            snippet=item.snippet,
                            content=doc.content,
                            status="success",
                            source_domain=item.source_domain,
                            metadata=dict(doc.metadata),
                        )
                        successful_sources.append(src)
                    else:
                        err_msg = doc.error or f"HTTP {doc.status_code}"
                        src = ResearchSource(
                            url=item.url,
                            title=item.title,
                            snippet=item.snippet,
                            content="",
                            status="failed",
                            error=err_msg,
                            source_domain=item.source_domain,
                        )
                        failed_sources.append(src)
                except Exception as fetch_err:
                    logger.warning("Fetch failed for source '%s': %s", item.url, fetch_err)
                    src = ResearchSource(
                        url=item.url,
                        title=item.title,
                        snippet=item.snippet,
                        content="",
                        status="failed",
                        error=str(fetch_err),
                        source_domain=item.source_domain,
                    )
                    failed_sources.append(src)
            else:
                # Content fetch not requested or provider unavailable: fallback to snippet
                src = ResearchSource(
                    url=item.url,
                    title=item.title,
                    snippet=item.snippet,
                    content=item.snippet,
                    status="success",
                    source_domain=item.source_domain,
                )
                successful_sources.append(src)

        return ResearchReport(
            query=query.strip(),
            sources=tuple(successful_sources),
            failed_sources=tuple(failed_sources),
            metadata={
                "search_provider": self.search_provider.name,
                "fetch_provider": self.fetch_provider.name if self.fetch_provider else None,
                "total_queried": len(search_res.items),
                "successful_count": len(successful_sources),
                "failed_count": len(failed_sources),
            },
        )
