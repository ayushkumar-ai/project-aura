import time
from dataclasses import dataclass, field
from typing import Any

from research.models import (
    ClaimEvidence,
    ClaimVerificationStatus,
    DiscoveredLink,
    EvidenceConflict,
    EvidenceItem,
    ResearchClaim,
    ResearchCoverage,
    ResearchSource,
    ResearchSubQuestion,
    SubQuestionCoverage,
    VerifiedClaim,
    _sanitize_metadata,
)


@dataclass(frozen=True)
class ResearchCheckpoint:
    """Serializable snapshot of an in-progress or completed research traversal, preserving all derived intelligence."""

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
    decomposed_sub_questions: tuple[ResearchSubQuestion, ...] = field(default_factory=tuple)
    completed_sub_questions: tuple[str, ...] = field(default_factory=tuple)
    pending_sub_questions: tuple[str, ...] = field(default_factory=tuple)
    research_rounds: int = 1
    total_queries: int = 0
    # M9.10 Full State Checkpointing additions
    claims: tuple[ResearchClaim, ...] = field(default_factory=tuple)
    contradictions: tuple[EvidenceConflict, ...] = field(default_factory=tuple)
    verified_claims: tuple[VerifiedClaim, ...] = field(default_factory=tuple)
    coverage: ResearchCoverage | None = None
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

        # M9.10 fields validation
        if isinstance(self.claims, (list, tuple)):
            for cl in self.claims:
                if not isinstance(cl, ResearchClaim):
                    raise TypeError("All items in claims must be ResearchClaim instances.")
            object.__setattr__(self, "claims", tuple(self.claims))
        else:
            raise TypeError("claims must be a sequence of ResearchClaim instances.")

        if isinstance(self.contradictions, (list, tuple)):
            for ct in self.contradictions:
                if not isinstance(ct, EvidenceConflict):
                    raise TypeError("All items in contradictions must be EvidenceConflict instances.")
            object.__setattr__(self, "contradictions", tuple(self.contradictions))
        else:
            raise TypeError("contradictions must be a sequence of EvidenceConflict instances.")

        if isinstance(self.verified_claims, (list, tuple)):
            for vc in self.verified_claims:
                if not isinstance(vc, VerifiedClaim):
                    raise TypeError("All items in verified_claims must be VerifiedClaim instances.")
            object.__setattr__(self, "verified_claims", tuple(self.verified_claims))
        else:
            raise TypeError("verified_claims must be a sequence of VerifiedClaim instances.")

        if self.coverage is not None and not isinstance(self.coverage, ResearchCoverage):
            raise TypeError("coverage must be an instance of ResearchCoverage or None.")

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
    """Convert a ResearchCheckpoint into a JSON-serializable dictionary with complete M9 research intelligence."""
    if not isinstance(checkpoint, ResearchCheckpoint):
        raise TypeError("checkpoint must be a ResearchCheckpoint instance.")

    def _serialize_evidence(ev: EvidenceItem) -> dict[str, Any]:
        return {
            "source_url": ev.source_url,
            "source_title": ev.source_title,
            "source_domain": ev.source_domain,
            "content": ev.content,
            "relevance_score": ev.relevance_score,
            "metadata": ev.metadata,
        }

    def _serialize_claim_evidence(ce: ClaimEvidence) -> dict[str, Any]:
        return {
            "source_url": ce.source_url,
            "source_title": ce.source_title,
            "passage": ce.passage,
            "stance": ce.stance,
            "confidence": ce.confidence,
            "metadata": ce.metadata,
        }

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
            "evidence": [_serialize_evidence(ev) for ev in src.evidence],
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

    def _serialize_claim(cl: ResearchClaim) -> dict[str, Any]:
        return {
            "claim_id": cl.claim_id,
            "statement": cl.statement,
            "sub_question_id": cl.sub_question_id,
            "supporting_sources": [_serialize_claim_evidence(ce) for ce in cl.supporting_sources],
            "refuting_sources": [_serialize_claim_evidence(ce) for ce in cl.refuting_sources],
            "consensus_status": cl.consensus_status,
            "confidence_score": cl.confidence_score,
            "metadata": cl.metadata,
        }

    def _serialize_conflict(ct: EvidenceConflict) -> dict[str, Any]:
        return {
            "claim": ct.claim,
            "source_a_url": ct.source_a_url,
            "source_a_evidence": ct.source_a_evidence,
            "source_b_url": ct.source_b_url,
            "source_b_evidence": ct.source_b_evidence,
            "conflict_type": ct.conflict_type,
            "metadata": ct.metadata,
        }

    def _serialize_verified_claim(vc: VerifiedClaim) -> dict[str, Any]:
        return {
            "claim_id": vc.claim_id,
            "statement": vc.statement,
            "verification_status": vc.verification_status.value,
            "confidence_score": vc.confidence_score,
            "supporting_evidence": [_serialize_claim_evidence(ce) for ce in vc.supporting_evidence],
            "refuting_evidence": [_serialize_claim_evidence(ce) for ce in vc.refuting_evidence],
            "contradiction_ids": list(vc.contradiction_ids),
            "reasoning": vc.reasoning,
            "metadata": vc.metadata,
        }

    def _serialize_coverage(cov: ResearchCoverage) -> dict[str, Any]:
        return {
            "overall_score": cov.overall_score,
            "coverage_ratio": cov.coverage_ratio,
            "is_sufficient": cov.is_sufficient,
            "total_sub_questions": cov.total_sub_questions,
            "covered_sub_questions": cov.covered_sub_questions,
            "unresolved_sub_questions": list(cov.unresolved_sub_questions),
            "source_diversity_score": cov.source_diversity_score,
            "distinct_domains": list(cov.distinct_domains),
            "explanation": cov.explanation,
            "sub_question_coverages": [
                {
                    "sub_question_id": sqc.sub_question_id,
                    "query": sqc.query,
                    "evidence_count": sqc.evidence_count,
                    "source_count": sqc.source_count,
                    "has_conflict": sqc.has_conflict,
                    "is_resolved": sqc.is_resolved,
                    "status": sqc.status,
                    "distinct_domains": list(sqc.distinct_domains),
                    "metadata": sqc.metadata,
                }
                for sqc in cov.sub_question_coverages
            ],
            "metadata": cov.metadata,
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
        "claims": [_serialize_claim(cl) for cl in checkpoint.claims],
        "contradictions": [_serialize_conflict(ct) for ct in checkpoint.contradictions],
        "verified_claims": [_serialize_verified_claim(vc) for vc in checkpoint.verified_claims],
        "coverage": _serialize_coverage(checkpoint.coverage) if checkpoint.coverage is not None else None,
        "metadata": checkpoint.metadata,
    }


def deserialize_research_checkpoint(data: dict[str, Any]) -> ResearchCheckpoint:
    """Reconstruct a typed, validated ResearchCheckpoint from dictionary data with backward compatibility."""
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

    def _deserialize_claim_evidence(ce_dict: dict[str, Any]) -> ClaimEvidence:
        if not isinstance(ce_dict, dict):
            raise TypeError("ClaimEvidence item must be a dictionary.")
        return ClaimEvidence(
            source_url=str(ce_dict.get("source_url", "")),
            source_title=str(ce_dict.get("source_title", "")),
            passage=str(ce_dict.get("passage", "")),
            stance=str(ce_dict.get("stance", "supports")),
            confidence=float(ce_dict.get("confidence", 1.0)),
            metadata=dict(ce_dict.get("metadata", {})),
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

    def _deserialize_claim(cl_dict: dict[str, Any]) -> ResearchClaim:
        if not isinstance(cl_dict, dict):
            raise TypeError("ResearchClaim item must be a dictionary.")
        supp = [_deserialize_claim_evidence(ce) for ce in cl_dict.get("supporting_sources", []) if isinstance(ce, dict)]
        ref = [_deserialize_claim_evidence(ce) for ce in cl_dict.get("refuting_sources", []) if isinstance(ce, dict)]
        return ResearchClaim(
            claim_id=str(cl_dict.get("claim_id", "")),
            statement=str(cl_dict.get("statement", "")),
            sub_question_id=str(cl_dict.get("sub_question_id", "")),
            supporting_sources=tuple(supp),
            refuting_sources=tuple(ref),
            consensus_status=str(cl_dict.get("consensus_status", "supported")),
            confidence_score=float(cl_dict.get("confidence_score", 1.0)),
            metadata=dict(cl_dict.get("metadata", {})),
        )

    def _deserialize_conflict(ct_dict: dict[str, Any]) -> EvidenceConflict:
        if not isinstance(ct_dict, dict):
            raise TypeError("EvidenceConflict item must be a dictionary.")
        return EvidenceConflict(
            claim=str(ct_dict.get("claim", "")),
            source_a_url=str(ct_dict.get("source_a_url", "")),
            source_a_evidence=str(ct_dict.get("source_a_evidence", "")),
            source_b_url=str(ct_dict.get("source_b_url", "")),
            source_b_evidence=str(ct_dict.get("source_b_evidence", "")),
            conflict_type=str(ct_dict.get("conflict_type", "divergent_claim")),
            metadata=dict(ct_dict.get("metadata", {})),
        )

    def _deserialize_verified_claim(vc_dict: dict[str, Any]) -> VerifiedClaim:
        if not isinstance(vc_dict, dict):
            raise TypeError("VerifiedClaim item must be a dictionary.")
        supp = [_deserialize_claim_evidence(ce) for ce in vc_dict.get("supporting_evidence", []) if isinstance(ce, dict)]
        ref = [_deserialize_claim_evidence(ce) for ce in vc_dict.get("refuting_evidence", []) if isinstance(ce, dict)]
        c_ids = [str(cid) for cid in vc_dict.get("contradiction_ids", []) if isinstance(cid, str)]
        return VerifiedClaim(
            claim_id=str(vc_dict.get("claim_id", "")),
            statement=str(vc_dict.get("statement", "")),
            verification_status=ClaimVerificationStatus(vc_dict.get("verification_status", "unsupported")),
            confidence_score=float(vc_dict.get("confidence_score", 0.0)),
            supporting_evidence=tuple(supp),
            refuting_evidence=tuple(ref),
            contradiction_ids=tuple(c_ids),
            reasoning=str(vc_dict.get("reasoning", "")),
            metadata=dict(vc_dict.get("metadata", {})),
        )

    def _deserialize_coverage(cov_dict: dict[str, Any]) -> ResearchCoverage:
        if not isinstance(cov_dict, dict):
            raise TypeError("ResearchCoverage item must be a dictionary.")
        sub_cov_list = []
        for sqc_dict in cov_dict.get("sub_question_coverages", []):
            if isinstance(sqc_dict, dict):
                sub_cov_list.append(
                    SubQuestionCoverage(
                        sub_question_id=str(sqc_dict.get("sub_question_id", "")),
                        query=str(sqc_dict.get("query", "")),
                        evidence_count=int(sqc_dict.get("evidence_count", 0)),
                        source_count=int(sqc_dict.get("source_count", 0)),
                        has_conflict=bool(sqc_dict.get("has_conflict", False)),
                        is_resolved=bool(sqc_dict.get("is_resolved", False)),
                        status=str(sqc_dict.get("status", "unresolved")),
                        distinct_domains=tuple(str(d) for d in sqc_dict.get("distinct_domains", [])),
                        metadata=dict(sqc_dict.get("metadata", {})),
                    )
                )
        return ResearchCoverage(
            overall_score=float(cov_dict.get("overall_score", 0.0)),
            coverage_ratio=float(cov_dict.get("coverage_ratio", 0.0)),
            is_sufficient=bool(cov_dict.get("is_sufficient", False)),
            total_sub_questions=int(cov_dict.get("total_sub_questions", len(sub_cov_list))),
            covered_sub_questions=int(cov_dict.get("covered_sub_questions", 0)),
            unresolved_sub_questions=tuple(str(u) for u in cov_dict.get("unresolved_sub_questions", [])),
            sub_question_coverages=tuple(sub_cov_list),
            source_diversity_score=float(cov_dict.get("source_diversity_score", 0.0)),
            distinct_domains=tuple(str(d) for d in cov_dict.get("distinct_domains", [])),
            explanation=str(cov_dict.get("explanation", "")),
            metadata=dict(cov_dict.get("metadata", {})),
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

    claims = tuple(_deserialize_claim(cl) for cl in data.get("claims", []) if isinstance(cl, dict))
    contradictions = tuple(_deserialize_conflict(ct) for ct in data.get("contradictions", []) if isinstance(ct, dict))
    verified_claims = tuple(_deserialize_verified_claim(vc) for vc in data.get("verified_claims", []) if isinstance(vc, dict))
    coverage = _deserialize_coverage(data["coverage"]) if isinstance(data.get("coverage"), dict) else None

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
        claims=claims,
        contradictions=contradictions,
        verified_claims=verified_claims,
        coverage=coverage,
        metadata=dict(data.get("metadata", {})),
    )
