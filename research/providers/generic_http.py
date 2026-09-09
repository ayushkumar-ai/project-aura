import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from research.interfaces import SearchProvider
from research.models import SearchItem, SearchResult

logger = logging.getLogger("aura.research.generic_http")


class GenericHttpSearchProvider(SearchProvider):
    """Adapter for REST/JSON search endpoints (e.g. SearxNG, custom search gateways)."""

    def __init__(
        self,
        endpoint_url: str,
        api_key: str | None = None,
        query_param: str = "q",
        results_field: str = "results",
        title_field: str = "title",
        url_field: str = "url",
        snippet_field: str = "content",
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
    ):
        if not isinstance(endpoint_url, str) or not endpoint_url.strip():
            raise ValueError("endpoint_url must be a non-empty string.")

        self.endpoint_url = endpoint_url.strip()
        self._api_key = api_key.strip() if api_key else None
        self.query_param = query_param
        self.results_field = results_field
        self.title_field = title_field
        self.url_field = url_field
        self.snippet_field = snippet_field
        self.headers = dict(headers) if headers else {}
        self.timeout = float(timeout)

    @property
    def name(self) -> str:
        return "generic_http"

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

        params: dict[str, Any] = {
            self.query_param: query.strip(),
            "format": "json",
        }
        if self._api_key:
            params["api_key"] = self._api_key

        query_str = urllib.parse.urlencode(params)
        sep = "&" if "?" in self.endpoint_url else "?"
        full_url = f"{self.endpoint_url}{sep}{query_str}"

        req_headers = {
            "Accept": "application/json",
            "User-Agent": "AURA-Research/1.0",
            **self.headers,
        }
        if self._api_key and "Authorization" not in req_headers:
            req_headers["Authorization"] = f"Bearer {self._api_key}"

        req = urllib.request.Request(full_url, headers=req_headers)

        try:
            with urllib.request.urlopen(req, timeout=effective_timeout) as response:
                status_code = getattr(response, "status", 200)
                if status_code != 200:
                    raise RuntimeError(f"Search endpoint returned status {status_code}.")

                raw_data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            logger.warning("Generic search HTTP error: %d %s", e.code, e.reason)
            raise RuntimeError(f"Search request failed with HTTP {e.code}: {e.reason}") from e
        except urllib.error.URLError as e:
            logger.warning("Generic search network error: %s", e.reason)
            raise RuntimeError(f"Search network connection failed: {e.reason}") from e
        except Exception as e:
            logger.warning("Generic search failed: %s", e)
            raise RuntimeError(f"Search provider error: {str(e)}") from e

        raw_items = raw_data.get(self.results_field, []) if isinstance(raw_data, dict) else []
        if not isinstance(raw_items, list):
            raw_items = []

        items: list[SearchItem] = []
        for r in raw_items:
            if not isinstance(r, dict):
                continue
            title = str(r.get(self.title_field) or "Untitled").strip()
            url = str(r.get(self.url_field) or "").strip()
            snippet = str(r.get(self.snippet_field) or r.get("snippet") or "").strip()

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
            metadata={"provider": "generic_http"},
        )
