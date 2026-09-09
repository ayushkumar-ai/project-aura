import logging
import re
from typing import Sequence

from research.contradictions import detect_contradictions
from research.models import ClaimEvidence, EvidenceConflict, EvidenceItem, ResearchClaim, ResearchSource

logger = logging.getLogger("aura.research.claims")


def extract_claims_from_evidence(
    evidence_items: Sequence[EvidenceItem],
    max_claims: int = 6,
) -> tuple[ResearchClaim, ...]:
    """Deterministically extract structured factual claims from a collection of EvidenceItems."""
    if not isinstance(evidence_items, (list, tuple)):
        raise TypeError("evidence_items must be a list or tuple of EvidenceItem instances.")

    if not evidence_items or max_claims <= 0:
        return ()

    # Group passages by subject keywords to consolidate claims across sources
    claims_list: list[ResearchClaim] = []
    seen_statements: set[str] = set()

    for idx, ev in enumerate(evidence_items):
        stmt = ev.content.strip()
        # Clean statement up to first two sentences
        sentences = re.split(r"(?<=[.!?])\s+", stmt)
        clean_statement = " ".join(sentences[:2]).strip()
        if not clean_statement or clean_statement.lower() in seen_statements:
            continue
        seen_statements.add(clean_statement.lower())

        supporting = [
            ClaimEvidence(
                source_url=ev.source_url,
                source_title=ev.source_title,
                passage=ev.content,
                stance="supports",
                confidence=min(round(ev.relevance_score / 3.0, 2), 1.0) if ev.relevance_score > 0 else 0.8,
            )
        ]

        claim = ResearchClaim(
            claim_id=f"claim_{len(claims_list)+1}",
            statement=clean_statement,
            supporting_sources=tuple(supporting),
            refuting_sources=(),
            consensus_status="supported",
            confidence_score=supporting[0].confidence,
        )
        claims_list.append(claim)
        if len(claims_list) >= max_claims:
            break

    return tuple(claims_list)


def aggregate_claims_with_contradictions(
    claims: Sequence[ResearchClaim],
    contradictions: Sequence[EvidenceConflict],
) -> tuple[ResearchClaim, ...]:
    """Integrate detected cross-source contradictions into claim consensus statuses."""
    if not isinstance(claims, (list, tuple)):
        raise TypeError("claims must be a list or tuple of ResearchClaim instances.")

    if not contradictions:
        return tuple(claims)

    updated_claims: list[ResearchClaim] = []
    conflict_urls: set[tuple[str, str]] = {
        (c.source_a_url, c.source_b_url) for c in contradictions
    } | {(c.source_b_url, c.source_a_url) for c in contradictions}

    for cl in claims:
        is_disputed = False
        refuting_evidence: list[ClaimEvidence] = list(cl.refuting_sources)

        for supp in cl.supporting_sources:
            for ct in contradictions:
                if supp.source_url == ct.source_a_url:
                    is_disputed = True
                    refuting_evidence.append(
                        ClaimEvidence(
                            source_url=ct.source_b_url,
                            source_title=f"Contradicting source for {ct.claim}",
                            passage=ct.source_b_evidence,
                            stance="refutes",
                            confidence=0.9,
                        )
                    )
                elif supp.source_url == ct.source_b_url:
                    is_disputed = True
                    refuting_evidence.append(
                        ClaimEvidence(
                            source_url=ct.source_a_url,
                            source_title=f"Contradicting source for {ct.claim}",
                            passage=ct.source_a_evidence,
                            stance="refutes",
                            confidence=0.9,
                        )
                    )

        new_status = "disputed" if is_disputed else cl.consensus_status
        new_conf = round(cl.confidence_score * 0.7, 2) if is_disputed else cl.confidence_score

        updated_claims.append(
            ResearchClaim(
                claim_id=cl.claim_id,
                statement=cl.statement,
                sub_question_id=cl.sub_question_id,
                supporting_sources=cl.supporting_sources,
                refuting_sources=tuple(refuting_evidence),
                consensus_status=new_status,
                confidence_score=new_conf,
                metadata=dict(cl.metadata),
            )
        )

    return tuple(updated_claims)
