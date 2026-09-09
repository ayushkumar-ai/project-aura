from research.contradictions import detect_contradictions
from research.evidence import extract_evidence_from_text, extract_source_evidence
from research.extractor import HTMLTextExtractor, extract_text_and_title_from_html
from research.interfaces import FetchProvider, SearchProvider, WebProvider
from research.models import (
    EvidenceConflict,
    EvidenceItem,
    ResearchReport,
    ResearchSource,
    SearchItem,
    SearchResult,
    WebDocument,
)
from research.ranking import (
    rank_research_sources,
    rank_search_items,
    score_research_source,
    score_search_item,
)
from research.service import ResearchService
from research.skill import create_research_skill
from research.tool import WebSearchTool
from research.url_utils import deduplicate_urls, normalize_url

__all__ = [
    "SearchProvider",
    "FetchProvider",
    "WebProvider",
    "SearchItem",
    "SearchResult",
    "WebDocument",
    "EvidenceItem",
    "EvidenceConflict",
    "ResearchSource",
    "ResearchReport",
    "HTMLTextExtractor",
    "extract_text_and_title_from_html",
    "normalize_url",
    "deduplicate_urls",
    "score_search_item",
    "rank_search_items",
    "score_research_source",
    "rank_research_sources",
    "extract_evidence_from_text",
    "extract_source_evidence",
    "detect_contradictions",
    "ResearchService",
    "WebSearchTool",
    "create_research_skill",
]
