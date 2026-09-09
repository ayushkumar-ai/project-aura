import time
from dataclasses import dataclass, field
from typing import Any

from research.models import (
    DiscoveredLink,
    EvidenceItem,
    ResearchSource,
    ResearchSubQuestion,
    _sanitize_metadata,
)


@dataclass(frozen=True)
class ResearchCheckpoint:
    """Serializable snapshot of an in-progress or completed research traversal, including iterative state."""

    query: str
    visited_urls: tuple[str, ...] = field(default_factory=tuple)
    successful_sources: tuple[ResearchSource, ...] = field(default_factory=tuple)
    failed_sources: tuple[ResearchSource, ...] = field(default_factory=tuple)
    discovered_links: tuple[DiscoveredLink, ...] = field(default_factory=tuple)
    pending_links: tuple[tuple[float, int, str, DiscoveredLink], ...] = field(default_factory=tuple)
    accumulated_chars: int = 0
    total_fetches: int = 0
    traversal_stats: dict[str, Any] = field(default_factory=dict)
    max_hops: int = 2
    max_pages: int = 6
    timestamp: float = field(default_factory=time.time)
    # M9.9 Iterative state additions
    decomposed_sub_questions: tuple[ResearchSubQuestion, ...] = field(default_factory=tuple)
    completed_sub_questions: tuple[str, ...] = field(default_factory=tuple)
    pending_sub_questions: tuple[str, ...] = field(default_factory=tuple)
    research_rounds: int = 1
    total_queries: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.query, str) or not self.query.strip():
            raise ValueError("query must be a non-empty string.")
        object.__setattr__(self, "query", self.query.strip())

        if isinstance(self.visited_urls, (list, tuple, set, frozenset)):
            for u in self.visited_urls:
                if not isinstance(u, str):
                    raise TypeError("All items in visited_urls must be strings.")
            object.__setattr__(self, "visited_urls", tuple(self.visited_urls))
        else:
            raise TypeError("visited_urls must be a sequence of strings.")

        if isinstance(self.successful_sources, (list, tuple)):
            for s in self.successful_sources:
                if not isinstance(s, ResearchSource):
                    raise TypeError("All items in successful_sources must be ResearchSource instances.")
            object.__setattr__(self, "successful_sources", tuple(self.successful_sources))
        else:
            raise TypeError("successful_sources must be a sequence of ResearchSource instances.")

        if isinstance(self.failed_sources, (list, tuple)):
            for s in self.failed_sources:
                if not isinstance(s, ResearchSource):
                    raise TypeError("All items in failed_sources must be ResearchSource instances.")
            object.__setattr__(self, "failed_sources", tuple(self.failed_sources))
        else:
            raise TypeError("failed_sources must be a sequence of ResearchSource instances.")

        if isinstance(self.discovered_links, (list, tuple)):
            for dl in self.discovered_links:
                if not isinstance(dl, DiscoveredLink):
                    raise TypeError("All items in discovered_links must be DiscoveredLink instances.")
            object.__setattr__(self, "discovered_links", tuple(self.discovered_links))
        else:
            raise TypeError("discovered_links must be a sequence of DiscoveredLink instances.")

        if isinstance(self.pending_links, (list, tuple)):
            clean_pending = []
            for item in self.pending_links:
                if not (isinstance(item, (list, tuple)) and len(item) == 4):
                    raise TypeError("Each pending link must be a 4-tuple: (neg_score, hop, url, DiscoveredLink).")
                score, hop, url_str, dl = item
                if not isinstance(dl, DiscoveredLink):
                    raise TypeError("Fourth element of pending link must be a DiscoveredLink instance.")
                clean_pending.append((float(score), int(hop), str(url_str), dl))
            object.__setattr__(self, "pending_links", tuple(clean_pending))
        else:
            raise TypeError("pending_links must be a sequence of pending link tuples.")

        if not isinstance(self.accumulated_chars, int) or self.accumulated_chars < 0:
            raise ValueError("accumulated_chars must be a non-negative integer.")

        if not isinstance(self.total_fetches, int) or self.total_fetches < 0:
            raise ValueError("total_fetches must be a non-negative integer.")

        if not isinstance(self.max_hops, int) or self.max_hops < 0:
            raise ValueError("max_hops must be a non-negative integer.")

        if not isinstance(self.max_pages, int) or self.max_pages <= 0:
            raise ValueError("max_pages must be a positive integer.")

        if not isinstance(self.timestamp, (int, float)) or self.timestamp <= 0:
            raise ValueError("timestamp must be a positive float.")

        # M9.9 fields validation
        if isinstance(self.decomposed_sub_questions, (list, tuple)):
            for sq in self.decomposed_sub_questions:
                if not isinstance(sq, ResearchSubQuestion):
                    raise TypeError("All items in decomposed_sub_questions must be ResearchSubQuestion instances.")
            object.__setattr__(self, "decomposed_sub_questions", tuple(self.decomposed_sub_questions))
        else:
            raise TypeError("decomposed_sub_questions must be a sequence of ResearchSubQuestion instances.")

        if isinstance(self.completed_sub_questions, (list, tuple, set)):
            object.__setattr__(self, "completed_sub_questions", tuple(str(q) for q in self.completed_sub_questions))
        else:
            raise TypeError("completed_sub_questions must be a sequence of strings.")

        if isinstance(self.pending_sub_questions, (list, tuple, set)):
            object.__setattr__(self, "pending_sub_questions", tuple(str(q) for q in self.pending_sub_questions))
        else:
            raise TypeError("pending_sub_questions must be a sequence of strings.")

        if not isinstance(self.research_rounds, int) or self.research_rounds < 0:
            raise ValueError("research_rounds must be a non-negative integer.")

        if not isinstance(self.total_queries, int) or self.total_queries < 0:
            raise ValueError("total_queries must be a non-negative integer.")

        if not isinstance(self.traversal_stats, dict):
            raise TypeError("traversal_stats must be a dict.")
        object.__setattr__(self, "traversal_stats", _sanitize_metadata(self.traversal_stats))

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))

    @property
    def total_completed(self) -> int:
        """Total number of successful and failed sources processed."""
        return len(self.successful_sources) + len(self.failed_sources)

    @property
    def is_complete(self) -> bool:
        """Check if research has met its page goal or exhausted pending links."""
        return len(self.successful_sources) >= self.max_pages or (len(self.pending_links) == 0 and len(self.visited_urls) > 0)


def serialize_research_checkpoint(checkpoint: ResearchCheckpoint) -> dict[str, Any]:
    """Convert a ResearchCheckpoint into a JSON-serializable dictionary."""
    if not isinstance(checkpoint, ResearchCheckpoint):
        raise TypeError("checkpoint must be a ResearchCheckpoint instance.")

    def _serialize_source(src: ResearchSource) -> dict[str, Any]:
        return {
            "url": src.url,
            "title": src.title,
            "snippet": src.snippet,
            "content": src.content,
            "status": src.status,
            "error": src.error,
            "source_domain": src.source_domain,
            "rank_score": src.rank_score,
            "hop": src.hop,
            "parent_url": src.parent_url,
            "quality_score": src.quality_score,
            "published_at": src.published_at,
            "freshness_score": src.freshness_score,
            "evidence": [
                {
                    "source_url": ev.source_url,
                    "source_title": ev.source_title,
                    "source_domain": ev.source_domain,
                    "content": ev.content,
                    "relevance_score": ev.relevance_score,
                    "metadata": ev.metadata,
                }
                for ev in src.evidence
            ],
            "metadata": src.metadata,
        }

    def _serialize_link(dl: DiscoveredLink) -> dict[str, Any]:
        return {
            "source_url": dl.source_url,
            "target_url": dl.target_url,
            "anchor_text": dl.anchor_text,
            "source_title": dl.source_title,
            "source_domain": dl.source_domain,
            "hop": dl.hop,
            "relevance_score": dl.relevance_score,
            "metadata": dl.metadata,
        }

    def _serialize_sub_q(sq: ResearchSubQuestion) -> dict[str, Any]:
        return {
            "sub_question_id": sq.sub_question_id,
            "query": sq.query,
            "rationale": sq.rationale,
            "weight": sq.weight,
            "metadata": sq.metadata,
        }

    return {
        "query": checkpoint.query,
        "visited_urls": list(checkpoint.visited_urls),
        "successful_sources": [_serialize_source(s) for s in checkpoint.successful_sources],
        "failed_sources": [_serialize_source(s) for s in checkpoint.failed_sources],
        "discovered_links": [_serialize_link(dl) for dl in checkpoint.discovered_links],
        "pending_links": [
            [item[0], item[1], item[2], _serialize_link(item[3])]
            for item in checkpoint.pending_links
        ],
        "accumulated_chars": checkpoint.accumulated_chars,
        "total_fetches": checkpoint.total_fetches,
        "traversal_stats": checkpoint.traversal_stats,
        "max_hops": checkpoint.max_hops,
        "max_pages": checkpoint.max_pages,
        "timestamp": checkpoint.timestamp,
        "decomposed_sub_questions": [_serialize_sub_q(sq) for sq in checkpoint.decomposed_sub_questions],
        "completed_sub_questions": list(checkpoint.completed_sub_questions),
        "pending_sub_questions": list(checkpoint.pending_sub_questions),
        "research_rounds": checkpoint.research_rounds,
        "total_queries": checkpoint.total_queries,
        "metadata": checkpoint.metadata,
    }


def deserialize_research_checkpoint(data: dict[str, Any]) -> ResearchCheckpoint:
    """Reconstruct a typed, validated ResearchCheckpoint from dictionary data."""
    if not isinstance(data, dict):
        raise TypeError("data must be a dictionary.")

    query = data.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Checkpoint data missing valid 'query'.")

    def _deserialize_evidence(ev_dict: dict[str, Any]) -> EvidenceItem:
        if not isinstance(ev_dict, dict):
            raise TypeError("Evidence item must be a dictionary.")
        return EvidenceItem(
            source_url=str(ev_dict.get("source_url", "")),
            source_title=str(ev_dict.get("source_title", "")),
            source_domain=str(ev_dict.get("source_domain", "")),
            content=str(ev_dict.get("content", "")),
            relevance_score=float(ev_dict.get("relevance_score", 0.0)),
            metadata=dict(ev_dict.get("metadata", {})),
        )

    def _deserialize_source(s_dict: dict[str, Any]) -> ResearchSource:
        if not isinstance(s_dict, dict):
            raise TypeError("Source item must be a dictionary.")
        ev_list = [_deserialize_evidence(e) for e in s_dict.get("evidence", []) if isinstance(e, dict)]
        return ResearchSource(
            url=str(s_dict.get("url", "")),
            title=str(s_dict.get("title", "")),
            snippet=str(s_dict.get("snippet", "")),
            content=str(s_dict.get("content", "")),
            status=str(s_dict.get("status", "success")),
            error=s_dict.get("error"),
            source_domain=str(s_dict.get("source_domain", "")),
            rank_score=float(s_dict.get("rank_score", 0.0)),
            hop=int(s_dict.get("hop", 0)),
            parent_url=s_dict.get("parent_url"),
            quality_score=float(s_dict.get("quality_score", 1.0)),
            published_at=s_dict.get("published_at"),
            freshness_score=float(s_dict.get("freshness_score", 1.0)),
            evidence=tuple(ev_list),
            metadata=dict(s_dict.get("metadata", {})),
        )

    def _deserialize_link(dl_dict: dict[str, Any]) -> DiscoveredLink:
        if not isinstance(dl_dict, dict):
            raise TypeError("Discovered link item must be a dictionary.")
        return DiscoveredLink(
            source_url=str(dl_dict.get("source_url", "")),
            target_url=str(dl_dict.get("target_url", "")),
            anchor_text=str(dl_dict.get("anchor_text", "")),
            source_title=str(dl_dict.get("source_title", "")),
            source_domain=str(dl_dict.get("source_domain", "")),
            hop=int(dl_dict.get("hop", 0)),
            relevance_score=float(dl_dict.get("relevance_score", 0.0)),
            metadata=dict(dl_dict.get("metadata", {})),
        )

    def _deserialize_sub_q(sq_dict: dict[str, Any]) -> ResearchSubQuestion:
        if not isinstance(sq_dict, dict):
            raise TypeError("SubQuestion item must be a dictionary.")
        return ResearchSubQuestion(
            sub_question_id=str(sq_dict.get("sub_question_id", "")),
            query=str(sq_dict.get("query", "")),
            rationale=str(sq_dict.get("rationale", "")),
            weight=float(sq_dict.get("weight", 1.0)),
            metadata=dict(sq_dict.get("metadata", {})),
        )

    visited_urls = tuple(str(u) for u in data.get("visited_urls", []))
    successful_sources = tuple(_deserialize_source(s) for s in data.get("successful_sources", []) if isinstance(s, dict))
    failed_sources = tuple(_deserialize_source(s) for s in data.get("failed_sources", []) if isinstance(s, dict))
    discovered_links = tuple(_deserialize_link(dl) for dl in data.get("discovered_links", []) if isinstance(dl, dict))

    pending_links = []
    for item in data.get("pending_links", []):
        if isinstance(item, (list, tuple)) and len(item) == 4 and isinstance(item[3], dict):
            score, hop, url_str, dl_dict = item
            dl_obj = _deserialize_link(dl_dict)
            pending_links.append((float(score), int(hop), str(url_str), dl_obj))

    sub_questions = tuple(_deserialize_sub_q(sq) for sq in data.get("decomposed_sub_questions", []) if isinstance(sq, dict))
    completed_sub_q = tuple(str(q) for q in data.get("completed_sub_questions", []))
    pending_sub_q = tuple(str(q) for q in data.get("pending_sub_questions", []))

    return ResearchCheckpoint(
        query=query,
        visited_urls=visited_urls,
        successful_sources=successful_sources,
        failed_sources=failed_sources,
        discovered_links=discovered_links,
        pending_links=tuple(pending_links),
        accumulated_chars=int(data.get("accumulated_chars", 0)),
        total_fetches=int(data.get("total_fetches", 0)),
        traversal_stats=dict(data.get("traversal_stats", {})),
        max_hops=int(data.get("max_hops", 2)),
        max_pages=int(data.get("max_pages", 6)),
        timestamp=float(data.get("timestamp", time.time())),
        decomposed_sub_questions=sub_questions,
        completed_sub_questions=completed_sub_q,
        pending_sub_questions=pending_sub_q,
        research_rounds=int(data.get("research_rounds", 1)),
        total_queries=int(data.get("total_queries", 0)),
        metadata=dict(data.get("metadata", {})),
    )
