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


def _extract_query_keywords(query: str) -> list[str]:
    """Extract distinct meaningful lowercase search tokens from query."""
    tokens = re.findall(r"\b[a-zA-Z0-9_-]+\b", query.lower())
    return [t for t in tokens if t not in STOP_WORDS and len(t) > 1]


def score_search_item(item: SearchItem, query: str, position_index: int = 0) -> float:
    """Deterministically score a SearchItem based on provider position and keyword relevance."""
    if not isinstance(item, SearchItem):
        raise TypeError("item must be a SearchItem instance.")

    # 1. Base position score (earlier positions get higher initial weight from search engine)
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
        # Tie break on -score, url, title for deterministic repeatability
        scored_items.append((-score, item.url, item.title, item))

    scored_items.sort(key=lambda x: (x[0], x[1], x[2]))
    return [entry[3] for entry in scored_items]


def score_research_source(
    source: ResearchSource,
    query: str,
    position_index: int = 0,
    domain_counts: dict[str, int] | None = None,
) -> float:
    """Deterministically score a ResearchSource based on relevance, content quality, and domain diversity."""
    if not isinstance(source, ResearchSource):
        raise TypeError("source must be a ResearchSource instance.")

    if source.status != "success":
        return 0.0

    # 1. Base position factor (provider search rank has prominent baseline weight)
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

    # 3. Content quality signals
    content_len = len(source.content)
    quality_score = 0.0
    if content_len >= 50:
        quality_score += 0.5
    if content_len >= 300:
        quality_score += 0.5
    if source.title and source.title.lower() != "untitled":
        quality_score += 0.3

    # 4. Domain diversity balancing (slight decay for repeated domains)
    domain_decay = 1.0
    if domain_counts is not None and source.source_domain:
        seen = domain_counts.get(source.source_domain, 0)
        domain_decay = 1.0 / (1.0 + 0.25 * seen)

    total_score = (base_pos + kw_score + quality_score) * domain_decay
    return round(total_score, 4)


def rank_research_sources(sources: Sequence[ResearchSource], query: str) -> list[ResearchSource]:
    """Deterministically rank a collection of ResearchSource objects, setting rank_score and tie-breaking."""
    if not isinstance(sources, (list, tuple)):
        raise TypeError("sources must be a list or tuple of ResearchSource instances.")

    domain_counts: dict[str, int] = {}
    scored_sources: list[tuple[float, str, str, ResearchSource]] = []

    for idx, src in enumerate(sources):
        score = score_research_source(src, query, position_index=idx, domain_counts=domain_counts)
        if src.source_domain:
            domain_counts[src.source_domain] = domain_counts.get(src.source_domain, 0) + 1

        # Return updated ResearchSource with computed rank_score
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
            metadata=dict(src.metadata),
        )
        scored_sources.append((-score, ranked_src.url, ranked_src.title, ranked_src))

    scored_sources.sort(key=lambda x: (x[0], x[1], x[2]))
    return [entry[3] for entry in scored_sources]
