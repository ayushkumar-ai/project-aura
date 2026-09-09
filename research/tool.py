import json
from typing import Any

from interfaces.tool import ToolInterface
from research.service import ResearchService


class WebSearchTool(ToolInterface):
    """Tool enabling AURA skills and agents to perform controlled web research with iterative deep research intelligence."""

    def __init__(self, service: ResearchService):
        if not isinstance(service, ResearchService):
            raise TypeError("service must be an instance of ResearchService.")
        self.service = service

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the web for up-to-date information, research sources, structured evidence, and verified findings."

    @property
    def keywords(self) -> tuple[str, ...]:
        return ("search", "web", "research", "lookup", "find", "browser", "crawler", "deep_research", "iterative", "verify")

    def execute(self, input_data: str) -> str:
        if not isinstance(input_data, str) or not input_data.strip():
            raise ValueError("input_data must be a non-empty string.")

        query = input_data.strip()
        max_sources = None
        fetch_content = True
        use_dynamic = False
        multi_hop = False
        max_hops = 2
        max_pages = 6
        deep_research = False
        decompose = False
        max_sub_questions = 3
        max_age_days = None
        iterative = False
        max_research_rounds = 2
        max_queries_total = 6
        min_coverage_ratio = 0.7

        # Check if input is structured JSON
        if query.startswith("{") and query.endswith("}"):
            try:
                parsed = json.loads(query)
                if isinstance(parsed, dict) and "query" in parsed:
                    query = str(parsed["query"]).strip()
                    max_sources = parsed.get("max_sources") or parsed.get("max_results")
                    if max_sources is not None:
                        max_sources = int(max_sources)
                    if "fetch" in parsed:
                        fetch_content = bool(parsed["fetch"])
                    if "dynamic" in parsed:
                        use_dynamic = bool(parsed["dynamic"])
                    elif "use_browser" in parsed:
                        use_dynamic = bool(parsed["use_browser"])
                    if "multi_hop" in parsed:
                        multi_hop = bool(parsed["multi_hop"])
                    if "max_hops" in parsed:
                        max_hops = int(parsed["max_hops"])
                    if "max_pages" in parsed:
                        max_pages = int(parsed["max_pages"])
                    if "deep_research" in parsed:
                        deep_research = bool(parsed["deep_research"])
                    if "decompose" in parsed:
                        decompose = bool(parsed["decompose"])
                    if "max_sub_questions" in parsed:
                        max_sub_questions = int(parsed["max_sub_questions"])
                    if "max_age_days" in parsed:
                        max_age_days = int(parsed["max_age_days"])
                    if "iterative" in parsed:
                        iterative = bool(parsed["iterative"])
                    if "max_research_rounds" in parsed:
                        max_research_rounds = int(parsed["max_research_rounds"])
                    if "max_queries_total" in parsed:
                        max_queries_total = int(parsed["max_queries_total"])
                    if "min_coverage_ratio" in parsed:
                        min_coverage_ratio = float(parsed["min_coverage_ratio"])
                    elif "min_coverage" in parsed:
                        min_coverage_ratio = float(parsed["min_coverage"])
            except Exception:
                pass  # Fall back to treating as raw query string

        report = self.service.research(
            query=query,
            max_sources=max_sources,
            fetch_content=fetch_content,
            use_dynamic=use_dynamic,
            multi_hop=multi_hop,
            max_hops=max_hops,
            max_pages=max_pages,
            deep_research=deep_research,
            decompose=decompose,
            max_sub_questions=max_sub_questions,
            max_age_days=max_age_days,
            iterative=iterative,
            max_research_rounds=max_research_rounds,
            max_queries_total=max_queries_total,
            min_coverage_ratio=min_coverage_ratio,
        )

        sources_data = []
        for src in report.sources:
            sources_data.append({
                "title": src.title,
                "url": src.url,
                "snippet": src.snippet,
                "content": src.content,
                "domain": src.source_domain,
                "rank_score": src.rank_score,
                "quality_score": src.quality_score,
                "freshness_score": src.freshness_score,
                "published_at": src.published_at,
                "hop": src.hop,
                "parent_url": src.parent_url,
            })

        failed_data = []
        for fsrc in report.failed_sources:
            failed_data.append({
                "title": fsrc.title,
                "url": fsrc.url,
                "error": fsrc.error,
                "hop": fsrc.hop,
            })

        evidence_data = []
        for ev in report.evidence:
            evidence_data.append({
                "source_url": ev.source_url,
                "source_title": ev.source_title,
                "source_domain": ev.source_domain,
                "content": ev.content,
                "relevance_score": ev.relevance_score,
                "metadata": ev.metadata,
            })

        contradictions_data = []
        for ct in report.contradictions:
            contradictions_data.append({
                "claim": ct.claim,
                "source_a_url": ct.source_a_url,
                "source_a_evidence": ct.source_a_evidence,
                "source_b_url": ct.source_b_url,
                "source_b_evidence": ct.source_b_evidence,
                "conflict_type": ct.conflict_type,
            })

        links_data = []
        for dl in report.discovered_links:
            links_data.append({
                "source_url": dl.source_url,
                "target_url": dl.target_url,
                "anchor_text": dl.anchor_text,
                "hop": dl.hop,
            })

        sub_q_data = []
        for sq in report.sub_questions:
            sub_q_data.append({
                "sub_question_id": sq.sub_question_id,
                "query": sq.query,
                "rationale": sq.rationale,
            })

        claims_data = []
        for cl in report.claims:
            claims_data.append({
                "claim_id": cl.claim_id,
                "statement": cl.statement,
                "consensus_status": cl.consensus_status,
                "confidence_score": cl.confidence_score,
                "sources_count": cl.total_sources_count,
            })

        verified_claims_data = []
        for vc in report.verified_claims:
            verified_claims_data.append({
                "claim_id": vc.claim_id,
                "statement": vc.statement,
                "verification_status": vc.verification_status.value,
                "confidence_score": vc.confidence_score,
                "reasoning": vc.reasoning,
                "supporting_evidence_count": len(vc.supporting_evidence),
                "refuting_evidence_count": len(vc.refuting_evidence),
                "contradiction_ids": list(vc.contradiction_ids),
            })

        assembled_answer_data = None
        if report.assembled_answer is not None:
            assembled_answer_data = {
                "summary": report.assembled_answer.summary,
                "formatted_answer": report.assembled_answer.formatted_answer,
                "is_grounded": report.assembled_answer.is_grounded,
                "confidence_score": report.assembled_answer.confidence_score,
                "unsupported_claims_flagged": list(report.assembled_answer.unsupported_claims_flagged),
                "conflicts_flagged": list(report.assembled_answer.conflicts_flagged),
                "citations": [
                    {
                        "citation_index": c.citation_index,
                        "source_url": c.source_url,
                        "source_title": c.source_title,
                        "domain": c.domain,
                        "hop": c.hop,
                    }
                    for c in report.assembled_answer.citations
                ],
                "sections": [
                    {
                        "title": sec.title,
                        "content": sec.content,
                        "section_type": sec.section_type,
                        "citations": list(sec.citations),
                        "claim_ids": list(sec.claim_ids),
                    }
                    for sec in report.assembled_answer.sections
                ],
            }

        confidence_data = None
        if report.confidence is not None:
            confidence_data = {
                "overall_score": report.confidence.overall_score,
                "coverage_ratio": report.confidence.coverage_ratio,
                "source_diversity_score": report.confidence.source_diversity_score,
                "evidence_density": report.confidence.evidence_density,
                "contradiction_penalty": report.confidence.contradiction_penalty,
            }

        coverage_data = None
        if report.coverage is not None:
            coverage_data = {
                "overall_score": report.coverage.overall_score,
                "coverage_ratio": report.coverage.coverage_ratio,
                "is_sufficient": report.coverage.is_sufficient,
                "total_sub_questions": report.coverage.total_sub_questions,
                "covered_sub_questions": report.coverage.covered_sub_questions,
                "unresolved_sub_questions": list(report.coverage.unresolved_sub_questions),
                "explanation": report.coverage.explanation,
            }

        return json.dumps({
            "query": report.query,
            "sources": sources_data,
            "failed_sources": failed_data,
            "evidence": evidence_data,
            "contradictions": contradictions_data,
            "discovered_links": links_data,
            "sub_questions": sub_q_data,
            "claims": claims_data,
            "verified_claims": verified_claims_data,
            "assembled_answer": assembled_answer_data,
            "confidence": confidence_data,
            "coverage": coverage_data,
            "traversal_stats": report.traversal_stats,
            "total_sources": len(sources_data),
        })
