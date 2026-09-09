import re
import time
from datetime import datetime, timezone
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
    """Extract distinct meaningful lowercase search tokens and compound technical terms from query."""
    raw = re.findall(r"(?:\.?[a-zA-Z0-9_]+(?:[+#.-][a-zA-Z0-9_]+)*[+#]*)", query.lower())
    tokens = [t.strip() for t in raw if t.strip()]
    return [t for t in tokens if (len(t) > 1 or t in {"c", "r"}) and t not in STOP_WORDS]


def compute_content_similarity(text_a: str, text_b: str) -> float:
    """Compute bounded token 3-gram Jaccard similarity between two texts."""
    if not text_a or not text_b:
        return 0.0

    raw_a = re.findall(r"(?:\.?[a-zA-Z0-9_]+(?:[+#.-][a-zA-Z0-9_]+)*[+#]*)", text_a.lower())
    words_a = [w for w in raw_a if (len(w) > 1 or w in {"c", "r"}) and w not in STOP_WORDS]

    raw_b = re.findall(r"(?:\.?[a-zA-Z0-9_]+(?:[+#.-][a-zA-Z0-9_]+)*[+#]*)", text_b.lower())
    words_b = [w for w in raw_b if (len(w) > 1 or w in {"c", "r"}) and w not in STOP_WORDS]

    if not words_a or not words_b:
        return 0.0

    # Extract 3-grams
    ngrams_a = {tuple(words_a[i : i + 3]) for i in range(max(1, len(words_a) - 2))}
    ngrams_b = {tuple(words_b[i : i + 3]) for i in range(max(1, len(words_b) - 2))}

    if not ngrams_a or not ngrams_b:
        set_a = set(words_a)
        set_b = set(words_b)
        intersection = len(set_a.intersection(set_b))
        union = len(set_a.union(set_b))
        return round(intersection / union if union > 0 else 0.0, 4)

    intersection = len(ngrams_a.intersection(ngrams_b))
    union = len(ngrams_a.union(ngrams_b))
    return round(intersection / union if union > 0 else 0.0, 4)


def detect_near_duplicate_sources(
    sources: Sequence[ResearchSource],
    threshold: float = 0.70,
    max_comparisons: int = 20,
) -> set[str]:
    """Identify duplicate or syndicated source URLs based on pairwise content similarity."""
    duplicate_urls: set[str] = set()
    valid_sources = [s for s in sources[:max_comparisons] if s.status == "success" and len((s.content or s.snippet).strip()) >= 50]

    for i in range(len(valid_sources)):
        src_i = valid_sources[i]
        if src_i.url in duplicate_urls:
            continue
        text_i = src_i.content or src_i.snippet

        for j in range(i + 1, len(valid_sources)):
            src_j = valid_sources[j]
            if src_j.url in duplicate_urls:
                continue
            text_j = src_j.content or src_j.snippet

            sim = compute_content_similarity(text_i, text_j)
            if sim >= threshold:
                duplicate_urls.add(src_j.url)

    return duplicate_urls


def calculate_freshness_score(
    published_at: str | None,
    max_age_days: int | None = None,
    reference_timestamp: float | None = None,
) -> float:
    """Calculate deterministic freshness score in [0.0, 1.0] based on published timestamp and age constraints."""
    if not published_at or not published_at.strip():
        if max_age_days is not None:
            return 0.3  # Penalized when freshness explicitly required but date unknown
        return 0.7  # Neutral default for unspecified date

    # Parse ISO or YYYY-MM-DD date
    ref_ts = reference_timestamp if reference_timestamp is not None else time.time()
    parsed_dt: datetime | None = None

    date_match = re.search(r"(\d{4})-(\d{2})-(\d{2})", published_at)
    if date_match:
        try:
            year, month, day = int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3))
            parsed_dt = datetime(year, month, day, tzinfo=timezone.utc)
        except Exception:
            parsed_dt = None

    if parsed_dt is None:
        return 0.4 if max_age_days is not None else 0.7

    age_seconds = max(0.0, ref_ts - parsed_dt.timestamp())
    age_days = age_seconds / 86400.0

    if max_age_days is not None:
        if age_days <= max_age_days:
            decay_ratio = age_days / max(max_age_days, 1)
            score = 1.0 - (0.3 * decay_ratio)
            return round(max(0.6, score), 4)
        else:
            over_ratio = age_days / max_age_days
            score = max(0.05, 0.5 / over_ratio)
            return round(score, 4)

    # General age decay when no specific max_age_days is requested
    if age_days <= 180:  # < 6 months
        return 1.0
    elif age_days <= 365:  # < 1 year
        return 0.9
    elif age_days <= 730:  # < 2 years
        return 0.8
    elif age_days <= 1825:  # < 5 years
        return 0.6
    else:  # > 5 years
        return 0.35


def evaluate_source_quality(
    source: ResearchSource,
    max_age_days: int | None = None,
) -> float:
    """Deterministically evaluate the quality, completeness, and freshness credibility of a ResearchSource."""
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

    # 2. Content Substance & Completeness Signals
    content_len = len(content.strip())
    if content_len >= 500:
        quality += 0.3
    elif content_len >= 200:
        quality += 0.15
    elif content_len < 50:
        quality -= 0.6  # Heavy penalty for empty or near-empty stubs

    if source.title and source.title.lower() != "untitled" and len(source.title) > 5:
        quality += 0.2

    # 3. Low-Quality & Spam Signals
    if any(ind in url for ind in LOW_QUALITY_INDICATORS):
        quality -= 0.6

    # 4. Freshness Evaluation
    freshness = calculate_freshness_score(source.published_at, max_age_days=max_age_days)
    if max_age_days is not None:
        if freshness < 0.5:
            quality -= 0.7  # Penalize stale sources when fresh info requested
        else:
            quality += 0.4
    else:
        if freshness >= 0.8:
            quality += 0.15
        elif freshness < 0.4:
            quality -= 0.2

    return max(round(quality, 4), 0.1)


def score_search_item(item: SearchItem, query: str, position_index: int = 0) -> float:
    """Deterministically score a SearchItem based on provider position and keyword relevance."""
    if not isinstance(item, SearchItem):
        raise TypeError("item must be a SearchItem instance.")

    query_keywords = _extract_query_keywords(query)
    score = 10.0 - min(position_index * 1.0, 5.0)

    domain = item.source_domain.lower()
    url = item.url.lower()
    title = item.title.lower()
    snippet = item.snippet.lower()

    if any(domain.endswith(tld) for tld in AUTHORITATIVE_TLDS):
        score += 3.0

    if any(kw in domain or kw in url for kw in DOCS_KEYWORDS):
        score += 2.0

    if any(ind in url for ind in LOW_QUALITY_INDICATORS):
        score -= 5.0

    if query_keywords:
        title_hits = sum(1 for kw in query_keywords if kw in title)
        snippet_hits = sum(1 for kw in query_keywords if kw in snippet)
        keyword_overlap = (title_hits * 2.0) + (snippet_hits * 1.0)
        score += keyword_overlap

    return max(round(score, 4), 0.0)


def rank_search_items(items: Sequence[SearchItem], query: str) -> list[SearchItem]:
    """Rank and sort a sequence of SearchItem objects deterministically."""
    if not isinstance(items, (list, tuple)):
        raise TypeError("items must be a list or tuple of SearchItem instances.")

    scored_items: list[tuple[float, int, SearchItem]] = []
    for idx, item in enumerate(items):
        if not isinstance(item, SearchItem):
            raise TypeError("All items must be SearchItem instances.")
        score = score_search_item(item, query, position_index=idx)
        scored_items.append((score, idx, item))

    scored_items.sort(key=lambda x: (-x[0], x[1], x[2].url))
    return [item for _, _, item in scored_items]


def score_research_source(
    source: ResearchSource,
    query: str,
    max_age_days: int | None = None,
) -> float:
    """Deterministically score a ResearchSource based on quality, hop penalty, evidence count, and keyword relevance."""
    if not isinstance(source, ResearchSource):
        raise TypeError("source must be a ResearchSource instance.")

    if source.status != "success":
        return 0.0

    quality_score = evaluate_source_quality(source, max_age_days=max_age_days)
    hop_penalty = source.hop * 0.8
    evidence_bonus = len(source.evidence) * 0.5

    query_keywords = _extract_query_keywords(query)
    title_lower = source.title.lower()
    content_lower = (source.content or source.snippet).lower()

    relevance_score = 0.0
    if query_keywords:
        title_hits = sum(1 for kw in query_keywords if kw in title_lower)
        content_hits = sum(1 for kw in query_keywords if kw in content_lower)
        relevance_score = (title_hits * 1.5) + min(content_hits * 0.2, 3.0)

    final_score = quality_score + evidence_bonus + relevance_score - hop_penalty
    return max(round(final_score, 4), 0.1)


def rank_research_sources(
    sources: Sequence[ResearchSource],
    query: str,
    max_age_days: int | None = None,
    deduplicate_near_duplicates: bool = True,
) -> list[ResearchSource]:
    """Rank, score, update, and sort a sequence of ResearchSource objects deterministically."""
    if not isinstance(sources, (list, tuple)):
        raise TypeError("sources must be a list or tuple of ResearchSource instances.")

    duplicate_urls = detect_near_duplicate_sources(sources) if deduplicate_near_duplicates else set()

    scored_sources: list[tuple[float, float, str, ResearchSource]] = []
    for s in sources:
        if not isinstance(s, ResearchSource):
            raise TypeError("All items in sources must be ResearchSource instances.")

        raw_score = score_research_source(s, query, max_age_days=max_age_days)
        if s.url in duplicate_urls:
            raw_score = max(0.05, round(raw_score * 0.3, 4))

        q_score = evaluate_source_quality(s, max_age_days=max_age_days)
        freshness = calculate_freshness_score(s.published_at, max_age_days=max_age_days)

        updated_s = ResearchSource(
            url=s.url,
            title=s.title,
            snippet=s.snippet,
            content=s.content,
            status=s.status,
            error=s.error,
            source_domain=s.source_domain,
            evidence=s.evidence,
            rank_score=raw_score,
            hop=s.hop,
            parent_url=s.parent_url,
            quality_score=q_score,
            published_at=s.published_at,
            freshness_score=freshness,
            metadata=dict(s.metadata),
        )
        scored_sources.append((raw_score, q_score, updated_s.url, updated_s))

    scored_sources.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return [s for _, _, _, s in scored_sources]
