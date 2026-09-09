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



    """Extract distinct meaningful lowercase search tokens from query."""



    tokens = re.findall(r"\b[a-zA-Z0-9_-]+\b", query.lower())



    return [t for t in tokens if t not in STOP_WORDS and len(t) > 1]











def compute_content_similarity(text_a: str, text_b: str) -> float:



    """Compute bounded token 3-gram Jaccard similarity between two texts."""



    if not text_a or not text_b:



        return 0.0







    words_a = [w.lower() for w in re.findall(r"\b[a-zA-Z0-9_]+\b", text_a) if len(w) > 2 and w not in STOP_WORDS]



    words_b = [w.lower() for w in re.findall(r"\b[a-zA-Z0-9_]+\b", text_b) if len(w) > 2 and w not in STOP_WORDS]







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



                # Flag the second (lower ranked or secondary) source as duplicate



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



            # Stale source beyond required window



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



    is_duplicate: bool = False,



    max_age_days: int | None = None,



) -> float:



    """Deterministically score a ResearchSource based on relevance, quality, domain diversity, and freshness."""



    if not isinstance(source, ResearchSource):



        raise TypeError("source must be a ResearchSource instance.")







    if source.status != "success":



        return 0.0







    # 1. Base position factor (tiebreaker)



    base_pos = 1.0 / (1.0 + 0.2 * position_index)







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







    # 3. Quality and Freshness evaluation factor



    quality_val = evaluate_source_quality(source, max_age_days=max_age_days)







    # 4. Domain diversity balancing



    domain_decay = 1.0



    if domain_counts is not None and source.source_domain:



        seen = domain_counts.get(source.source_domain, 0)



        domain_decay = 1.0 / (1.0 + 0.25 * seen)







    # 5. Near-duplicate penalty



    duplicate_factor = 0.35 if is_duplicate else 1.0







    total_score = (base_pos + kw_score + (quality_val * 1.5)) * domain_decay * duplicate_factor



    return round(total_score, 4)











def rank_research_sources(



    sources: Sequence[ResearchSource],



    query: str,



    max_age_days: int | None = None,



) -> list[ResearchSource]:



    """Deterministically rank a collection of ResearchSource objects, setting rank_score, quality_score, and freshness_score."""



    if not isinstance(sources, (list, tuple)):



        raise TypeError("sources must be a list or tuple of ResearchSource instances.")







    duplicate_urls = detect_near_duplicate_sources(sources)



    domain_counts: dict[str, int] = {}



    scored_sources: list[tuple[float, str, str, ResearchSource]] = []







    for idx, src in enumerate(sources):



        is_dup = src.url in duplicate_urls



        score = score_research_source(



            src,



            query,



            position_index=idx,



            domain_counts=domain_counts,



            is_duplicate=is_dup,



            max_age_days=max_age_days,



        )



        q_score = evaluate_source_quality(src, max_age_days=max_age_days)



        f_score = calculate_freshness_score(src.published_at, max_age_days=max_age_days)







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



            published_at=src.published_at,



            freshness_score=f_score,



            metadata=dict(src.metadata),



        )



        scored_sources.append((-score, ranked_src.url, ranked_src.title, ranked_src))







    scored_sources.sort(key=lambda x: (x[0], x[1], x[2]))



    return [entry[3] for entry in scored_sources]
