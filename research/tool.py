import json
from typing import Any

from interfaces.tool import ToolInterface
from research.service import ResearchService


class WebSearchTool(ToolInterface):
    """Tool enabling AURA skills and agents to perform controlled web research with deep research intelligence."""

    def __init__(self, service: ResearchService):
        if not isinstance(service, ResearchService):
            raise TypeError("service must be an instance of ResearchService.")
        self.service = service

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the web for up-to-date information, research sources, and structured evidence."

    @property
    def keywords(self) -> tuple[str, ...]:
        return ("search", "web", "research", "lookup", "find", "browser", "crawler", "deep_research")

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

        confidence_data = None
        if report.confidence is not None:
            confidence_data = {
                "overall_score": report.confidence.overall_score,
                "coverage_ratio": report.confidence.coverage_ratio,
                "source_diversity_score": report.confidence.source_diversity_score,
                "evidence_density": report.confidence.evidence_density,
                "contradiction_penalty": report.confidence.contradiction_penalty,
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
            "confidence": confidence_data,
            "traversal_stats": report.traversal_stats,
            "total_sources": len(sources_data),
        })
