from research.extractor import HTMLTextExtractor, extract_text_and_title_from_html
from research.interfaces import FetchProvider, SearchProvider, WebProvider
from research.models import (
    ResearchReport,
    ResearchSource,
    SearchItem,
    SearchResult,
    WebDocument,
)
from research.providers.factory import create_fetch_provider, create_search_provider
from research.providers.generic_http import GenericHttpSearchProvider
from research.providers.http_fetch import HttpFetchProvider
from research.providers.tavily import TavilySearchProvider
from research.service import ResearchService
from research.skill import create_research_skill
from research.tool import WebSearchTool

__all__ = [
    "SearchProvider",
    "FetchProvider",
    "WebProvider",
    "SearchItem",
    "SearchResult",
    "WebDocument",
    "ResearchSource",
    "ResearchReport",
    "ResearchService",
    "WebSearchTool",
    "create_research_skill",
    "create_search_provider",
    "create_fetch_provider",
    "HttpFetchProvider",
    "TavilySearchProvider",
    "GenericHttpSearchProvider",
    "HTMLTextExtractor",
    "extract_text_and_title_from_html",
]
