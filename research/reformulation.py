import json
import logging
import re
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from interfaces.model import ModelInterface
from research.models import EvidenceConflict, EvidenceItem, ResearchCoverage, ResearchSubQuestion

logger = logging.getLogger("aura.research.reformulation")

_STOPWORDS = frozenset({
    "a", "an", "the", "in", "on", "of", "to", "for", "with", "by", "at", "from",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "and", "or", "but", "if", "then", "else", "when",
    "this", "that", "these", "those", "it", "its", "as", "what", "which", "who",
    "how", "all", "any", "both", "each", "more", "most", "other", "some", "such",
    "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very", "can",
    "will", "just", "should", "now", "overview", "information", "find", "search",
    "explain", "detail", "details",
})

_SYNONYM_MAP = {
    "cost": ["pricing", "economics", "price breakdown"],
    "costs": ["pricing", "economics", "expenses"],
    "manufacturing": ["production", "fabrication", "assembly"],
    "efficiency": ["performance", "benchmarks", "metrics"],
    "power": ["energy consumption", "wattage"],
    "features": ["capabilities", "specifications", "specs"],
    "security": ["vulnerabilities", "safety", "defense"],
    "versus": ["comparison", "vs", "differences"],
    "compare": ["comparison", "vs", "tradeoffs"],
    "speed": ["throughput", "latency", "performance"],
}

_CONVERSATIONAL_PREFIXES = (
    r"^(?:what\s+is\s+the|what\s+are\s+the|how\s+does|how\s+to|why\s+is|can\s+you\s+find|please\s+search\s+for|search\s+for|find\s+information\s+on|overview\s+of|explain\s+the|details\s+about)\s+",
)


def _clean_query_text(text: str, max_chars: int = 200) -> str:
    """Sanitize and clamp query string, neutralizing prompt-injection tokens and control characters."""
    if not text:
        return ""
    clean = re.sub(r"[\r\n\t]+", " ", str(text)).strip()
    clean = re.sub(r"<[^>]+>", "", clean).strip()
    clean = re.sub(r"[{}\[\]\"\']", "", clean).strip()
    if len(clean) > max_chars:
        clean = clean[:max_chars].strip()
    return clean


def _extract_core_keywords(query: str) -> list[str]:
    """Extract ordered distinctive keyword tokens from query."""
    raw = re.findall(r"(?:\.?[a-zA-Z0-9_]+(?:[+#.-][a-zA-Z0-9_]+)*[+#]*)", query.lower())
    tokens = [t.strip() for t in raw if t.strip()]
    return [t for t in tokens if (len(t) > 1 or t in {"c", "r"}) and t not in _STOPWORDS]


class QueryReformulator:
    """Deterministically analyzes unresolved research sub-questions and produces bounded reformulated queries."""

    def __init__(
        self,
        max_reformulations_per_question: int = 2,
        max_query_chars: int = 200,
    ):
        if not isinstance(max_reformulations_per_question, int) or max_reformulations_per_question <= 0:
            raise ValueError("max_reformulations_per_question must be a positive integer.")
        if not isinstance(max_query_chars, int) or max_query_chars <= 0:
            raise ValueError("max_query_chars must be a positive integer.")

        self.max_reformulations_per_question = max_reformulations_per_question
        self.max_query_chars = max_query_chars

    def reformulate_heuristic(
        self,
        unresolved_sub_question: str,
        original_query: str = "",
        evidence: Sequence[EvidenceItem] = (),
        contradictions: Sequence[EvidenceConflict] = (),
        max_alternatives: int | None = None,
    ) -> tuple[str, ...]:
        """Generate deterministic heuristic query reformulations for an unresolved sub-question."""
        if not isinstance(unresolved_sub_question, str) or not unresolved_sub_question.strip():
            return ()

        limit = max_alternatives or self.max_reformulations_per_question
        q_clean = unresolved_sub_question.strip()

        # 1. Strip conversational prefixes
        core_query = q_clean
        for prefix in _CONVERSATIONAL_PREFIXES:
            core_query = re.sub(prefix, "", core_query, flags=re.IGNORECASE).strip()

        alternatives: list[str] = []
        seen: set[str] = {q_clean.lower(), core_query.lower()}

        # 2. Strategy A: Synonym and Term Expansion
        words = core_query.split()
        synonym_replaced_words = list(words)
        replaced_count = 0
        for i, w in enumerate(words):
            w_lower = w.lower()
            if w_lower in _SYNONYM_MAP and replaced_count < 2:
                synonym_replaced_words[i] = _SYNONYM_MAP[w_lower][0]
                replaced_count += 1

        if replaced_count > 0:
            alt_synonym = " ".join(synonym_replaced_words)
            alt_synonym_clean = _clean_query_text(alt_synonym, self.max_query_chars)
            if alt_synonym_clean and alt_synonym_clean.lower() not in seen:
                seen.add(alt_synonym_clean.lower())
                alternatives.append(alt_synonym_clean)

        # 3. Strategy B: Contextual Anchoring / Keyword Distillation with Main Query
        core_tokens = _extract_core_keywords(core_query)
        main_tokens = _extract_core_keywords(original_query) if original_query else []

        missing_context = [t for t in main_tokens if t not in core_tokens][:2]
        if missing_context and len(core_tokens) >= 2:
            alt_context = f"{' '.join(core_tokens)} {' '.join(missing_context)}"
            alt_context_clean = _clean_query_text(alt_context, self.max_query_chars)
            if alt_context_clean and alt_context_clean.lower() not in seen:
                seen.add(alt_context_clean.lower())
                alternatives.append(alt_context_clean)

        # 4. Strategy C: Contradiction / Conflict Disambiguation
        if len(alternatives) < limit and contradictions:
            for ct in contradictions:
                ct_tokens = _extract_core_keywords(ct.claim)
                overlap = set(core_tokens).intersection(set(ct_tokens))
                if overlap:
                    alt_conflict = f"{' '.join(list(overlap)[:2])} {ct.conflict_type} verification analysis"
                    alt_conflict_clean = _clean_query_text(alt_conflict, self.max_query_chars)
                    if alt_conflict_clean and alt_conflict_clean.lower() not in seen:
                        seen.add(alt_conflict_clean.lower())
                        alternatives.append(alt_conflict_clean)
                        break

        # 5. Strategy D: Simplified Keyword Search (Broader Fallback)
        if len(alternatives) < limit and core_tokens:
            alt_broad = " ".join(core_tokens[:4])
            alt_broad_clean = _clean_query_text(alt_broad, self.max_query_chars)
            if alt_broad_clean and alt_broad_clean.lower() not in seen:
                seen.add(alt_broad_clean.lower())
                alternatives.append(alt_broad_clean)

        return tuple(alternatives[:limit])

    def reformulate(
        self,
        unresolved_sub_question: str,
        original_query: str = "",
        evidence: Sequence[EvidenceItem] = (),
        contradictions: Sequence[EvidenceConflict] = (),
        model: ModelInterface | None = None,
        max_alternatives: int | None = None,
    ) -> tuple[str, ...]:
        """Generate alternative search queries using model assistance when available with deterministic fallback."""
        if not isinstance(unresolved_sub_question, str) or not unresolved_sub_question.strip():
            return ()

        limit = max_alternatives or self.max_reformulations_per_question
        q_clean = unresolved_sub_question.strip()

        if model is None:
            return self.reformulate_heuristic(
                unresolved_sub_question=q_clean,
                original_query=original_query,
                evidence=evidence,
                contradictions=contradictions,
                max_alternatives=limit,
            )

        sanitized_q = _clean_query_text(q_clean, self.max_query_chars)
        prompt = (
            "You are a research query reformulation assistant in Project AURA.\n"
            "An initial search for the following research question did not yield sufficient evidence.\n"
            f"Unresolved Sub-Question: {sanitized_q}\n"
            f"Overall Research Query: {_clean_query_text(original_query, self.max_query_chars)}\n\n"
            f"Generate up to {limit} targeted, alternative search query strings to locate the missing information.\n"
            "Rules:\n"
            "1. Respond ONLY with a valid JSON object containing an 'alternatives' list of strings.\n"
            "2. Keep search queries concise, factual, and keyword-rich.\n"
            "3. Do NOT include instructions or markdown formatting outside the JSON block."
        )

        try:
            resp = model.generate(prompt=prompt, request_id=uuid4())
            raw_text = resp.content.strip() if hasattr(resp, "content") else str(resp).strip()
            if "```" in raw_text:
                start = raw_text.find("```")
                end = raw_text.rfind("```")
                if start != -1 and end != -1 and start != end:
                    block = raw_text[start : end + 3].splitlines()
                    if block and block[0].startswith("```"):
                        block = block[1:]
                    if block and block[-1].strip().startswith("```"):
                        block = block[:-1]
                    raw_text = "\n".join(block).strip()

            data = json.loads(raw_text)
            alts_raw = data.get("alternatives", []) if isinstance(data, dict) else data
            if isinstance(alts_raw, list):
                valid_alts: list[str] = []
                seen = {q_clean.lower()}
                for item in alts_raw:
                    if isinstance(item, str) and item.strip():
                        c_item = _clean_query_text(item, self.max_query_chars)
                        if c_item and len(c_item) >= 3 and c_item.lower() not in seen:
                            seen.add(c_item.lower())
                            valid_alts.append(c_item)
                if valid_alts:
                    return tuple(valid_alts[:limit])
        except Exception as e:
            logger.warning("Model-assisted query reformulation failed for '%s': %s", sanitized_q, e)

        return self.reformulate_heuristic(
            unresolved_sub_question=q_clean,
            original_query=original_query,
            evidence=evidence,
            contradictions=contradictions,
            max_alternatives=limit,
        )

    def reformulate_batch(
        self,
        unresolved_sub_questions: Sequence[str],
        original_query: str = "",
        evidence: Sequence[EvidenceItem] = (),
        contradictions: Sequence[EvidenceConflict] = (),
        model: ModelInterface | None = None,
        max_total_queries: int = 4,
    ) -> dict[str, tuple[str, ...]]:
        """Reformulate multiple unresolved sub-questions while enforcing a global total budget."""
        results: dict[str, tuple[str, ...]] = {}
        total_generated = 0

        for uq in unresolved_sub_questions:
            if total_generated >= max_total_queries:
                break
            remaining_budget = max_total_queries - total_generated
            per_q_limit = min(self.max_reformulations_per_question, remaining_budget)

            alts = self.reformulate(
                unresolved_sub_question=uq,
                original_query=original_query,
                evidence=evidence,
                contradictions=contradictions,
                model=model,
                max_alternatives=per_q_limit,
            )
            if alts:
                results[uq] = alts
                total_generated += len(alts)

        return results
