import logging
import re
from collections.abc import Sequence
from typing import Any

from research.models import (
    EvidenceConflict,
    EvidenceItem,
    ResearchCoverage,
    ResearchSource,
    ResearchSubQuestion,
    SubQuestionCoverage,
)

logger = logging.getLogger("aura.research.coverage")

_STOPWORDS = frozenset({
    "a", "an", "the", "in", "on", "of", "to", "for", "with", "by", "at", "from",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "and", "or", "but", "if", "then", "else", "when",
    "this", "that", "these", "those", "it", "its", "as", "what", "which", "who",
    "how", "all", "any", "both", "each", "more", "most", "other", "some", "such",
    "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very", "can",
    "will", "just", "should", "now",
})


def _extract_tokens(text: str) -> set[str]:
    """Extract normalized alphanumeric words excluding common stopwords."""
    if not text:
        return set()
    words = re.findall(r"[a-z0-9_]+", text.lower())
    return {w for w in words if len(w) > 1 and w not in _STOPWORDS}


def evaluate_research_coverage(
    query: str,
    sub_questions: Sequence[ResearchSubQuestion],
    evidence: Sequence[EvidenceItem],
    sources: Sequence[ResearchSource],
    contradictions: Sequence[EvidenceConflict] = (),
    min_sources_per_question: int = 1,
    min_coverage_ratio: float = 0.7,
) -> ResearchCoverage:
    """Deterministically evaluate whether collected research evidence sufficiently covers decomposed sub-questions."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Query must be a non-empty string.")

    q_clean = query.strip()
    effective_sub_questions = list(sub_questions) if sub_questions else [
        ResearchSubQuestion(
            sub_question_id="sub_q_1",
            query=q_clean,
            rationale="Primary query focus",
        )
    ]

    sub_coverages: list[SubQuestionCoverage] = []
    unresolved_list: list[str] = []

    # Map sources by URL for fast lookup
    source_map: dict[str, ResearchSource] = {s.url: s for s in sources if s.status == "success"}

    for sq in effective_sub_questions:
        sq_tokens = _extract_tokens(sq.query)

        # 1. Match evidence
        matched_ev: list[EvidenceItem] = []
        matched_source_urls: set[str] = set()

        for ev in evidence:
            # Check explicit metadata linkage first
            explicit_sq_id = ev.metadata.get("sub_question_id") if isinstance(ev.metadata, dict) else None
            if explicit_sq_id and explicit_sq_id == sq.sub_question_id:
                matched_ev.append(ev)
                matched_source_urls.add(ev.source_url)
                continue

            # Check lexical/semantic overlap
            ev_tokens = _extract_tokens(ev.content)
            if sq_tokens and ev_tokens:
                overlap = len(sq_tokens.intersection(ev_tokens))
                if overlap >= max(1, len(sq_tokens) * 0.3):
                    matched_ev.append(ev)
                    matched_source_urls.add(ev.source_url)

        # 2. Match sources directly if not already found in evidence
        for src in sources:
            if src.status != "success":
                continue
            if src.url in matched_source_urls:
                continue
            src_tokens = _extract_tokens(src.title + " " + src.snippet)
            if sq_tokens and src_tokens:
                overlap = len(sq_tokens.intersection(src_tokens))
                if overlap >= max(1, len(sq_tokens) * 0.4):
                    matched_source_urls.add(src.url)

        # 3. Check contradictions relating to this sub-question
        has_conflict = False
        for ct in contradictions:
            conflict_tokens = _extract_tokens(ct.claim)
            if sq_tokens and conflict_tokens:
                overlap = len(sq_tokens.intersection(conflict_tokens))
                if overlap >= 2 or (ct.source_a_url in matched_source_urls or ct.source_b_url in matched_source_urls):
                    has_conflict = True
                    break

        # 4. Determine domain diversity & status
        distinct_domains = sorted(
            list(
                {
                    source_map[u].source_domain
                    for u in matched_source_urls
                    if u in source_map and source_map[u].source_domain
                }
            )
        )

        ev_count = len(matched_ev)
        src_count = len(matched_source_urls)

        status: str
        is_resolved: bool

        if ev_count == 0 and src_count == 0:
            status = "unresolved"
            is_resolved = False
            unresolved_list.append(sq.query)
        elif has_conflict:
            status = "conflicted"
            # If there's multiple sources supporting one side and resolved, is_resolved could be conditional,
            # but conflicting evidence requires flagging
            is_resolved = ev_count >= 2 and src_count >= max(2, min_sources_per_question)
            if not is_resolved:
                unresolved_list.append(sq.query)
        elif ev_count >= 1 and src_count >= min_sources_per_question:
            status = "covered"
            is_resolved = True
        else:
            status = "insufficient"
            is_resolved = False
            unresolved_list.append(sq.query)

        sub_coverages.append(
            SubQuestionCoverage(
                sub_question_id=sq.sub_question_id,
                query=sq.query,
                evidence_count=ev_count,
                source_count=src_count,
                has_conflict=has_conflict,
                is_resolved=is_resolved,
                status=status,
                distinct_domains=tuple(distinct_domains),
            )
        )

    # Calculate overall aggregate metrics
    total_sub_q = len(sub_coverages)
    covered_count = sum(1 for sc in sub_coverages if sc.is_resolved)
    coverage_ratio = round(covered_count / total_sub_q, 4) if total_sub_q > 0 else 1.0

    all_domains = sorted(list({s.source_domain for s in sources if s.status == "success" and s.source_domain}))
    source_diversity = round(min(1.0, len(all_domains) / max(1, len(sources))), 4) if sources else 0.0

    has_any_conflict = any(sc.has_conflict for sc in sub_coverages)
    has_any_sources = len(sources) > 0

    is_sufficient = bool(
        coverage_ratio >= min_coverage_ratio
        and has_any_sources
        and (covered_count > 0 or total_sub_q == 0)
    )

    conflict_penalty = 0.15 if has_any_conflict else 0.0
    evidence_density_score = min(1.0, len(evidence) / max(1, total_sub_q * 2))

    base_score = 0.55 * coverage_ratio + 0.30 * source_diversity + 0.15 * evidence_density_score - conflict_penalty
    overall_score = round(min(1.0, max(0.0, base_score)), 2)

    # Construct explainable report
    if is_sufficient:
        explanation = (
            f"Sufficient coverage ({coverage_ratio * 100:.0f}%): {covered_count}/{total_sub_q} sub-questions resolved "
            f"across {len(all_domains)} distinct domain(s)."
        )
    else:
        unresolved_str = ", ".join(f"'{u}'" for u in unresolved_list[:3])
        explanation = (
            f"Insufficient coverage ({coverage_ratio * 100:.0f}%): {total_sub_q - covered_count}/{total_sub_q} sub-questions unresolved. "
            f"Missing evidence for: {unresolved_str}."
        )

    return ResearchCoverage(
        overall_score=overall_score,
        coverage_ratio=coverage_ratio,
        is_sufficient=is_sufficient,
        total_sub_questions=total_sub_q,
        covered_sub_questions=covered_count,
        unresolved_sub_questions=tuple(unresolved_list),
        sub_question_coverages=tuple(sub_coverages),
        source_diversity_score=source_diversity,
        distinct_domains=tuple(all_domains),
        explanation=explanation,
        metadata={
            "min_coverage_ratio": min_coverage_ratio,
            "min_sources_per_question": min_sources_per_question,
            "total_sources": len(sources),
            "total_evidence": len(evidence),
            "total_contradictions": len(contradictions),
        },
    )


class ResearchCoverageEvaluator:
    """Evaluates coverage, completeness, and unresolved inquiries for research workflows."""

    def __init__(
        self,
        min_sources_per_question: int = 1,
        min_coverage_ratio: float = 0.7,
    ):
        if not isinstance(min_sources_per_question, int) or min_sources_per_question <= 0:
            raise ValueError("min_sources_per_question must be a positive integer.")
        if not isinstance(min_coverage_ratio, (int, float)) or not (0.0 <= min_coverage_ratio <= 1.0):
            raise ValueError("min_coverage_ratio must be a float between 0.0 and 1.0.")

        self.min_sources_per_question = min_sources_per_question
        self.min_coverage_ratio = float(min_coverage_ratio)

    def evaluate(
        self,
        query: str,
        sub_questions: Sequence[ResearchSubQuestion],
        evidence: Sequence[EvidenceItem],
        sources: Sequence[ResearchSource],
        contradictions: Sequence[EvidenceConflict] = (),
    ) -> ResearchCoverage:
        """Evaluate coverage deterministically across all provided research assets."""
        return evaluate_research_coverage(
            query=query,
            sub_questions=sub_questions,
            evidence=evidence,
            sources=sources,
            contradictions=contradictions,
            min_sources_per_question=self.min_sources_per_question,
            min_coverage_ratio=self.min_coverage_ratio,
        )
