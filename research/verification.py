import logging
import re
from collections.abc import Sequence
from typing import Any

from research.models import (
    ClaimEvidence,
    ClaimVerificationStatus,
    EvidenceConflict,
    EvidenceItem,
    ResearchClaim,
    ResearchSource,
    VerifiedClaim,
)

logger = logging.getLogger("aura.research.verification")

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
    """Extract normalized alphanumeric words from text excluding common stopwords."""
    if not text:
        return set()
    words = re.findall(r"[a-z0-9_]+", text.lower())
    return {w for w in words if len(w) > 1 and w not in _STOPWORDS}


def _extract_numbers(text: str) -> set[str]:
    """Extract numeric values or percentages from text to evaluate quantitative consistency."""
    if not text:
        return set()
    return set(re.findall(r"\d+(?:\.\d+)?%?", text))


class ClaimVerifier:
    """Deterministically verifies extracted factual claims against source evidence and detected contradictions."""

    def __init__(
        self,
        min_support_confidence: float = 0.5,
        min_multi_source_threshold: int = 2,
        max_evidence_per_claim: int = 5,
    ):
        if not isinstance(min_support_confidence, (int, float)) or not (0.0 <= min_support_confidence <= 1.0):
            raise ValueError("min_support_confidence must be a float between 0.0 and 1.0.")
        if not isinstance(min_multi_source_threshold, int) or min_multi_source_threshold <= 0:
            raise ValueError("min_multi_source_threshold must be a positive integer.")
        if not isinstance(max_evidence_per_claim, int) or max_evidence_per_claim <= 0:
            raise ValueError("max_evidence_per_claim must be a positive integer.")

        self.min_support_confidence = float(min_support_confidence)
        self.min_multi_source_threshold = min_multi_source_threshold
        self.max_evidence_per_claim = max_evidence_per_claim

    def match_evidence_to_claim(
        self,
        claim_statement: str,
        evidence_items: Sequence[EvidenceItem],
    ) -> list[tuple[EvidenceItem, float]]:
        """Compute keyword, numeric, and semantic overlap between a claim statement and evidence passages."""
        if not claim_statement or not evidence_items:
            return []

        claim_tokens = _extract_tokens(claim_statement)
        claim_nums = _extract_numbers(claim_statement)
        if not claim_tokens:
            return []

        matched: list[tuple[EvidenceItem, float]] = []

        for ev in evidence_items:
            ev_tokens = _extract_tokens(ev.content)
            if not ev_tokens:
                continue

            common_tokens = claim_tokens.intersection(ev_tokens)
            overlap_ratio = len(common_tokens) / len(claim_tokens)

            # Check numeric alignment if numbers are present in the claim
            num_bonus = 0.0
            if claim_nums:
                ev_nums = _extract_numbers(ev.content)
                common_nums = claim_nums.intersection(ev_nums)
                if common_nums:
                    num_bonus = 0.25 * (len(common_nums) / len(claim_nums))
                else:
                    # Numbers in claim but missing or conflicting in passage
                    num_bonus = -0.15

            score = min(max(overlap_ratio + num_bonus, 0.0), 1.0)
            if score >= 0.2:  # Overlap threshold
                matched.append((ev, round(score, 4)))

        matched.sort(key=lambda x: x[1], reverse=True)
        return matched

    def verify_claim(
        self,
        claim: ResearchClaim,
        evidence: Sequence[EvidenceItem],
        contradictions: Sequence[EvidenceConflict],
        sources: Sequence[ResearchSource],
    ) -> VerifiedClaim:
        """Verify a single claim, determining its authoritative status and evidence grounding."""
        if not isinstance(claim, ResearchClaim):
            raise TypeError("claim must be an instance of ResearchClaim.")

        stmt = claim.statement.strip()
        if not stmt:
            return VerifiedClaim(
                claim_id=claim.claim_id,
                statement=stmt,
                verification_status=ClaimVerificationStatus.UNSUPPORTED,
                confidence_score=0.0,
                reasoning="Claim statement is empty.",
            )

        # 1. Match claim against evidence pool and pre-associated claim evidence
        matched_items = self.match_evidence_to_claim(stmt, evidence)

        # Merge pre-associated supporting evidence
        supporting_map: dict[str, ClaimEvidence] = {}
        for ev in claim.supporting_sources:
            supporting_map[ev.source_url + ":" + ev.passage[:50]] = ev

        for itm, score in matched_items[:self.max_evidence_per_claim]:
            key = itm.source_url + ":" + itm.content[:50]
            if key not in supporting_map:
                supporting_map[key] = ClaimEvidence(
                    source_url=itm.source_url,
                    source_title=itm.source_title,
                    passage=itm.content,
                    stance="supports",
                    confidence=min(round(score * 1.2, 2), 1.0),
                )

        supporting_list = list(supporting_map.values())[:self.max_evidence_per_claim]

        # 2. Collect refuting / contradicting evidence
        refuting_list: list[ClaimEvidence] = list(claim.refuting_sources)
        contradiction_ids: list[str] = []

        claim_tokens = _extract_tokens(stmt)
        claim_urls = {ev.source_url for ev in supporting_list}

        for ct in contradictions:
            conflict_tokens = _extract_tokens(ct.claim)
            overlap = len(claim_tokens.intersection(conflict_tokens))
            url_match = (ct.source_a_url in claim_urls) or (ct.source_b_url in claim_urls)

            if overlap >= 2 or url_match:
                cid = f"conflict_{ct.source_a_url}_{ct.source_b_url}"
                if cid not in contradiction_ids:
                    contradiction_ids.append(cid)

                # Add opposing evidence
                if ct.source_a_url in claim_urls and ct.source_b_url not in claim_urls:
                    refuting_list.append(
                        ClaimEvidence(
                            source_url=ct.source_b_url,
                            source_title=f"Opposing source for {ct.claim}",
                            passage=ct.source_b_evidence,
                            stance="refutes",
                            confidence=0.85,
                        )
                    )
                elif ct.source_b_url in claim_urls and ct.source_a_url not in claim_urls:
                    refuting_list.append(
                        ClaimEvidence(
                            source_url=ct.source_a_url,
                            source_title=f"Opposing source for {ct.claim}",
                            passage=ct.source_a_evidence,
                            stance="refutes",
                            confidence=0.85,
                        )
                    )

        # 3. Determine authoritative verification status
        distinct_supporting_domains = {
            s.source_domain for s in sources if s.url in {ev.source_url for ev in supporting_list} and s.source_domain
        }
        if not distinct_supporting_domains:
            # Fallback domain extraction
            distinct_supporting_domains = {
                ev.source_url.split("//")[-1].split("/")[0] for ev in supporting_list if "//" in ev.source_url
            }

        num_supporting = len(supporting_list)
        num_refuting = len(refuting_list)
        has_contradictions = len(contradiction_ids) > 0 or num_refuting > 0

        status: ClaimVerificationStatus
        confidence: float
        reasoning: str

        if num_supporting == 0 and num_refuting == 0:
            status = ClaimVerificationStatus.UNSUPPORTED
            confidence = 0.0
            reasoning = "No supporting or corroborating evidence passages found in retrieved sources."

        elif has_contradictions:
            if num_supporting > 0 and num_refuting > 0:
                # Both supporting and refuting evidence present
                if num_refuting >= num_supporting:
                    status = ClaimVerificationStatus.CONTRADICTED
                    confidence = round(max(0.15, min(0.35, 1.0 - (num_refuting / (num_supporting + num_refuting)))), 2)
                    reasoning = f"Directly contradicted by {num_refuting} opposing evidence passage(s) across sources."
                else:
                    status = ClaimVerificationStatus.UNCERTAIN
                    confidence = 0.40
                    reasoning = f"Conflicting evidence: {num_supporting} supporting vs {num_refuting} refuting source(s)."
            elif num_refuting > 0:
                status = ClaimVerificationStatus.CONTRADICTED
                confidence = 0.10
                reasoning = f"Refuted by {num_refuting} conflicting evidence passage(s)."
            else:
                status = ClaimVerificationStatus.UNCERTAIN
                confidence = 0.45
                reasoning = "Unresolved data conflicts detected for this claim."

        elif len(distinct_supporting_domains) >= self.min_multi_source_threshold:
            status = ClaimVerificationStatus.SUPPORTED
            base_score = 0.85 + min(0.15, 0.05 * (len(distinct_supporting_domains) - self.min_multi_source_threshold))
            confidence = round(min(base_score, 1.0), 2)
            reasoning = f"Corroborated by {len(distinct_supporting_domains)} independent source domains."

        elif num_supporting >= 1:
            status = ClaimVerificationStatus.PARTIALLY_SUPPORTED
            confidence = round(min(0.70, 0.50 + 0.10 * num_supporting), 2)
            reasoning = f"Partially supported by {num_supporting} passage(s) from single domain/source."

        else:
            status = ClaimVerificationStatus.UNSUPPORTED
            confidence = 0.0
            reasoning = "Insufficient evidence to corroborate claim."

        return VerifiedClaim(
            claim_id=claim.claim_id,
            statement=stmt,
            verification_status=status,
            confidence_score=confidence,
            supporting_evidence=tuple(supporting_list),
            refuting_evidence=tuple(refuting_list),
            contradiction_ids=tuple(contradiction_ids),
            reasoning=reasoning,
            metadata={
                "sub_question_id": claim.sub_question_id,
                "supporting_count": num_supporting,
                "refuting_count": num_refuting,
                "distinct_domains": sorted(list(distinct_supporting_domains)),
            },
        )

    def verify_claims(
        self,
        claims: Sequence[ResearchClaim],
        evidence: Sequence[EvidenceItem],
        contradictions: Sequence[EvidenceConflict],
        sources: Sequence[ResearchSource],
        max_claims: int = 10,
    ) -> tuple[VerifiedClaim, ...]:
        """Verify all extracted claims deterministically, enforcing bounded processing limits."""
        if not isinstance(claims, (list, tuple)):
            raise TypeError("claims must be a list or tuple of ResearchClaim instances.")

        if not claims or max_claims <= 0:
            return ()

        verified: list[VerifiedClaim] = []
        for cl in claims[:max_claims]:
            vc = self.verify_claim(
                claim=cl,
                evidence=evidence,
                contradictions=contradictions,
                sources=sources,
            )
            verified.append(vc)

        return tuple(verified)


def verify_claims(
    claims: Sequence[ResearchClaim],
    evidence: Sequence[EvidenceItem],
    contradictions: Sequence[EvidenceConflict],
    sources: Sequence[ResearchSource],
    max_claims: int = 10,
) -> tuple[VerifiedClaim, ...]:
    """Convenience functional wrapper for ClaimVerifier.verify_claims."""
    verifier = ClaimVerifier()
    return verifier.verify_claims(
        claims=claims,
        evidence=evidence,
        contradictions=contradictions,
        sources=sources,
        max_claims=max_claims,
    )
