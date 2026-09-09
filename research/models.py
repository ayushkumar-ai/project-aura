import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.parse import urlparse


def _extract_domain(url: str) -> str:
    """Extract domain/hostname from URL if valid, else return empty string."""
    try:
        parsed = urlparse(url)
        return parsed.hostname or parsed.netloc or ""
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
    published_at: str | None = None
    freshness_score: float = 1.0
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

        if self.published_at is not None:
            if not isinstance(self.published_at, str) or not self.published_at.strip():
                raise ValueError("published_at must be a non-empty string or None.")
            object.__setattr__(self, "published_at", self.published_at.strip())

        if not isinstance(self.freshness_score, (int, float)) or not (0.0 <= self.freshness_score <= 1.0):
            raise ValueError("freshness_score must be a float between 0.0 and 1.0.")
        object.__setattr__(self, "freshness_score", float(self.freshness_score))

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
    quality_score: float = 1.0
    published_at: str | None = None
    freshness_score: float = 1.0
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

        if not isinstance(self.quality_score, (int, float)):
            raise TypeError("quality_score must be a numeric value.")
        object.__setattr__(self, "quality_score", float(self.quality_score))

        if self.published_at is not None:
            if not isinstance(self.published_at, str) or not self.published_at.strip():
                raise ValueError("published_at must be a non-empty string or None.")
            object.__setattr__(self, "published_at", self.published_at.strip())

        if not isinstance(self.freshness_score, (int, float)) or not (0.0 <= self.freshness_score <= 1.0):
            raise ValueError("freshness_score must be a float between 0.0 and 1.0.")
        object.__setattr__(self, "freshness_score", float(self.freshness_score))

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))


@dataclass(frozen=True)
class ResearchSubQuestion:
    """Represents a decomposed sub-question derived from a complex research query."""

    sub_question_id: str
    query: str
    rationale: str = ""
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.sub_question_id, str) or not self.sub_question_id.strip():
            raise ValueError("sub_question_id must be a non-empty string.")
        object.__setattr__(self, "sub_question_id", self.sub_question_id.strip())

        if not isinstance(self.query, str) or not self.query.strip():
            raise ValueError("query must be a non-empty string.")
        object.__setattr__(self, "query", self.query.strip())

        if not isinstance(self.rationale, str):
            raise TypeError("rationale must be a string.")
        object.__setattr__(self, "rationale", self.rationale.strip())

        if not isinstance(self.weight, (int, float)) or self.weight <= 0:
            raise ValueError("weight must be a positive number.")
        object.__setattr__(self, "weight", float(self.weight))

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))


class ClaimVerificationStatus(str, Enum):
    """Authoritative deterministic verification status of a factual claim."""

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    CONTRADICTED = "contradicted"
    UNSUPPORTED = "unsupported"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class ClaimEvidence:
    """Associates an evidence passage with a specific claim, source, and stance."""

    source_url: str
    source_title: str
    passage: str
    stance: str = "supports"  # "supports" | "refutes" | "neutral"
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.source_url, str) or not self.source_url.strip():
            raise ValueError("source_url must be a non-empty string.")
        object.__setattr__(self, "source_url", self.source_url.strip())

        if not isinstance(self.source_title, str):
            raise TypeError("source_title must be a string.")
        object.__setattr__(self, "source_title", self.source_title.strip())

        if not isinstance(self.passage, str) or not self.passage.strip():
            raise ValueError("passage must be a non-empty string.")
        object.__setattr__(self, "passage", self.passage.strip())

        if not isinstance(self.stance, str) or self.stance not in ("supports", "refutes", "neutral"):
            raise ValueError("stance must be one of: 'supports', 'refutes', 'neutral'.")
        object.__setattr__(self, "stance", self.stance.strip())

        if not isinstance(self.confidence, (int, float)) or not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be a float between 0.0 and 1.0.")
        object.__setattr__(self, "confidence", float(self.confidence))

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))


@dataclass(frozen=True)
class ResearchClaim:
    """Represents an extracted factual claim with supporting and refuting evidence linkages."""

    claim_id: str
    statement: str
    sub_question_id: str = ""
    supporting_sources: tuple[ClaimEvidence, ...] = field(default_factory=tuple)
    refuting_sources: tuple[ClaimEvidence, ...] = field(default_factory=tuple)
    consensus_status: str = "supported"  # "supported" | "partially_supported" | "contradicted" | "unsupported" | "uncertain" | "disputed" | "unverified"
    confidence_score: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.claim_id, str) or not self.claim_id.strip():
            raise ValueError("claim_id must be a non-empty string.")
        object.__setattr__(self, "claim_id", self.claim_id.strip())

        if not isinstance(self.statement, str) or not self.statement.strip():
            raise ValueError("statement must be a non-empty string.")
        object.__setattr__(self, "statement", self.statement.strip())

        if not isinstance(self.sub_question_id, str):
            raise TypeError("sub_question_id must be a string.")
        object.__setattr__(self, "sub_question_id", self.sub_question_id.strip())

        if isinstance(self.supporting_sources, (list, tuple)):
            for ev in self.supporting_sources:
                if not isinstance(ev, ClaimEvidence):
                    raise TypeError("All items in supporting_sources must be ClaimEvidence instances.")
            object.__setattr__(self, "supporting_sources", tuple(self.supporting_sources))
        else:
            raise TypeError("supporting_sources must be a list or tuple of ClaimEvidence instances.")

        if isinstance(self.refuting_sources, (list, tuple)):
            for ev in self.refuting_sources:
                if not isinstance(ev, ClaimEvidence):
                    raise TypeError("All items in refuting_sources must be ClaimEvidence instances.")
            object.__setattr__(self, "refuting_sources", tuple(self.refuting_sources))
        else:
            raise TypeError("refuting_sources must be a list or tuple of ClaimEvidence instances.")

        valid_statuses = (
            "supported",
            "partially_supported",
            "contradicted",
            "unsupported",
            "uncertain",
            "disputed",
            "unverified",
        )
        if not isinstance(self.consensus_status, str) or self.consensus_status not in valid_statuses:
            raise ValueError(f"consensus_status must be one of: {valid_statuses}.")
        object.__setattr__(self, "consensus_status", self.consensus_status.strip())

        if not isinstance(self.confidence_score, (int, float)) or not (0.0 <= self.confidence_score <= 1.0):
            raise ValueError("confidence_score must be a float between 0.0 and 1.0.")
        object.__setattr__(self, "confidence_score", float(self.confidence_score))

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))

    @property
    def total_sources_count(self) -> int:
        """Return total distinct sources referencing this claim."""
        seen = {ev.source_url for ev in self.supporting_sources} | {ev.source_url for ev in self.refuting_sources}
        return len(seen)


@dataclass(frozen=True)
class VerifiedClaim:
    """Represents a research claim verified against source evidence with an authoritative status and confidence."""

    claim_id: str
    statement: str
    verification_status: ClaimVerificationStatus = ClaimVerificationStatus.UNSUPPORTED
    confidence_score: float = 0.0
    supporting_evidence: tuple[ClaimEvidence, ...] = field(default_factory=tuple)
    refuting_evidence: tuple[ClaimEvidence, ...] = field(default_factory=tuple)
    contradiction_ids: tuple[str, ...] = field(default_factory=tuple)
    reasoning: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.claim_id, str) or not self.claim_id.strip():
            raise ValueError("claim_id must be a non-empty string.")
        object.__setattr__(self, "claim_id", self.claim_id.strip())

        if not isinstance(self.statement, str) or not self.statement.strip():
            raise ValueError("statement must be a non-empty string.")
        object.__setattr__(self, "statement", self.statement.strip())

        if isinstance(self.verification_status, str):
            object.__setattr__(self, "verification_status", ClaimVerificationStatus(self.verification_status))
        elif not isinstance(self.verification_status, ClaimVerificationStatus):
            raise TypeError("verification_status must be an instance of ClaimVerificationStatus.")

        if not isinstance(self.confidence_score, (int, float)) or not (0.0 <= self.confidence_score <= 1.0):
            raise ValueError("confidence_score must be a float between 0.0 and 1.0.")
        object.__setattr__(self, "confidence_score", float(self.confidence_score))

        if isinstance(self.supporting_evidence, (list, tuple)):
            for ev in self.supporting_evidence:
                if not isinstance(ev, ClaimEvidence):
                    raise TypeError("All items in supporting_evidence must be ClaimEvidence instances.")
            object.__setattr__(self, "supporting_evidence", tuple(self.supporting_evidence))
        else:
            raise TypeError("supporting_evidence must be a sequence of ClaimEvidence instances.")

        if isinstance(self.refuting_evidence, (list, tuple)):
            for ev in self.refuting_evidence:
                if not isinstance(ev, ClaimEvidence):
                    raise TypeError("All items in refuting_evidence must be ClaimEvidence instances.")
            object.__setattr__(self, "refuting_evidence", tuple(self.refuting_evidence))
        else:
            raise TypeError("refuting_evidence must be a sequence of ClaimEvidence instances.")

        if isinstance(self.contradiction_ids, (list, tuple)):
            for cid in self.contradiction_ids:
                if not isinstance(cid, str):
                    raise TypeError("All items in contradiction_ids must be strings.")
            object.__setattr__(self, "contradiction_ids", tuple(self.contradiction_ids))
        else:
            raise TypeError("contradiction_ids must be a sequence of strings.")

        if not isinstance(self.reasoning, str):
            raise TypeError("reasoning must be a string.")
        object.__setattr__(self, "reasoning", self.reasoning.strip())

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))

    @property
    def is_supported(self) -> bool:
        """Check if claim is verified as supported."""
        return self.verification_status == ClaimVerificationStatus.SUPPORTED

    @property
    def is_partially_supported(self) -> bool:
        """Check if claim is partially supported by single source or limited evidence."""
        return self.verification_status == ClaimVerificationStatus.PARTIALLY_SUPPORTED

    @property
    def is_contradicted(self) -> bool:
        """Check if claim is flagged as contradicted by opposing evidence."""
        return self.verification_status == ClaimVerificationStatus.CONTRADICTED

    @property
    def is_unsupported(self) -> bool:
        """Check if claim lacks grounding evidence."""
        return self.verification_status == ClaimVerificationStatus.UNSUPPORTED

    @property
    def is_uncertain(self) -> bool:
        """Check if claim evidence is conflicting or ambiguous."""
        return self.verification_status == ClaimVerificationStatus.UNCERTAIN

    @property
    def evidence_count(self) -> int:
        """Total supporting and refuting evidence items."""
        return len(self.supporting_evidence) + len(self.refuting_evidence)


@dataclass(frozen=True)
class AnswerCitation:
    """Represents a verified citation linked to a specific ResearchSource."""

    citation_index: int
    source_url: str
    source_title: str
    evidence_passage: str = ""
    domain: str = ""
    hop: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.citation_index, int) or self.citation_index <= 0:
            raise ValueError("citation_index must be a positive integer.")

        if not isinstance(self.source_url, str) or not self.source_url.strip():
            raise ValueError("source_url must be a non-empty string.")
        object.__setattr__(self, "source_url", self.source_url.strip())

        if not isinstance(self.source_title, str):
            raise TypeError("source_title must be a string.")
        object.__setattr__(self, "source_title", self.source_title.strip())

        if not isinstance(self.evidence_passage, str):
            raise TypeError("evidence_passage must be a string.")
        object.__setattr__(self, "evidence_passage", self.evidence_passage.strip())

        domain = self.domain.strip() if self.domain else _extract_domain(self.source_url)
        object.__setattr__(self, "domain", domain)

        if not isinstance(self.hop, int) or self.hop < 0:
            raise ValueError("hop must be a non-negative integer.")

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))


@dataclass(frozen=True)
class AnswerSection:
    """Represents a structured subsection in an assembled research answer."""

    title: str
    content: str
    citations: tuple[int, ...] = field(default_factory=tuple)
    claim_ids: tuple[str, ...] = field(default_factory=tuple)
    section_type: str = "findings"  # "findings" | "conflict" | "limitations" | "sources" | "coverage"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("title must be a non-empty string.")
        object.__setattr__(self, "title", self.title.strip())

        if not isinstance(self.content, str):
            raise TypeError("content must be a string.")
        object.__setattr__(self, "content", self.content.strip())

        if isinstance(self.citations, (list, tuple)):
            for c in self.citations:
                if not isinstance(c, int) or c <= 0:
                    raise ValueError("All items in citations must be positive integers.")
            object.__setattr__(self, "citations", tuple(self.citations))
        else:
            raise TypeError("citations must be a sequence of positive integers.")

        if isinstance(self.claim_ids, (list, tuple)):
            for cid in self.claim_ids:
                if not isinstance(cid, str):
                    raise TypeError("All items in claim_ids must be strings.")
            object.__setattr__(self, "claim_ids", tuple(self.claim_ids))
        else:
            raise TypeError("claim_ids must be a sequence of strings.")

        valid_sec_types = ("findings", "conflict", "limitations", "sources", "coverage")
        if self.section_type not in valid_sec_types:
            raise ValueError(f"section_type must be one of: {valid_sec_types}.")

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))


@dataclass(frozen=True)
class AssembledAnswer:
    """Represents a complete, citation-annotated, evidence-verified research answer."""

    query: str
    summary: str
    sections: tuple[AnswerSection, ...] = field(default_factory=tuple)
    verified_claims: tuple[VerifiedClaim, ...] = field(default_factory=tuple)
    citations: tuple[AnswerCitation, ...] = field(default_factory=tuple)
    unsupported_claims_flagged: tuple[str, ...] = field(default_factory=tuple)
    conflicts_flagged: tuple[str, ...] = field(default_factory=tuple)
    formatted_answer: str = ""
    is_grounded: bool = True
    confidence_score: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.query, str) or not self.query.strip():
            raise ValueError("query must be a non-empty string.")
        object.__setattr__(self, "query", self.query.strip())

        if not isinstance(self.summary, str):
            raise TypeError("summary must be a string.")
        object.__setattr__(self, "summary", self.summary.strip())

        if isinstance(self.sections, (list, tuple)):
            for sec in self.sections:
                if not isinstance(sec, AnswerSection):
                    raise TypeError("All items in sections must be AnswerSection instances.")
            object.__setattr__(self, "sections", tuple(self.sections))
        else:
            raise TypeError("sections must be a sequence of AnswerSection instances.")

        if isinstance(self.verified_claims, (list, tuple)):
            for vc in self.verified_claims:
                if not isinstance(vc, VerifiedClaim):
                    raise TypeError("All items in verified_claims must be VerifiedClaim instances.")
            object.__setattr__(self, "verified_claims", tuple(self.verified_claims))
        else:
            raise TypeError("verified_claims must be a sequence of VerifiedClaim instances.")

        if isinstance(self.citations, (list, tuple)):
            for c in self.citations:
                if not isinstance(c, AnswerCitation):
                    raise TypeError("All items in citations must be AnswerCitation instances.")
            object.__setattr__(self, "citations", tuple(self.citations))
        else:
            raise TypeError("citations must be a sequence of AnswerCitation instances.")

        if isinstance(self.unsupported_claims_flagged, (list, tuple)):
            for u in self.unsupported_claims_flagged:
                if not isinstance(u, str):
                    raise TypeError("All items in unsupported_claims_flagged must be strings.")
            object.__setattr__(self, "unsupported_claims_flagged", tuple(self.unsupported_claims_flagged))
        else:
            raise TypeError("unsupported_claims_flagged must be a sequence of strings.")

        if isinstance(self.conflicts_flagged, (list, tuple)):
            for c in self.conflicts_flagged:
                if not isinstance(c, str):
                    raise TypeError("All items in conflicts_flagged must be strings.")
            object.__setattr__(self, "conflicts_flagged", tuple(self.conflicts_flagged))
        else:
            raise TypeError("conflicts_flagged must be a sequence of strings.")

        if not isinstance(self.formatted_answer, str):
            raise TypeError("formatted_answer must be a string.")

        if not isinstance(self.is_grounded, bool):
            raise TypeError("is_grounded must be a boolean.")

        if not isinstance(self.confidence_score, (int, float)) or not (0.0 <= self.confidence_score <= 1.0):
            raise ValueError("confidence_score must be a float between 0.0 and 1.0.")
        object.__setattr__(self, "confidence_score", float(self.confidence_score))

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))


@dataclass(frozen=True)
class CitationValidationResult:
    """Outcome of citation verification against retrieved research sources."""

    is_valid: bool
    total_citations_found: int
    valid_citations: tuple[int, ...] = field(default_factory=tuple)
    invalid_citations: tuple[int, ...] = field(default_factory=tuple)
    unreferenced_sources: tuple[int, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.is_valid, bool):
            raise TypeError("is_valid must be a boolean.")

        if not isinstance(self.total_citations_found, int) or self.total_citations_found < 0:
            raise ValueError("total_citations_found must be a non-negative integer.")

        if isinstance(self.valid_citations, (list, tuple)):
            for c in self.valid_citations:
                if not isinstance(c, int) or c <= 0:
                    raise ValueError("All valid_citations must be positive integers.")
            object.__setattr__(self, "valid_citations", tuple(self.valid_citations))
        else:
            raise TypeError("valid_citations must be a sequence of positive integers.")

        if isinstance(self.invalid_citations, (list, tuple)):
            for c in self.invalid_citations:
                if not isinstance(c, int) or c <= 0:
                    raise ValueError("All invalid_citations must be positive integers.")
            object.__setattr__(self, "invalid_citations", tuple(self.invalid_citations))
        else:
            raise TypeError("invalid_citations must be a sequence of positive integers.")

        if isinstance(self.unreferenced_sources, (list, tuple)):
            for c in self.unreferenced_sources:
                if not isinstance(c, int) or c <= 0:
                    raise ValueError("All unreferenced_sources must be positive integers.")
            object.__setattr__(self, "unreferenced_sources", tuple(self.unreferenced_sources))
        else:
            raise TypeError("unreferenced_sources must be a sequence of positive integers.")

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))

    @property
    def has_hallucinated_citations(self) -> bool:
        """Check if any invalid or out-of-bounds citations were detected."""
        return len(self.invalid_citations) > 0


@dataclass(frozen=True)
class ResearchConfidence:
    """Aggregate confidence and coverage signals for a research operation."""

    overall_score: float  # 0.0 to 1.0
    coverage_ratio: float  # Sub-questions covered / total sub-questions
    source_diversity_score: float  # Distinct domains ratio
    evidence_density: float  # Average evidence items per source
    contradiction_penalty: float = 0.0  # Deduction for unresolved contradictions
    citation_validity_score: float = 1.0  # Ratio of valid citations
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        for field_name in ("overall_score", "coverage_ratio", "source_diversity_score", "citation_validity_score"):
            val = getattr(self, field_name)
            if not isinstance(val, (int, float)) or not (0.0 <= val <= 1.0):
                raise ValueError(f"{field_name} must be a float between 0.0 and 1.0.")
            object.__setattr__(self, field_name, float(val))

        if not isinstance(self.evidence_density, (int, float)) or self.evidence_density < 0:
            raise ValueError("evidence_density must be a non-negative float.")
        object.__setattr__(self, "evidence_density", float(self.evidence_density))

        if not isinstance(self.contradiction_penalty, (int, float)) or self.contradiction_penalty < 0:
            raise ValueError("contradiction_penalty must be a non-negative float.")
        object.__setattr__(self, "contradiction_penalty", float(self.contradiction_penalty))

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))


@dataclass(frozen=True)
class SubQuestionCoverage:
    """Coverage and resolution state for an individual research sub-question."""

    sub_question_id: str
    query: str
    evidence_count: int = 0
    source_count: int = 0
    has_conflict: bool = False
    is_resolved: bool = False
    status: str = "unresolved"  # "covered" | "insufficient" | "conflicted" | "unresolved"
    distinct_domains: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.sub_question_id, str) or not self.sub_question_id.strip():
            raise ValueError("sub_question_id must be a non-empty string.")
        object.__setattr__(self, "sub_question_id", self.sub_question_id.strip())

        if not isinstance(self.query, str) or not self.query.strip():
            raise ValueError("query must be a non-empty string.")
        object.__setattr__(self, "query", self.query.strip())

        if not isinstance(self.evidence_count, int) or self.evidence_count < 0:
            raise ValueError("evidence_count must be a non-negative integer.")

        if not isinstance(self.source_count, int) or self.source_count < 0:
            raise ValueError("source_count must be a non-negative integer.")

        if not isinstance(self.has_conflict, bool):
            raise TypeError("has_conflict must be a boolean.")

        if not isinstance(self.is_resolved, bool):
            raise TypeError("is_resolved must be a boolean.")

        valid_statuses = ("covered", "insufficient", "conflicted", "unresolved")
        if not isinstance(self.status, str) or self.status not in valid_statuses:
            raise ValueError(f"status must be one of: {valid_statuses}.")
        object.__setattr__(self, "status", self.status.strip())

        if isinstance(self.distinct_domains, (list, tuple, set)):
            object.__setattr__(self, "distinct_domains", tuple(str(d).strip() for d in self.distinct_domains if str(d).strip()))
        else:
            raise TypeError("distinct_domains must be a sequence of strings.")

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))


@dataclass(frozen=True)
class ResearchCoverage:
    """Comprehensive coverage and resolution evaluation of an iterative research operation."""

    overall_score: float  # 0.0 to 1.0
    coverage_ratio: float  # 0.0 to 1.0
    is_sufficient: bool
    total_sub_questions: int
    covered_sub_questions: int
    unresolved_sub_questions: tuple[str, ...] = field(default_factory=tuple)
    sub_question_coverages: tuple[SubQuestionCoverage, ...] = field(default_factory=tuple)
    source_diversity_score: float = 1.0
    distinct_domains: tuple[str, ...] = field(default_factory=tuple)
    explanation: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        for field_name in ("overall_score", "coverage_ratio", "source_diversity_score"):
            val = getattr(self, field_name)
            if not isinstance(val, (int, float)) or not (0.0 <= val <= 1.0):
                raise ValueError(f"{field_name} must be a float between 0.0 and 1.0.")
            object.__setattr__(self, field_name, float(val))

        if not isinstance(self.is_sufficient, bool):
            raise TypeError("is_sufficient must be a boolean.")

        if not isinstance(self.total_sub_questions, int) or self.total_sub_questions < 0:
            raise ValueError("total_sub_questions must be a non-negative integer.")

        if not isinstance(self.covered_sub_questions, int) or self.covered_sub_questions < 0:
            raise ValueError("covered_sub_questions must be a non-negative integer.")

        if isinstance(self.unresolved_sub_questions, (list, tuple)):
            object.__setattr__(self, "unresolved_sub_questions", tuple(str(q).strip() for q in self.unresolved_sub_questions if str(q).strip()))
        else:
            raise TypeError("unresolved_sub_questions must be a sequence of strings.")

        if isinstance(self.sub_question_coverages, (list, tuple)):
            for sqc in self.sub_question_coverages:
                if not isinstance(sqc, SubQuestionCoverage):
                    raise TypeError("All items in sub_question_coverages must be SubQuestionCoverage instances.")
            object.__setattr__(self, "sub_question_coverages", tuple(self.sub_question_coverages))
        else:
            raise TypeError("sub_question_coverages must be a sequence of SubQuestionCoverage instances.")

        if isinstance(self.distinct_domains, (list, tuple, set)):
            object.__setattr__(self, "distinct_domains", tuple(str(d).strip() for d in self.distinct_domains if str(d).strip()))
        else:
            raise TypeError("distinct_domains must be a sequence of strings.")

        if not isinstance(self.explanation, str):
            raise TypeError("explanation must be a string.")
        object.__setattr__(self, "explanation", self.explanation.strip())

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))


@dataclass(frozen=True)
class ResearchReport:
    """Aggregated outcome of a research operation with full source, evidence, claim, verification, and answer attribution."""

    query: str
    sources: tuple[ResearchSource, ...] = field(default_factory=tuple)
    failed_sources: tuple[ResearchSource, ...] = field(default_factory=tuple)
    summary: str | None = None
    evidence: tuple[EvidenceItem, ...] = field(default_factory=tuple)
    contradictions: tuple[EvidenceConflict, ...] = field(default_factory=tuple)
    discovered_links: tuple[DiscoveredLink, ...] = field(default_factory=tuple)
    traversal_stats: dict[str, Any] = field(default_factory=dict)
    # M9.6 additions
    sub_questions: tuple[ResearchSubQuestion, ...] = field(default_factory=tuple)
    claims: tuple[ResearchClaim, ...] = field(default_factory=tuple)
    confidence: ResearchConfidence | None = None
    citation_validation: CitationValidationResult | None = None
    # M9.7 additions
    verified_claims: tuple[VerifiedClaim, ...] = field(default_factory=tuple)
    assembled_answer: AssembledAnswer | None = None
    # M9.9 additions
    coverage: ResearchCoverage | None = None
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

        if isinstance(self.sub_questions, (list, tuple)):
            for sq in self.sub_questions:
                if not isinstance(sq, ResearchSubQuestion):
                    raise TypeError("All items in sub_questions must be ResearchSubQuestion instances.")
            object.__setattr__(self, "sub_questions", tuple(self.sub_questions))
        else:
            raise TypeError("sub_questions must be a list or tuple of ResearchSubQuestion instances.")

        if isinstance(self.claims, (list, tuple)):
            for cl in self.claims:
                if not isinstance(cl, ResearchClaim):
                    raise TypeError("All items in claims must be ResearchClaim instances.")
            object.__setattr__(self, "claims", tuple(self.claims))
        else:
            raise TypeError("claims must be a list or tuple of ResearchClaim instances.")

        if self.confidence is not None and not isinstance(self.confidence, ResearchConfidence):
            raise TypeError("confidence must be an instance of ResearchConfidence or None.")

        if self.citation_validation is not None and not isinstance(self.citation_validation, CitationValidationResult):
            raise TypeError("citation_validation must be an instance of CitationValidationResult or None.")

        if isinstance(self.verified_claims, (list, tuple)):
            for vc in self.verified_claims:
                if not isinstance(vc, VerifiedClaim):
                    raise TypeError("All items in verified_claims must be VerifiedClaim instances.")
            object.__setattr__(self, "verified_claims", tuple(self.verified_claims))
        else:
            raise TypeError("verified_claims must be a sequence of VerifiedClaim instances.")

        if self.assembled_answer is not None and not isinstance(self.assembled_answer, AssembledAnswer):
            raise TypeError("assembled_answer must be an instance of AssembledAnswer or None.")

        if self.coverage is not None and not isinstance(self.coverage, ResearchCoverage):
            raise TypeError("coverage must be an instance of ResearchCoverage or None.")

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

    @property
    def has_claims(self) -> bool:
        """Check if any structured research claims were extracted."""
        return len(self.claims) > 0

    @property
    def has_verified_claims(self) -> bool:
        """Check if any verified claims are available."""
        return len(self.verified_claims) > 0

    @property
    def has_coverage(self) -> bool:
        """Check if research coverage evaluation is present."""
        return self.coverage is not None

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

    def format_claims_summary(self) -> str:
        """Format structured factual claims and consensus statuses."""
        if not self.claims:
            return "No structured claims extracted."
        lines = []
        for idx, cl in enumerate(self.claims, 1):
            status_tag = f"[{cl.consensus_status.upper()}]"
            lines.append(f"{idx}. {status_tag} {cl.statement} (Sources: {cl.total_sources_count})")
        return "\n".join(lines)

    def format_verified_claims_summary(self) -> str:
        """Format verified factual claims with authoritative status and confidence."""
        if not self.verified_claims:
            return "No verified claims available."
        lines = []
        for idx, vc in enumerate(self.verified_claims, 1):
            status_tag = f"[{vc.verification_status.value.upper()}]"
            lines.append(f"{idx}. {status_tag} (Conf: {vc.confidence_score:.2f}) {vc.statement}")
        return "\n".join(lines)
