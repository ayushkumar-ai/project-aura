import json
from typing import Any

from interfaces.tool import ToolInterface
from research.service import ResearchService


class WebSearchTool(ToolInterface):
    """Tool enabling AURA skills and agents to perform controlled web research."""

    def __init__(self, service: ResearchService):
        if not isinstance(service, ResearchService):
            raise TypeError("service must be an instance of ResearchService.")
        self.service = service

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the web for up-to-date information and research sources."

    @property
    def keywords(self) -> tuple[str, ...]:
        return ("search", "web", "research", "lookup", "find")

    def execute(self, input_data: str) -> str:
        if not isinstance(input_data, str) or not input_data.strip():
            raise ValueError("input_data must be a non-empty string.")

        query = input_data.strip()
        max_sources = None
        fetch_content = True

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
            except Exception:
                pass  # Fall back to treating as raw query string

        report = self.service.research(
            query=query,
            max_sources=max_sources,
            fetch_content=fetch_content,
        )

        sources_data = []
        for src in report.sources:
            sources_data.append({
                "title": src.title,
                "url": src.url,
                "snippet": src.snippet,
                "content": src.content,
                "domain": src.source_domain,
            })

        failed_data = []
        for fsrc in report.failed_sources:
            failed_data.append({
                "title": fsrc.title,
                "url": fsrc.url,
                "error": fsrc.error,
            })

        return json.dumps({
            "query": report.query,
            "sources": sources_data,
            "failed_sources": failed_data,
            "total_sources": len(sources_data),
        })
