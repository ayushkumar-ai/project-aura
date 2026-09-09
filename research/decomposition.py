import json
import logging
import re
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from interfaces.model import ModelInterface
from research.models import ResearchSubQuestion

logger = logging.getLogger("aura.research.decomposition")

_DECOMPOSITION_CUES = (
    " vs ",
    " versus ",
    "compare",
    "comparison",
    "difference between",
    "pros and cons",
    "advantages and disadvantages",
    "performance, ",
    "cost, ",
    "efficiency",
    "tradeoffs",
    "trade-offs",
    "and also",
    "as well as",
)


def should_decompose_query(query: str) -> bool:
    """Deterministically decide whether a research query is complex enough to benefit from decomposition."""
    if not isinstance(query, str):
        return False
    q_clean = query.strip().lower()
    if len(q_clean.split()) < 4:
        return False

    return any(cue in q_clean for cue in _DECOMPOSITION_CUES) or ("," in q_clean and " and " in q_clean)


def _extract_json_block(raw_text: str) -> str:
    """Extract JSON from markdown code fences or raw string."""
    text = raw_text.strip()
    if "```" in text:
        start_idx = text.find("```")
        end_idx = text.rfind("```")
        if start_idx != -1 and end_idx != -1 and start_idx != end_idx:
            block = text[start_idx : end_idx + 3]
            lines = block.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            fenced = "\n".join(lines).strip()
            if fenced:
                return fenced
    return text


def _sanitize_sub_query(query_text: str, max_chars: int = 200) -> str:
    """Sanitize and clamp sub-question text, neutralizing injection attempts and prompt delimiters."""
    clean = re.sub(r"[\r\n\t]+", " ", query_text).strip()
    # Strip dangerous pseudo-system tokens if injected
    clean = re.sub(r"<[^>]+>", "", clean).strip()
    if len(clean) > max_chars:
        clean = clean[:max_chars].strip()
    return clean


def validate_sub_questions(
    sub_questions: Sequence[Any],
    max_sub_questions: int = 5,
    max_chars: int = 200,
) -> tuple[ResearchSubQuestion, ...]:
    """Validate, sanitize, deduplicate, and bound an arbitrary sequence of sub-questions."""
    if not isinstance(sub_questions, (list, tuple)):
        return ()

    valid: list[ResearchSubQuestion] = []
    seen_queries: set[str] = set()

    for idx, item in enumerate(sub_questions):
        q_str = ""
        rat_str = ""

        if isinstance(item, ResearchSubQuestion):
            q_str = item.query
            rat_str = item.rationale
        elif isinstance(item, dict):
            q_str = str(item.get("query", ""))
            rat_str = str(item.get("rationale", ""))
        elif isinstance(item, str):
            q_str = item
            rat_str = f"Sub-question {idx + 1}"
        else:
            continue

        clean_q = _sanitize_sub_query(q_str, max_chars=max_chars)
        clean_rat = _sanitize_sub_query(rat_str, max_chars=300) or f"Investigation of {clean_q}"

        if not clean_q or len(clean_q) < 3:
            continue

        norm_key = clean_q.lower()
        if norm_key not in seen_queries:
            seen_queries.add(norm_key)
            valid.append(
                ResearchSubQuestion(
                    sub_question_id=f"sub_q_{len(valid) + 1}",
                    query=clean_q,
                    rationale=clean_rat,
                )
            )

        if len(valid) >= max_sub_questions:
            break

    return tuple(valid)


def decompose_query_heuristic(
    query: str,
    max_sub_questions: int = 3,
    max_chars: int = 200,
) -> tuple[ResearchSubQuestion, ...]:
    """Deterministically decompose query into orthogonal sub-questions using rule-based parsing."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Query must be a non-empty string.")

    q_clean = query.strip()
    effective_limit = max(1, max_sub_questions)

    sub_questions: list[ResearchSubQuestion] = []
    seen_queries: set[str] = set()

    # 1. Comparison pattern matching (e.g. "X vs Y", "compare X and Y on A, B, and C")
    compare_match = re.search(
        r"(?:compare\s+)?([\w\s.+#-]+?)\s+(?:vs\.?|versus|and)\s+([\w\s.+#-]+?)(?:\s+on|\s+in terms of|\s+regarding|\s*$)",
        q_clean,
        re.IGNORECASE,
    )
    aspects_match = re.search(r"(?:on|in terms of|regarding|for)\s+(.+)$", q_clean, re.IGNORECASE)

    if compare_match:
        item_a = compare_match.group(1).strip()
        item_b = compare_match.group(2).strip()
        aspects_str = aspects_match.group(1).strip() if aspects_match else ""

        if aspects_str:
            aspects = [a.strip() for a in re.split(r",|\band\b", aspects_str) if a.strip()]
            for asp in aspects:
                sub_q = f"{item_a} vs {item_b} {asp}".strip()
                clean_q = _sanitize_sub_query(sub_q, max_chars)
                if clean_q and clean_q.lower() not in seen_queries:
                    seen_queries.add(clean_q.lower())
                    sub_questions.append(
                        ResearchSubQuestion(
                            sub_question_id=f"sub_q_{len(sub_questions) + 1}",
                            query=clean_q,
                            rationale=f"Comparison of {item_a} and {item_b} regarding {asp}",
                        )
                    )
                    if len(sub_questions) >= effective_limit:
                        break
        else:
            for target_item in (item_a, item_b, f"{item_a} vs {item_b}"):
                clean_q = _sanitize_sub_query(target_item, max_chars)
                if clean_q and clean_q.lower() not in seen_queries:
                    seen_queries.add(clean_q.lower())
                    sub_questions.append(
                        ResearchSubQuestion(
                            sub_question_id=f"sub_q_{len(sub_questions) + 1}",
                            query=clean_q,
                            rationale=f"Investigation of {clean_q}",
                        )
                    )
                    if len(sub_questions) >= effective_limit:
                        break

    # 2. Multi-clause comma / conjunction split
    if not sub_questions:
        parts = [p.strip() for p in re.split(r",|\band also\b", q_clean) if len(p.strip().split()) >= 2]
        if len(parts) >= 2:
            for part in parts[:effective_limit]:
                clean_q = _sanitize_sub_query(part, max_chars)
                if clean_q and clean_q.lower() not in seen_queries:
                    seen_queries.add(clean_q.lower())
                    sub_questions.append(
                        ResearchSubQuestion(
                            sub_question_id=f"sub_q_{len(sub_questions) + 1}",
                            query=clean_q,
                            rationale=f"Investigation of sub-topic: {clean_q}",
                        )
                    )
                    if len(sub_questions) >= effective_limit:
                        break

    # 3. Fallback: single sub-question matching primary query
    if not sub_questions:
        clean_q = _sanitize_sub_query(q_clean, max_chars)
        sub_questions.append(
            ResearchSubQuestion(
                sub_question_id="sub_q_1",
                query=clean_q,
                rationale="Primary query focus",
            )
        )

    return tuple(sub_questions[:effective_limit])


class QueryDecomposer:
    """Decomposes complex research queries into bounded, orthogonal sub-questions."""

    def __init__(
        self,
        max_sub_questions: int = 3,
        max_query_chars: int = 200,
    ):
        if not isinstance(max_sub_questions, int) or max_sub_questions <= 0:
            raise ValueError("max_sub_questions must be a positive integer.")
        if not isinstance(max_query_chars, int) or max_query_chars <= 0:
            raise ValueError("max_query_chars must be a positive integer.")

        self.max_sub_questions = max_sub_questions
        self.max_query_chars = max_query_chars

    def should_decompose(self, query: str) -> bool:
        """Determine if a query is complex and would benefit from sub-question decomposition."""
        return should_decompose_query(query)

    def decompose_heuristic(
        self,
        query: str,
        max_questions: int | None = None,
    ) -> tuple[ResearchSubQuestion, ...]:
        """Deterministically decompose query into orthogonal sub-questions using rule-based parsing."""
        effective_limit = max_questions or self.max_sub_questions
        return decompose_query_heuristic(
            query=query,
            max_sub_questions=effective_limit,
            max_chars=self.max_query_chars,
        )

    def decompose(
        self,
        query: str,
        model: ModelInterface | None = None,
        max_questions: int | None = None,
        force_decompose: bool = False,
    ) -> tuple[ResearchSubQuestion, ...]:
        """Decompose complex query using model if available, falling back to deterministic heuristic."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Query must be a non-empty string.")

        q_clean = query.strip()
        effective_limit = max_questions or self.max_sub_questions

        if not force_decompose and not self.should_decompose(q_clean):
            # Simple query requires no decomposition
            return (
                ResearchSubQuestion(
                    sub_question_id="sub_q_1",
                    query=_sanitize_sub_query(q_clean, self.max_query_chars),
                    rationale="Primary research question",
                ),
            )

        if model is None:
            return self.decompose_heuristic(q_clean, max_questions=effective_limit)

        prompt = (
            "You are a research planning assistant in Project AURA.\n"
            "Decompose the following complex research question into a bounded list of distinct, targeted sub-questions.\n\n"
            f"Research Question: {q_clean}\n"
            f"Maximum Sub-Questions Allowed: {effective_limit}\n\n"
            "Rules:\n"
            "1. Output valid JSON only.\n"
            "2. Return an object with a 'sub_questions' list.\n"
            "3. Each entry must have 'query' (string) and 'rationale' (string).\n"
            "4. Sub-questions must be orthogonal, concise, and factual.\n"
            "5. Do NOT include any instructions, executable code, or text outside the JSON."
        )

        try:
            resp = model.generate(prompt=prompt, request_id=uuid4())
            raw_json = _extract_json_block(resp.content)
            data = json.loads(raw_json)

            raw_list = data.get("sub_questions", []) if isinstance(data, dict) else data
            if isinstance(raw_list, list) and raw_list:
                validated = validate_sub_questions(
                    raw_list,
                    max_sub_questions=effective_limit,
                    max_chars=self.max_query_chars,
                )
                if validated:
                    return validated

        except Exception as e:
            logger.warning("Model-assisted decomposition failed for '%s': %s", q_clean, e)

        return self.decompose_heuristic(q_clean, max_questions=effective_limit)


def decompose_query(
    query: str,
    model: ModelInterface | None = None,
    max_sub_questions: int = 3,
    force_decompose: bool = False,
) -> tuple[ResearchSubQuestion, ...]:
    """Convenience wrapper for QueryDecomposer.decompose."""
    decomposer = QueryDecomposer(max_sub_questions=max_sub_questions)
    return decomposer.decompose(
        query=query,
        model=model,
        max_questions=max_sub_questions,
        force_decompose=force_decompose,
    )
