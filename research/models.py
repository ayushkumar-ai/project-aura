import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse


def _extract_domain(url: str) -> str:
    """Extract domain from URL if valid, else return empty string."""
    try:
        parsed = urlparse(url)
        return parsed.netloc or ""
    except Exception:
        return ""


def _sanitize_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Sanitize metadata to ensure no arbitrary callables or executable objects are stored."""
    clean: dict[str, Any] = {}
    for k, v in meta.items():
        if not isinstance(k, str) or not k.strip():
            continue
        if callable(v):
            raise ValueError(f"Metadata value for key '{k}' cannot be callable.")
        if isinstance(v, (str, int, float, bool)) or v is None:
            clean[k] = v
        elif isinstance(v, (list, tuple)):
            clean[k] = [str(x) for x in v if not callable(x)]
        elif isinstance(v, dict):
            clean[k] = {str(dk): str(dv) for dk, dv in v.items() if not callable(dv)}
        else:
            clean[k] = str(v)
    return clean


@dataclass(frozen=True)
class SearchItem:
    """Represents an individual search result item."""

    title: str
    url: str
    snippet: str
    source_domain: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("title must be a non-empty string.")
        object.__setattr__(self, "title", self.title.strip())

        if not isinstance(self.url, str) or not self.url.strip():
            raise ValueError("url must be a non-empty string.")
        url_clean = self.url.strip()
        if not (url_clean.startswith("http://") or url_clean.startswith("https://")):
            raise ValueError(f"Invalid URL scheme in '{url_clean}'. Must start with http:// or https://.")
        object.__setattr__(self, "url", url_clean)

        if not isinstance(self.snippet, str):
            raise TypeError("snippet must be a string.")
        object.__setattr__(self, "snippet", self.snippet.strip())

        domain = self.source_domain.strip() if self.source_domain else _extract_domain(url_clean)
        object.__setattr__(self, "source_domain", domain)

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))


@dataclass(frozen=True)
class SearchResult:
    """Represents the complete result set of a search query."""

    query: str
    items: tuple[SearchItem, ...] = field(default_factory=tuple)
    total_results: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.query, str) or not self.query.strip():
            raise ValueError("query must be a non-empty string.")
        object.__setattr__(self, "query", self.query.strip())

        if isinstance(self.items, (list, tuple)):
            for itm in self.items:
                if not isinstance(itm, SearchItem):
                    raise TypeError("All items in SearchResult must be SearchItem instances.")
            object.__setattr__(self, "items", tuple(self.items))
        else:
            raise TypeError("items must be a list or tuple of SearchItem instances.")

        if not isinstance(self.total_results, int) or self.total_results < 0:
            raise ValueError("total_results must be a non-negative integer.")

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))


@dataclass(frozen=True)
class WebDocument:
    """Represents the raw/extracted textual content of a fetched web page."""

    url: str
    title: str = ""
    content: str = ""
    status_code: int = 200
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.url, str) or not self.url.strip():
            raise ValueError("url must be a non-empty string.")
        url_clean = self.url.strip()
        if not (url_clean.startswith("http://") or url_clean.startswith("https://")):
            raise ValueError(f"Invalid URL scheme in '{url_clean}'. Must start with http:// or https://.")
        object.__setattr__(self, "url", url_clean)

        if not isinstance(self.title, str):
            raise TypeError("title must be a string.")
        object.__setattr__(self, "title", self.title.strip())

        if not isinstance(self.content, str):
            raise TypeError("content must be a string.")

        if not isinstance(self.status_code, int):
            raise TypeError("status_code must be an integer.")

        if self.error is not None and not isinstance(self.error, str):
            raise TypeError("error must be a string or None.")

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))

    @property
    def is_success(self) -> bool:
        """Check if document was retrieved successfully."""
        return 200 <= self.status_code < 300 and self.error is None


@dataclass(frozen=True)
class ResearchSource:
    """Represents a verified source utilized for research and attribution."""

    url: str
    title: str
    snippet: str = ""
    content: str = ""
    status: str = "success"  # "success" | "failed" | "skipped"
    error: str | None = None
    source_domain: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.url, str) or not self.url.strip():
            raise ValueError("url must be a non-empty string.")
        url_clean = self.url.strip()
        if not (url_clean.startswith("http://") or url_clean.startswith("https://")):
            raise ValueError(f"Invalid URL scheme in '{url_clean}'. Must start with http:// or https://.")
        object.__setattr__(self, "url", url_clean)

        if not isinstance(self.title, str):
            raise TypeError("title must be a string.")
        object.__setattr__(self, "title", self.title.strip())

        if not isinstance(self.snippet, str):
            raise TypeError("snippet must be a string.")

        if not isinstance(self.content, str):
            raise TypeError("content must be a string.")

        if not isinstance(self.status, str) or self.status not in ("success", "failed", "skipped"):
            raise ValueError("status must be one of: 'success', 'failed', 'skipped'.")

        if self.error is not None and not isinstance(self.error, str):
            raise TypeError("error must be a string or None.")

        domain = self.source_domain.strip() if self.source_domain else _extract_domain(url_clean)
        object.__setattr__(self, "source_domain", domain)

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))


@dataclass(frozen=True)
class ResearchReport:
    """Aggregated outcome of a research operation with full source attribution."""

    query: str
    sources: tuple[ResearchSource, ...] = field(default_factory=tuple)
    failed_sources: tuple[ResearchSource, ...] = field(default_factory=tuple)
    summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.query, str) or not self.query.strip():
            raise ValueError("query must be a non-empty string.")
        object.__setattr__(self, "query", self.query.strip())

        if isinstance(self.sources, (list, tuple)):
            for s in self.sources:
                if not isinstance(s, ResearchSource):
                    raise TypeError("All items in sources must be ResearchSource instances.")
            object.__setattr__(self, "sources", tuple(self.sources))
        else:
            raise TypeError("sources must be a list or tuple of ResearchSource instances.")

        if isinstance(self.failed_sources, (list, tuple)):
            for s in self.failed_sources:
                if not isinstance(s, ResearchSource):
                    raise TypeError("All items in failed_sources must be ResearchSource instances.")
            object.__setattr__(self, "failed_sources", tuple(self.failed_sources))
        else:
            raise TypeError("failed_sources must be a list or tuple of ResearchSource instances.")

        if self.summary is not None and not isinstance(self.summary, str):
            raise TypeError("summary must be a string or None.")

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))

    @property
    def has_sources(self) -> bool:
        """Check if any successful sources are available."""
        return len(self.sources) > 0

    def format_citations(self) -> str:
        """Format source citations for prompt injection or report rendering."""
        lines = []
        for idx, src in enumerate(self.sources, 1):
            lines.append(f"[{idx}] {src.title} - {src.url}")
        return "\n".join(lines)
