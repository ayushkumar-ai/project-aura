from typing import Sequence
from research.models import EvidenceConflict, EvidenceItem, ResearchClaim, ResearchConfidence, ResearchSource, ResearchSubQuestion


def calculate_research_confidence(
    sources: Sequence[ResearchSource],
    sub_questions: Sequence[ResearchSubQuestion],
    evidence: Sequence[EvidenceItem],
    contradictions: Sequence[EvidenceConflict],
    claims: Sequence[ResearchClaim] | None = None,
    citation_validity: float = 1.0,
) -> ResearchConfidence:
    """Calculate deterministic confidence and coverage scores for a completed research report."""
    total_sources = len(sources)
    if total_sources == 0:
        return ResearchConfidence(
            overall_score=0.0,
            coverage_ratio=0.0,
            source_diversity_score=0.0,
            evidence_density=0.0,
            contradiction_penalty=0.0,
            citation_validity_score=citation_validity,
        )

    # 1. Coverage Ratio: sub-questions addressed
    total_sq = len(sub_questions)
    coverage_ratio = 1.0 if total_sq == 0 else min(total_sources / total_sq, 1.0)

    # 2. Source Diversity Score: unique domains / total sources
    domains = {s.source_domain for s in sources if s.source_domain}
    diversity_score = min(len(domains) / max(total_sources, 1), 1.0)

    # 3. Evidence Density: average evidence passages per source
    evidence_density = round(len(evidence) / max(total_sources, 1), 2)

    # 4. Contradiction Penalty
    contradiction_penalty = min(len(contradictions) * 0.15, 0.4)

    # 5. Overall Confidence calculation
    base_confidence = (coverage_ratio * 0.4) + (diversity_score * 0.3) + (min(evidence_density / 2.0, 1.0) * 0.3)
    final_score = base_confidence * citation_validity - contradiction_penalty
    overall_score = max(min(round(final_score, 4), 1.0), 0.0)

    return ResearchConfidence(
        overall_score=overall_score,
        coverage_ratio=round(coverage_ratio, 4),
        source_diversity_score=round(diversity_score, 4),
        evidence_density=evidence_density,
        contradiction_penalty=round(contradiction_penalty, 4),
        citation_validity_score=round(citation_validity, 4),
        metadata={
            "domains_count": len(domains),
            "sources_count": total_sources,
            "evidence_count": len(evidence),
            "contradictions_count": len(contradictions),
        },
    )
