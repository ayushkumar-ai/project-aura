import re
from typing import Sequence
from research.models import CitationValidationResult, ResearchSource


def extract_citations(text: str) -> tuple[int, ...]:
    """Extract distinct positive 1-indexed citation numbers from text formatted like [1], [2], [1, 2]."""
    if not text or not isinstance(text, str):
        return ()

    # Match bracketed numbers like [1], [2], [1, 2, 3] or [1][2]
    pattern = r"\[([0-9,\s]+)\]"
    matches = re.findall(pattern, text)
    citations: set[int] = set()

    for m in matches:
        parts = m.split(",")
        for p in parts:
            p_clean = p.strip()
            if p_clean.isdigit():
                num = int(p_clean)
                if num > 0:
                    citations.add(num)

    return tuple(sorted(citations))


def validate_citations(
    text: str,
    sources: Sequence[ResearchSource],
) -> CitationValidationResult:
    """Validate extracted citations against the list of retrieved ResearchSource items."""
    if not isinstance(sources, (list, tuple)):
        raise TypeError("sources must be a list or tuple of ResearchSource instances.")

    extracted_nums = extract_citations(text)
    num_sources = len(sources)

    valid: list[int] = []
    invalid: list[int] = []

    for num in extracted_nums:
        if 1 <= num <= num_sources:
            valid.append(num)
        else:
            invalid.append(num)

    # Calculate sources that were available but never cited
    cited_set = set(valid)
    unreferenced = [i for i in range(1, num_sources + 1) if i not in cited_set]

    is_valid = len(invalid) == 0 and len(valid) > 0 if num_sources > 0 else True

    return CitationValidationResult(
        is_valid=is_valid,
        total_citations_found=len(extracted_nums),
        valid_citations=tuple(sorted(valid)),
        invalid_citations=tuple(sorted(invalid)),
        unreferenced_sources=tuple(sorted(unreferenced)),
        metadata={
            "total_sources_available": num_sources,
            "citation_coverage_ratio": round(len(valid) / num_sources, 4) if num_sources > 0 else 0.0,
        },
    )
