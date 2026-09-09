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
    raw_html: str = ""
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

        if not isinstance(self.raw_html, str):
            raise TypeError("raw_html must be a string.")

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))

    @property
    def is_success(self) -> bool:
        """Check if document was retrieved successfully."""
        return 200 <= self.status_code < 300 and self.error is None


@dataclass(frozen=True)
class EvidenceItem:
    """Represents an extracted, bounded piece of evidence from a specific research source."""

    source_url: str
    source_title: str
    source_domain: str
    content: str
    relevance_score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.source_url, str) or not self.source_url.strip():
            raise ValueError("source_url must be a non-empty string.")
        url_clean = self.source_url.strip()
        if not (url_clean.startswith("http://") or url_clean.startswith("https://")):
            raise ValueError(f"Invalid URL scheme in '{url_clean}'. Must start with http:// or https://.")
        object.__setattr__(self, "source_url", url_clean)

        if not isinstance(self.source_title, str):
            raise TypeError("source_title must be a string.")
        object.__setattr__(self, "source_title", self.source_title.strip())

        if not isinstance(self.source_domain, str):
            raise TypeError("source_domain must be a string.")
        domain = self.source_domain.strip() if self.source_domain else _extract_domain(url_clean)
        object.__setattr__(self, "source_domain", domain)

        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("content must be a non-empty string.")
        object.__setattr__(self, "content", self.content.strip())

        if not isinstance(self.relevance_score, (int, float)):
            raise TypeError("relevance_score must be a numeric value.")
        object.__setattr__(self, "relevance_score", float(self.relevance_score))

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))


@dataclass(frozen=True)
class EvidenceConflict:
    """Represents a potential factual or metric contradiction identified across research sources."""

    claim: str
    source_a_url: str
    source_a_evidence: str
    source_b_url: str
    source_b_evidence: str
    conflict_type: str = "divergent_claim"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.claim, str) or not self.claim.strip():
            raise ValueError("claim must be a non-empty string.")
        object.__setattr__(self, "claim", self.claim.strip())

        if not isinstance(self.source_a_url, str) or not self.source_a_url.strip():
            raise ValueError("source_a_url must be a non-empty string.")
        object.__setattr__(self, "source_a_url", self.source_a_url.strip())

        if not isinstance(self.source_a_evidence, str) or not self.source_a_evidence.strip():
            raise ValueError("source_a_evidence must be a non-empty string.")
        object.__setattr__(self, "source_a_evidence", self.source_a_evidence.strip())

        if not isinstance(self.source_b_url, str) or not self.source_b_url.strip():
            raise ValueError("source_b_url must be a non-empty string.")
        object.__setattr__(self, "source_b_url", self.source_b_url.strip())

        if not isinstance(self.source_b_evidence, str) or not self.source_b_evidence.strip():
            raise ValueError("source_b_evidence must be a non-empty string.")
        object.__setattr__(self, "source_b_evidence", self.source_b_evidence.strip())

        if not isinstance(self.conflict_type, str) or not self.conflict_type.strip():
            raise ValueError("conflict_type must be a non-empty string.")
        object.__setattr__(self, "conflict_type", self.conflict_type.strip())

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))


@dataclass(frozen=True)
class DiscoveredLink:
    """Represents a hyperlink discovered during document extraction or web traversal."""

    source_url: str
    target_url: str
    anchor_text: str = ""
    source_title: str = ""
    source_domain: str = ""
    hop: int = 0
    relevance_score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.source_url, str):
            raise TypeError("source_url must be a string.")
        object.__setattr__(self, "source_url", self.source_url.strip())

        if not isinstance(self.target_url, str) or not self.target_url.strip():
            raise ValueError("target_url must be a non-empty string.")
        t_clean = self.target_url.strip()
        if not (t_clean.startswith("http://") or t_clean.startswith("https://")):
            raise ValueError(f"Invalid URL scheme in '{t_clean}'. Must start with http:// or https://.")
        object.__setattr__(self, "target_url", t_clean)

        if not isinstance(self.anchor_text, str):
            raise TypeError("anchor_text must be a string.")
        object.__setattr__(self, "anchor_text", self.anchor_text.strip())

        if not isinstance(self.source_title, str):
            raise TypeError("source_title must be a string.")
        object.__setattr__(self, "source_title", self.source_title.strip())

        if not isinstance(self.source_domain, str):
            raise TypeError("source_domain must be a string.")
        domain = self.source_domain.strip() if self.source_domain else _extract_domain(t_clean)
        object.__setattr__(self, "source_domain", domain)

        if not isinstance(self.hop, int) or self.hop < 0:
            raise ValueError("hop must be a non-negative integer.")

        if not isinstance(self.relevance_score, (int, float)):
            raise TypeError("relevance_score must be a numeric value.")
        object.__setattr__(self, "relevance_score", float(self.relevance_score))

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))


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
    evidence: tuple[EvidenceItem, ...] = field(default_factory=tuple)
    rank_score: float = 0.0
    hop: int = 0
    parent_url: str | None = None
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

        if isinstance(self.evidence, (list, tuple)):
            for ev in self.evidence:
                if not isinstance(ev, EvidenceItem):
                    raise TypeError("All items in evidence must be EvidenceItem instances.")
            object.__setattr__(self, "evidence", tuple(self.evidence))
        else:
            raise TypeError("evidence must be a list or tuple of EvidenceItem instances.")

        if not isinstance(self.rank_score, (int, float)):
            raise TypeError("rank_score must be a numeric value.")
        object.__setattr__(self, "rank_score", float(self.rank_score))

        if not isinstance(self.hop, int) or self.hop < 0:
            raise ValueError("hop must be a non-negative integer.")

        if self.parent_url is not None and not isinstance(self.parent_url, str):
            raise TypeError("parent_url must be a string or None.")

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))


@dataclass(frozen=True)
class ResearchReport:
    """Aggregated outcome of a research operation with full source, evidence, and traversal attribution."""

    query: str
    sources: tuple[ResearchSource, ...] = field(default_factory=tuple)
    failed_sources: tuple[ResearchSource, ...] = field(default_factory=tuple)
    summary: str | None = None
    evidence: tuple[EvidenceItem, ...] = field(default_factory=tuple)
    contradictions: tuple[EvidenceConflict, ...] = field(default_factory=tuple)
    discovered_links: tuple[DiscoveredLink, ...] = field(default_factory=tuple)
    traversal_stats: dict[str, Any] = field(default_factory=dict)
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

        if isinstance(self.evidence, (list, tuple)):
            for ev in self.evidence:
                if not isinstance(ev, EvidenceItem):
                    raise TypeError("All items in evidence must be EvidenceItem instances.")
            object.__setattr__(self, "evidence", tuple(self.evidence))
        else:
            raise TypeError("evidence must be a list or tuple of EvidenceItem instances.")

        if isinstance(self.contradictions, (list, tuple)):
            for ct in self.contradictions:
                if not isinstance(ct, EvidenceConflict):
                    raise TypeError("All items in contradictions must be EvidenceConflict instances.")
            object.__setattr__(self, "contradictions", tuple(self.contradictions))
        else:
            raise TypeError("contradictions must be a list or tuple of EvidenceConflict instances.")

        if isinstance(self.discovered_links, (list, tuple)):
            for dl in self.discovered_links:
                if not isinstance(dl, DiscoveredLink):
                    raise TypeError("All items in discovered_links must be DiscoveredLink instances.")
            object.__setattr__(self, "discovered_links", tuple(self.discovered_links))
        else:
            raise TypeError("discovered_links must be a list or tuple of DiscoveredLink instances.")

        if not isinstance(self.traversal_stats, dict):
            raise TypeError("traversal_stats must be a dict.")
        object.__setattr__(self, "traversal_stats", _sanitize_metadata(self.traversal_stats))

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))

    @property
    def has_sources(self) -> bool:
        """Check if any successful sources are available."""
        return len(self.sources) > 0

    @property
    def has_contradictions(self) -> bool:
        """Check if any potential evidence contradictions were detected."""
        return len(self.contradictions) > 0

    def format_citations(self) -> str:
        """Format source citations for prompt injection or report rendering."""
        lines = []
        for idx, src in enumerate(self.sources, 1):
            hop_tag = f" [Hop {src.hop}]" if src.hop > 0 else ""
            lines.append(f"[{idx}] {src.title} - {src.url}{hop_tag}")
        return "\n".join(lines)

    def format_evidence_summary(self) -> str:
        """Format bounded evidence passages with source linkage."""
        if not self.evidence:
            return "No specific evidence passages extracted."
        lines = []
        for idx, ev in enumerate(self.evidence, 1):
            lines.append(f"[{idx}] ({ev.source_title}) {ev.content}")
        return "\n".join(lines)
