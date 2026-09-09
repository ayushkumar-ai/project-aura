import logging
import re
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

from core.models import AURAResponse
from interfaces.model import ModelInterface
from research.citations import extract_citations, validate_citations
from research.models import (
    AnswerCitation,
    AnswerSection,
    AssembledAnswer,
    ClaimEvidence,
    ClaimVerificationStatus,
    EvidenceConflict,
    EvidenceItem,
    ResearchCoverage,
    ResearchSource,
    VerifiedClaim,
)

logger = logging.getLogger("aura.research.assembly")


def build_answer_citations(
    sources: Sequence[ResearchSource],
    evidence: Sequence[EvidenceItem] | None = None,
    max_citations: int = 20,
) -> tuple[AnswerCitation, ...]:
    """Deterministically map ResearchSource records into AnswerCitation instances."""
    if not isinstance(sources, (list, tuple)):
        raise TypeError("sources must be a list or tuple of ResearchSource instances.")

    evidence_by_url: dict[str, str] = {}
    if evidence:
        for ev in evidence:
            if ev.source_url not in evidence_by_url:
                evidence_by_url[ev.source_url] = ev.content

    citations: list[AnswerCitation] = []
    for idx, src in enumerate(sources[:max_citations], 1):
        passage = evidence_by_url.get(src.url) or (src.snippet if src.snippet else "")
        citations.append(
            AnswerCitation(
                citation_index=idx,
                source_url=src.url,
                source_title=src.title,
                evidence_passage=passage[:300] if passage else "",
                domain=src.source_domain,
                hop=src.hop,
            )
        )

    return tuple(citations)


class AnswerAssembler:
    """Assembles verified research findings into structured, citation-grounded answers with coverage integration."""

    def __init__(
        self,
        max_citations: int = 20,
        max_answer_chars: int = 8000,
        require_grounding: bool = True,
    ):
        if not isinstance(max_citations, int) or max_citations <= 0:
            raise ValueError("max_citations must be a positive integer.")
        if not isinstance(max_answer_chars, int) or max_answer_chars <= 0:
            raise ValueError("max_answer_chars must be a positive integer.")

        self.max_citations = max_citations
        self.max_answer_chars = max_answer_chars
        self.require_grounding = bool(require_grounding)

    def _format_source_references(self, citations: Sequence[AnswerCitation]) -> str:
        """Format 1-based bracketed source references."""
        if not citations:
            return "No sources available."
        lines = []
        for c in citations:
            hop_str = f" [Hop {c.hop}]" if c.hop > 0 else ""
            lines.append(f"[{c.citation_index}] {c.source_title}{hop_str} - {c.source_url}")
        return "\n".join(lines)

    def _resolve_claim_citations(
        self,
        claim: VerifiedClaim,
        url_to_index: dict[str, int],
    ) -> tuple[int, ...]:
        """Resolve valid 1-based source indices for a verified claim's supporting evidence."""
        indexes: set[int] = set()
        for ev in claim.supporting_evidence:
            if ev.source_url in url_to_index:
                indexes.add(url_to_index[ev.source_url])
        return tuple(sorted(indexes))

    def assemble(
        self,
        query: str,
        verified_claims: Sequence[VerifiedClaim],
        sources: Sequence[ResearchSource],
        contradictions: Sequence[EvidenceConflict] = (),
        evidence: Sequence[EvidenceItem] = (),
        coverage: ResearchCoverage | None = None,
        model: ModelInterface | None = None,
        request_id: UUID | None = None,
    ) -> AssembledAnswer:
        """Convert verified claims, sources, coverage, and evidence into an authoritative AssembledAnswer."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Query must be a non-empty string.")

        q_clean = query.strip()
        citations = build_answer_citations(sources=sources, evidence=evidence, max_citations=self.max_citations)
        url_to_index: dict[str, int] = {c.source_url: c.citation_index for c in citations}

        # 1. Group claims by status
        supported_claims = [c for c in verified_claims if c.is_supported or c.verification_status == ClaimVerificationStatus.PARTIALLY_SUPPORTED]
        conflicted_claims = [c for c in verified_claims if c.is_contradicted or c.is_uncertain]
        unsupported_claims = [c for c in verified_claims if c.is_unsupported]

        sections: list[AnswerSection] = []

        # 2. Build Section 1: Executive Findings
        findings_lines = []
        all_findings_citations: set[int] = set()
        findings_claim_ids: list[str] = []

        for cl in supported_claims:
            c_indices = self._resolve_claim_citations(cl, url_to_index)
            all_findings_citations.update(c_indices)
            findings_claim_ids.append(cl.claim_id)

            cite_tags = "".join(f"[{idx}]" for idx in c_indices) if c_indices else ""
            status_prefix = "" if cl.is_supported else "[Partial] "
            cite_str = f" {cite_tags}" if cite_tags else ""
            findings_lines.append(f"- {status_prefix}{cl.statement}{cite_str}")

        if findings_lines:
            findings_content = "\n".join(findings_lines)
        elif not sources:
            findings_content = f"No verified information could be retrieved for query '{q_clean}'."
        else:
            findings_content = "Research completed, but no claims met full support criteria."

        sections.append(
            AnswerSection(
                title="Key Findings",
                content=findings_content,
                citations=tuple(sorted(all_findings_citations)),
                claim_ids=tuple(findings_claim_ids),
                section_type="findings",
            )
        )

        # 3. Build Section 2: Conflicts & Contradictions (if any exist)
        conflicts_flagged: list[str] = []
        conflict_citations: set[int] = set()

        if conflicted_claims or contradictions:
            conflict_lines = []
            for cl in conflicted_claims:
                conflicts_flagged.append(cl.statement)
                supp_indices = self._resolve_claim_citations(cl, url_to_index)
                ref_indices: set[int] = set()
                for rev in cl.refuting_evidence:
                    if rev.source_url in url_to_index:
                        ref_indices.add(url_to_index[rev.source_url])

                conflict_citations.update(supp_indices)
                conflict_citations.update(ref_indices)

                supp_tag = "".join(f"[{i}]" for i in supp_indices) or "Source(s)"
                ref_tag = "".join(f"[{i}]" for i in ref_indices) or "Opposing source(s)"
                conflict_lines.append(f"- Disputed Claim: {cl.statement} (Supported by {supp_tag} vs Opposed by {ref_tag})")

            for ct in contradictions:
                s_a = url_to_index.get(ct.source_a_url)
                s_b = url_to_index.get(ct.source_b_url)
                tag_a = f"[{s_a}]" if s_a else ct.source_a_url
                tag_b = f"[{s_b}]" if s_b else ct.source_b_url
                if s_a:
                    conflict_citations.add(s_a)
                if s_b:
                    conflict_citations.add(s_b)
                conflict_lines.append(f"- Evidence Conflict: {ct.claim} (Divergence between {tag_a} and {tag_b})")

            sections.append(
                AnswerSection(
                    title="Evidence Conflicts & Inconsistencies",
                    content="\n".join(conflict_lines),
                    citations=tuple(sorted(conflict_citations)),
                    claim_ids=tuple(c.claim_id for c in conflicted_claims),
                    section_type="conflict",
                )
            )

        # 4. Build Section 3: Limitations / Unsupported / Coverage Gaps (if any)
        unsupported_flagged: list[str] = []
        unsupported_lines: list[str] = []

        if unsupported_claims:
            for ucl in unsupported_claims:
                unsupported_flagged.append(ucl.statement)
                unsupported_lines.append(f"- Unverified: {ucl.statement} (Reason: {ucl.reasoning})")

        if coverage and (not coverage.is_sufficient or coverage.unresolved_sub_questions):
            for uq in coverage.unresolved_sub_questions:
                if uq not in unsupported_flagged:
                    unsupported_flagged.append(uq)
                    unsupported_lines.append(f"- Unresolved Sub-Topic: {uq} (Insufficient source coverage)")

        if unsupported_lines:
            sections.append(
                AnswerSection(
                    title="Data Limitations & Uncorroborated Inquiries",
                    content="\n".join(unsupported_lines),
                    citations=(),
                    claim_ids=tuple(u.claim_id for u in unsupported_claims),
                    section_type="limitations",
                )
            )

        # 5. Build Section 4: Sources
        ref_text = self._format_source_references(citations)
        sections.append(
            AnswerSection(
                title="References",
                content=ref_text,
                citations=tuple(c.citation_index for c in citations),
                claim_ids=(),
                section_type="sources",
            )
        )

        # 6. Format Full Answer Text
        text_blocks: list[str] = [f"### Research Report: {q_clean}"]
        for sec in sections:
            if sec.section_type == "sources":
                text_blocks.append(f"### {sec.title}\n{sec.content}")
            else:
                text_blocks.append(f"#### {sec.title}\n{sec.content}")

        formatted_answer = "\n\n".join(text_blocks)
        if len(formatted_answer) > self.max_answer_chars:
            formatted_answer = formatted_answer[:self.max_answer_chars] + "... [truncated]"

        # 7. Confidence & Grounding Evaluation
        extracted_cites = extract_citations(formatted_answer)
        num_sources = len(sources)
        invalid_cites = [c for c in extracted_cites if c < 1 or c > num_sources]
        is_grounded = len(invalid_cites) == 0 and (len(supported_claims) > 0 or not sources)

        # Aggregate confidence
        if verified_claims:
            avg_claim_conf = sum(c.confidence_score for c in verified_claims) / len(verified_claims)
        else:
            avg_claim_conf = 0.5 if sources else 0.0

        if coverage is not None:
            cov_weight = 0.3 * coverage.coverage_ratio
            claim_weight = 0.7 * avg_claim_conf
            combined_conf = cov_weight + claim_weight
        else:
            combined_conf = avg_claim_conf

        penalty = 0.2 if conflicts_flagged else 0.0
        final_conf = max(0.0, min(1.0, round(combined_conf - penalty, 4)))

        # Summary line
        summary_text = (
            f"Verified {len(supported_claims)} supported claim(s) across {len(sources)} source(s)."
            if sources
            else "No sources available to answer research question."
        )

        return AssembledAnswer(
            query=q_clean,
            summary=summary_text,
            sections=tuple(sections),
            verified_claims=tuple(verified_claims),
            citations=citations,
            unsupported_claims_flagged=tuple(unsupported_flagged),
            conflicts_flagged=tuple(conflicts_flagged),
            formatted_answer=formatted_answer,
            is_grounded=is_grounded,
            confidence_score=final_conf,
            metadata={
                "supported_count": len(supported_claims),
                "conflicts_count": len(conflicted_claims),
                "unsupported_count": len(unsupported_claims),
                "total_citations": len(citations),
                "invalid_citations_detected": invalid_cites,
                "coverage_score": coverage.overall_score if coverage else None,
                "coverage_ratio": coverage.coverage_ratio if coverage else None,
            },
        )


def assemble_answer(
    query: str,
    verified_claims: Sequence[VerifiedClaim],
    sources: Sequence[ResearchSource],
    contradictions: Sequence[EvidenceConflict] = (),
    evidence: Sequence[EvidenceItem] = (),
    coverage: ResearchCoverage | None = None,
    model: ModelInterface | None = None,
    max_answer_chars: int = 8000,
) -> AssembledAnswer:
    """Convenience wrapper for AnswerAssembler.assemble."""
    assembler = AnswerAssembler(max_answer_chars=max_answer_chars)
    return assembler.assemble(
        query=query,
        verified_claims=verified_claims,
        sources=sources,
        contradictions=contradictions,
        evidence=evidence,
        coverage=coverage,
        model=model,
    )
