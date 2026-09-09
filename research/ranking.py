import re
from typing import Sequence
from research.models import ResearchSource, SearchItem

STOP_WORDS = frozenset({
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any", "are",
    "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but",
    "by", "could", "did", "do", "does", "doing", "down", "during", "each", "few", "for", "from",
    "further", "had", "has", "have", "having", "he", "her", "here", "hers", "herself", "him",
    "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself", "just", "me",
    "more", "most", "my", "myself", "no", "nor", "not", "now", "of", "off", "on", "once", "only",
    "or", "other", "ought", "our", "ours", "ourselves", "out", "over", "own", "same", "she",
    "should", "so", "some", "such", "than", "that", "the", "their", "theirs", "them", "themselves",
    "then", "there", "these", "they", "this", "those", "through", "to", "too", "under", "until",
    "up", "very", "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom",
    "why", "with", "would", "you", "your", "yours", "yourself", "yourselves"
})

AUTHORITATIVE_TLDS = frozenset({".gov", ".edu", ".mil", ".ac.uk", ".gov.uk", ".org"})
DOCS_KEYWORDS = frozenset({"docs", "documentation", "guide", "manual", "spec", "specification", "rfc", "arxiv", "developer", "api"})
LOW_QUALITY_INDICATORS = frozenset({"spam", "promoted", "sponsored", "adclick", "affiliate", "popup", "track"})


def _extract_query_keywords(query: str) -> list[str]:
    """Extract distinct meaningful lowercase search tokens from query."""
    tokens = re.findall(r"\b[a-zA-Z0-9_-]+\b", query.lower())
    return [t for t in tokens if t not in STOP_WORDS and len(t) > 1]


def evaluate_source_quality(source: ResearchSource) -> float:
    """Deterministically evaluate the quality, domain authority, and credibility of a ResearchSource."""
    if not isinstance(source, ResearchSource):
        raise TypeError("source must be a ResearchSource instance.")

    if source.status != "success":
        return 0.0

    domain = (source.source_domain or "").lower().strip()
    url = source.url.lower().strip()
    content = source.content or source.snippet or ""

    quality = 1.0

    # 1. Domain Authority Signals
    if any(domain.endswith(tld) for tld in AUTHORITATIVE_TLDS):
        quality += 0.5

    if any(kw in domain or kw in url for kw in DOCS_KEYWORDS):
        quality += 0.4

    # 2. Content Substance Signals
    content_len = len(content.strip())
    if content_len >= 500:
        quality += 0.3
    elif content_len >= 200:
        quality += 0.15
    elif content_len < 40:
        quality -= 0.4

    if source.title and source.title.lower() != "untitled" and len(source.title) > 5:
        quality += 0.2

    # 3. Low-Quality & Spam Signals
    if any(ind in url for ind in LOW_QUALITY_INDICATORS):
        quality -= 0.6

    # 4. Freshness hints
    current_year_str = "2026"
    if current_year_str in content or current_year_str in url or "2025" in content:
        quality += 0.2

    return max(round(quality, 4), 0.1)


def score_search_item(item: SearchItem, query: str, position_index: int = 0) -> float:
    """Deterministically score a SearchItem based on provider position and keyword relevance."""
    if not isinstance(item, SearchItem):
        raise TypeError("item must be a SearchItem instance.")

    # 1. Base position score
    base_pos = 3.0 / (1.0 + 0.5 * position_index)

    # 2. Keyword relevance
    keywords = _extract_query_keywords(query)
    kw_score = 0.0
    if keywords:
        title_lower = item.title.lower()
        snippet_lower = item.snippet.lower()

        title_hits = sum(1 for kw in keywords if kw in title_lower)
        snippet_hits = sum(1 for kw in keywords if kw in snippet_lower)

        title_ratio = title_hits / len(keywords)
        snippet_ratio = snippet_hits / len(keywords)

        kw_score = (title_ratio * 2.0) + (snippet_ratio * 1.0)

    # 3. Domain presence bonus
    domain_bonus = 0.2 if item.source_domain else 0.0

    total_score = base_pos + kw_score + domain_bonus
    return round(total_score, 4)


def rank_search_items(items: Sequence[SearchItem], query: str) -> list[SearchItem]:
    """Deterministically rank a collection of SearchItem objects with tie-breaking."""
    if not isinstance(items, (list, tuple)):
        raise TypeError("items must be a list or tuple of SearchItem instances.")

    scored_items: list[tuple[float, str, str, SearchItem]] = []
    for idx, item in enumerate(items):
        score = score_search_item(item, query, position_index=idx)
        scored_items.append((-score, item.url, item.title, item))

    scored_items.sort(key=lambda x: (x[0], x[1], x[2]))
    return [entry[3] for entry in scored_items]


def score_research_source(
    source: ResearchSource,
    query: str,
    position_index: int = 0,
    domain_counts: dict[str, int] | None = None,
) -> float:
    """Deterministically score a ResearchSource based on relevance, quality, and domain diversity."""
    if not isinstance(source, ResearchSource):
        raise TypeError("source must be a ResearchSource instance.")

    if source.status != "success":
        return 0.0

    # 1. Base position factor
    base_pos = 3.0 / (1.0 + 0.6 * position_index)

    # 2. Keyword relevance in title, snippet, and content
    keywords = _extract_query_keywords(query)
    kw_score = 0.0
    if keywords:
        title_lower = source.title.lower()
        snippet_lower = source.snippet.lower()
        content_lower = source.content.lower()

        title_hits = sum(1 for kw in keywords if kw in title_lower)
        snippet_hits = sum(1 for kw in keywords if kw in snippet_lower)
        content_hits = sum(1 for kw in keywords if kw in content_lower)

        title_ratio = title_hits / len(keywords)
        snippet_ratio = snippet_hits / len(keywords)
        content_ratio = content_hits / len(keywords)

        kw_score = (title_ratio * 2.0) + (snippet_ratio * 0.8) + (content_ratio * 1.5)

    # 3. Quality evaluation factor
    quality_val = evaluate_source_quality(source)

    # 4. Domain diversity balancing
    domain_decay = 1.0
    if domain_counts is not None and source.source_domain:
        seen = domain_counts.get(source.source_domain, 0)
        domain_decay = 1.0 / (1.0 + 0.25 * seen)

    total_score = (base_pos + kw_score + (quality_val * 0.5)) * domain_decay
    return round(total_score, 4)


def rank_research_sources(sources: Sequence[ResearchSource], query: str) -> list[ResearchSource]:
    """Deterministically rank a collection of ResearchSource objects, setting rank_score and quality_score."""
    if not isinstance(sources, (list, tuple)):
        raise TypeError("sources must be a list or tuple of ResearchSource instances.")

    domain_counts: dict[str, int] = {}
    scored_sources: list[tuple[float, str, str, ResearchSource]] = []

    for idx, src in enumerate(sources):
        score = score_research_source(src, query, position_index=idx, domain_counts=domain_counts)
        q_score = evaluate_source_quality(src)
        if src.source_domain:
            domain_counts[src.source_domain] = domain_counts.get(src.source_domain, 0) + 1

        ranked_src = ResearchSource(
            url=src.url,
            title=src.title,
            snippet=src.snippet,
            content=src.content,
            status=src.status,
            error=src.error,
            source_domain=src.source_domain,
            evidence=src.evidence,
            rank_score=score,
            hop=src.hop,
            parent_url=src.parent_url,
            quality_score=q_score,
            metadata=dict(src.metadata),
        )
        scored_sources.append((-score, ranked_src.url, ranked_src.title, ranked_src))

    scored_sources.sort(key=lambda x: (x[0], x[1], x[2]))
    return [entry[3] for entry in scored_sources]
