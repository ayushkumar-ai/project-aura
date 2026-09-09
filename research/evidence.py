import re
from typing import Sequence
from research.models import EvidenceItem, ResearchSource
from research.ranking import _extract_query_keywords


def _split_into_candidate_passages(text: str) -> list[str]:
    """Split document text into clean, coherent sentences or paragraph blocks."""
    if not text or not text.strip():
        return []

    # First split on structural paragraphs
    raw_paragraphs = re.split(r"\n\n+", text)
    passages: list[str] = []

    for para in raw_paragraphs:
        cleaned_para = para.strip()
        if not cleaned_para:
            continue
        # If paragraph is short, keep as single passage
        if len(cleaned_para) <= 300:
            if len(cleaned_para) >= 20:
                passages.append(cleaned_para)
        else:
            # Split longer paragraphs into sentences
            sentences = re.split(r"(?<=[.!?])\s+", cleaned_para)
            curr_buf = ""
            for s in sentences:
                s_clean = s.strip()
                if not s_clean:
                    continue
                if len(curr_buf) + len(s_clean) < 300:
                    curr_buf = f"{curr_buf} {s_clean}".strip() if curr_buf else s_clean
                else:
                    if curr_buf and len(curr_buf) >= 20:
                        passages.append(curr_buf)
                    curr_buf = s_clean
            if curr_buf and len(curr_buf) >= 20:
                passages.append(curr_buf)

    return passages


def _score_passage_relevance(passage: str, keywords: list[str]) -> float:
    """Calculate deterministic relevance score for an evidence passage against query keywords."""
    if not keywords:
        return 1.0

    p_lower = passage.lower()
    matches = sum(1 for kw in keywords if kw in p_lower)
    overlap_ratio = matches / len(keywords)

    # Bonus for multiple keyword occurrences
    bonus = 0.0
    for kw in keywords:
        count = p_lower.count(kw)
        if count > 1:
            bonus += min(count * 0.1, 0.5)

    return round(overlap_ratio * 3.0 + bonus, 4)


def extract_evidence_from_text(
    query: str,
    text: str,
    source_url: str,
    source_title: str,
    source_domain: str = "",
    max_passages: int = 3,
    max_passage_chars: int = 400,
) -> tuple[EvidenceItem, ...]:
    """Extract bounded, relevant evidence passages from raw text with source attribution."""
    if not text or not text.strip():
        return ()

    if max_passages <= 0:
        return ()

    keywords = _extract_query_keywords(query)
    candidates = _split_into_candidate_passages(text)

    if not candidates:
        # Fallback if text is shorter than threshold
        clean_text = text.strip()
        if len(clean_text) > max_passage_chars:
            clean_text = clean_text[:max_passage_chars] + "... [truncated]"
        score = _score_passage_relevance(clean_text, keywords)
        return (
            EvidenceItem(
                source_url=source_url,
                source_title=source_title,
                source_domain=source_domain,
                content=clean_text,
                relevance_score=score,
            ),
        )

    scored_candidates: list[tuple[float, int, str]] = []
    seen_passages: set[str] = set()

    for idx, passage in enumerate(candidates):
        norm = passage.strip()
        if norm in seen_passages:
            continue
        seen_passages.add(norm)

        # Truncate if passage exceeds maximum passage chars
        if len(norm) > max_passage_chars:
            norm = norm[:max_passage_chars] + "... [truncated]"

        score = _score_passage_relevance(norm, keywords)
        scored_candidates.append((-score, idx, norm))

    # Sort deterministically by highest score, then original order
    scored_candidates.sort(key=lambda x: (x[0], x[1]))

    evidence_items: list[EvidenceItem] = []
    for neg_score, _, passage_content in scored_candidates[:max_passages]:
        score = -neg_score
        item = EvidenceItem(
            source_url=source_url,
            source_title=source_title,
            source_domain=source_domain,
            content=passage_content,
            relevance_score=score,
        )
        evidence_items.append(item)

    return tuple(evidence_items)


def extract_source_evidence(
    source: ResearchSource,
    query: str,
    max_passages: int = 3,
    max_passage_chars: int = 400,
) -> tuple[EvidenceItem, ...]:
    """Extract bounded evidence items from a ResearchSource object."""
    if not isinstance(source, ResearchSource):
        raise TypeError("source must be a ResearchSource instance.")

    if source.status != "success":
        return ()

    content_to_use = source.content or source.snippet
    return extract_evidence_from_text(
        query=query,
        text=content_to_use,
        source_url=source.url,
        source_title=source.title,
        source_domain=source.source_domain,
        max_passages=max_passages,
        max_passage_chars=max_passage_chars,
    )
