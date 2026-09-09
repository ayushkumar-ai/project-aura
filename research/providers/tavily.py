import json
import logging
import urllib.error
import urllib.request
from typing import Any

from research.interfaces import SearchProvider
from research.models import SearchItem, SearchResult

logger = logging.getLogger("aura.research.tavily")


class TavilySearchProvider(SearchProvider):
    """Adapter for Tavily search API providing structured research search results."""

    def __init__(
        self,
        api_key: str,
        endpoint: str = "https://api.tavily.com/search",
        timeout: float = 10.0,
    ):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("Tavily API key must be a non-empty string.")
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ValueError("endpoint must be a non-empty string.")

        self._api_key = api_key.strip()
        self.endpoint = endpoint.strip()
        self.timeout = float(timeout)

    @property
    def name(self) -> str:
        return "tavily"

    def search(
        self,
        query: str,
        max_results: int = 5,
        timeout: float | None = None,
    ) -> SearchResult:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Query must be a non-empty string.")

        effective_timeout = timeout if timeout is not None else self.timeout
        effective_limit = max(1, min(max_results, 20))

        payload = {
            "api_key": self._api_key,
            "query": query.strip(),
            "max_results": effective_limit,
            "include_answer": False,
            "search_depth": "basic",
        }
        body_bytes = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            self.endpoint,
            data=body_bytes,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "AURA-Research/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as response:
                status_code = getattr(response, "status", 200)
                if status_code != 200:
                    raise RuntimeError(f"Tavily API returned unexpected status {status_code}.")

                raw_data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                pass
            logger.warning("Tavily API error (%d): %s", e.code, err_body)
            if e.code == 401:
                raise RuntimeError("Tavily API authorization failed: Invalid API key.") from e
            elif e.code == 429:
                raise RuntimeError("Tavily API rate limit exceeded.") from e
            raise RuntimeError(f"Tavily search request failed with HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            logger.warning("Tavily network error: %s", e.reason)
            raise RuntimeError(f"Tavily network connection failed: {e.reason}") from e
        except Exception as e:
            logger.warning("Tavily search failed: %s", e)
            raise RuntimeError(f"Tavily search provider error: {str(e)}") from e

        raw_results = raw_data.get("results", [])
        if not isinstance(raw_results, list):
            raw_results = []

        items: list[SearchItem] = []
        for r in raw_results:
            if not isinstance(r, dict):
                continue
            title = str(r.get("title") or "Untitled").strip()
            url = str(r.get("url") or "").strip()
            snippet = str(r.get("content") or r.get("snippet") or "").strip()

            if not url or not (url.startswith("http://") or url.startswith("https://")):
                continue

            try:
                items.append(
                    SearchItem(
                        title=title,
                        url=url,
                        snippet=snippet,
                    )
                )
            except Exception:
                continue

        return SearchResult(
            query=query.strip(),
            items=tuple(items[:effective_limit]),
            total_results=len(items),
            metadata={"provider": "tavily"},
        )
