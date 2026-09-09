import re
from typing import Sequence
from research.models import EvidenceConflict, EvidenceItem

# Common polar opposites and negation markers
AFFIRMATIVE_TERMS = frozenset({
    "achieved", "demonstrated", "confirmed", "supported", "approved", "succeeded",
    "operational", "functional", "increased", "viable", "available", "completed"
})

NEGATIVE_TERMS = frozenset({
    "failed", "unsupported", "rejected", "disproven", "unachieved", "non-functional",
    "unviable", "delayed", "unavailable", "cancelled", "denied", "impossible"
})

NEGATION_MODIFIERS = frozenset({
    "not", "never", "cannot", "hardly", "barely", "failed to", "unable to"
})


def _extract_numeric_entities(text: str) -> list[tuple[str, str]]:
    """Extract numeric metrics and their associated entity nouns (e.g., ('1000', 'qubits'), ('100', 'mw'))."""
    pattern = r"\b(\$?\d+(?:[\.,]\d+)?%?)\s*([a-zA-Z_-]+)?"
    matches = re.findall(pattern, text)
    results = []
    for num, noun in matches:
        clean_num = num.replace(",", "").strip()
        clean_noun = noun.lower().strip() if noun else ""
        if clean_noun and len(clean_noun) >= 2:
            results.append((clean_num, clean_noun))
    return results


def detect_contradictions(
    evidence_items: Sequence[EvidenceItem],
    query: str = "",
) -> tuple[EvidenceConflict, ...]:
    """Deterministically detect potential factual or numerical contradictions across different sources."""
    if not isinstance(evidence_items, (list, tuple)):
        raise TypeError("evidence_items must be a list or tuple of EvidenceItem instances.")

    if len(evidence_items) < 2:
        return ()

    conflicts: list[EvidenceConflict] = []
    seen_conflict_keys: set[tuple[str, str, str]] = set()

    # Compare pairwise across distinct source URLs
    for i in range(len(evidence_items)):
        item_a = evidence_items[i]
        for j in range(i + 1, len(evidence_items)):
            item_b = evidence_items[j]
            if item_a.source_url == item_b.source_url:
                continue

            # Check 1: Numerical/metric conflict on identical entity noun
            nums_a = _extract_numeric_entities(item_a.content)
            nums_b = _extract_numeric_entities(item_b.content)

            for val_a, noun_a in nums_a:
                for val_b, noun_b in nums_b:
                    if noun_a == noun_b and val_a != val_b:
                        claim_desc = f"Conflicting metric for '{noun_a}': {val_a} vs {val_b}"
                        key = (claim_desc, min(item_a.source_url, item_b.source_url), max(item_a.source_url, item_b.source_url))
                        if key not in seen_conflict_keys:
                            seen_conflict_keys.add(key)
                            conflicts.append(
                                EvidenceConflict(
                                    claim=claim_desc,
                                    source_a_url=item_a.source_url,
                                    source_a_evidence=item_a.content,
                                    source_b_url=item_b.source_url,
                                    source_b_evidence=item_b.content,
                                    conflict_type="numerical_mismatch",
                                )
                            )

            # Check 2: Affirmation vs Negation polarity conflict
            words_a = set(re.findall(r"\b[a-zA-Z]+\b", item_a.content.lower()))
            words_b = set(re.findall(r"\b[a-zA-Z]+\b", item_b.content.lower()))

            has_aff_a = bool(words_a & AFFIRMATIVE_TERMS)
            has_neg_a = bool(words_a & NEGATIVE_TERMS) or any(neg in item_a.content.lower() for neg in NEGATION_MODIFIERS)

            has_aff_b = bool(words_b & AFFIRMATIVE_TERMS)
            has_neg_b = bool(words_b & NEGATIVE_TERMS) or any(neg in item_b.content.lower() for neg in NEGATION_MODIFIERS)

            # If A affirms and B negates on common subject keywords
            common_words = (words_a & words_b) - AFFIRMATIVE_TERMS - NEGATIVE_TERMS
            meaningful_common = [w for w in common_words if len(w) > 4]

            if meaningful_common:
                if (has_aff_a and has_neg_b and not has_neg_a) or (has_neg_a and has_aff_b and not has_neg_b):
                    subject_topic = meaningful_common[0]
                    claim_desc = f"Polarity divergence regarding '{subject_topic}'"
                    key = (claim_desc, min(item_a.source_url, item_b.source_url), max(item_a.source_url, item_b.source_url))
                    if key not in seen_conflict_keys:
                        seen_conflict_keys.add(key)
                        conflicts.append(
                            EvidenceConflict(
                                claim=claim_desc,
                                source_a_url=item_a.source_url,
                                source_a_evidence=item_a.content,
                                source_b_url=item_b.source_url,
                                source_b_evidence=item_b.content,
                                conflict_type="polarity_conflict",
                            )
                        )

    # Sort deterministically
    conflicts.sort(key=lambda c: (c.conflict_type, c.claim, c.source_a_url, c.source_b_url))
    return tuple(conflicts)
