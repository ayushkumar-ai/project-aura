from research.contradictions import detect_contradictions
from research.crawler import BoundedWebCrawler, score_discovered_link
from research.evidence import extract_evidence_from_text, extract_source_evidence
from research.extractor import HTMLTextExtractor, extract_links_from_html, extract_text_and_title_from_html
from research.interfaces import BrowserProvider, FetchProvider, SearchProvider, WebProvider
from research.models import (
    DiscoveredLink,
    EvidenceConflict,
    EvidenceItem,
    ResearchReport,
    ResearchSource,
    SearchItem,
    SearchResult,
    WebDocument,
)
from research.providers.browser import BrowserFetchProvider, FakeBrowserProvider
from research.providers.factory import (
    create_browser_provider,
    create_fetch_provider,
    create_search_provider,
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
    "BrowserProvider",
    "WebProvider",
    "SearchItem",
    "SearchResult",
    "WebDocument",
    "EvidenceItem",
    "EvidenceConflict",
    "DiscoveredLink",
    "ResearchSource",
    "ResearchReport",
    "HTMLTextExtractor",
    "extract_text_and_title_from_html",
    "extract_links_from_html",
    "normalize_url",
    "deduplicate_urls",
    "score_search_item",
    "rank_search_items",
    "score_research_source",
    "rank_research_sources",
    "extract_evidence_from_text",
    "extract_source_evidence",
    "detect_contradictions",
    "score_discovered_link",
    "BoundedWebCrawler",
    "BrowserFetchProvider",
    "FakeBrowserProvider",
    "create_search_provider",
    "create_fetch_provider",
    "create_browser_provider",
    "ResearchService",
    "WebSearchTool",
    "create_research_skill",
]
