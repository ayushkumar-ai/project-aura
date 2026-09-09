from research.citations import extract_citations, validate_citations
from research.claims import aggregate_claims_with_contradictions, extract_claims_from_evidence
from research.confidence import calculate_research_confidence
from research.contradictions import detect_contradictions
from research.crawler import BoundedWebCrawler, score_discovered_link
from research.evidence import extract_evidence_from_text, extract_source_evidence
from research.extractor import HTMLTextExtractor, extract_links_from_html, extract_text_and_title_from_html
from research.interfaces import BrowserProvider, FetchProvider, SearchProvider, WebProvider
from research.models import (
    AnswerCitation,
    AnswerSection,
    AssembledAnswer,
    CitationValidationResult,
    ClaimEvidence,
    ClaimVerificationStatus,
    DiscoveredLink,
    EvidenceConflict,
    EvidenceItem,
    ResearchClaim,
    ResearchConfidence,
    ResearchReport,
    ResearchSource,
    ResearchSubQuestion,
    SearchItem,
    SearchResult,
    VerifiedClaim,
    WebDocument,
)
from research.planner import ResearchPlanner
from research.providers.browser import BrowserFetchProvider, FakeBrowserProvider
from research.providers.factory import (
    create_browser_provider,
    create_fetch_provider,
    create_search_provider,
)
from research.ranking import (
    evaluate_source_quality,
    rank_research_sources,
    rank_search_items,
    score_research_source,
    score_search_item,
)
from research.service import ResearchService
from research.skill import create_research_skill
from research.tool import WebSearchTool
from research.url_utils import deduplicate_urls, normalize_url
from research.verification import ClaimVerifier, verify_claims
from research.assembly import AnswerAssembler, assemble_answer

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
    "ResearchSubQuestion",
    "ClaimEvidence",
    "ResearchClaim",
    "ClaimVerificationStatus",
    "VerifiedClaim",
    "AnswerCitation",
    "AnswerSection",
    "AssembledAnswer",
    "CitationValidationResult",
    "ResearchConfidence",
    "ResearchReport",
    "HTMLTextExtractor",
    "extract_text_and_title_from_html",
    "extract_links_from_html",
    "normalize_url",
    "deduplicate_urls",
    "score_search_item",
    "rank_search_items",
    "evaluate_source_quality",
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
    "ResearchPlanner",
    "extract_claims_from_evidence",
    "aggregate_claims_with_contradictions",
    "extract_citations",
    "validate_citations",
    "calculate_research_confidence",
    "ClaimVerifier",
    "verify_claims",
    "AnswerAssembler",
    "assemble_answer",
    "ResearchService",
    "WebSearchTool",
    "create_research_skill",
]
