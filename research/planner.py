import json
import logging
import re
from typing import Any
from uuid import uuid4

from interfaces.model import ModelInterface
from research.models import ResearchSubQuestion

logger = logging.getLogger("aura.research.planner")


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


class ResearchPlanner:
    """Decomposes complex research queries into bounded, orthogonal sub-questions."""

    def __init__(self, max_sub_questions: int = 3):
        if max_sub_questions <= 0:
            raise ValueError("max_sub_questions must be a positive integer.")
        self.max_sub_questions = max_sub_questions

    def should_decompose(self, query: str) -> bool:
        """Determine if a query is complex and would benefit from sub-question decomposition."""
        q_clean = query.strip().lower()
        if len(q_clean.split()) < 4:
            return False

        decomposition_cues = [
            " vs ", " versus ", "compare", "comparison", "difference between",
            "pros and cons", "advantages and disadvantages", "performance, ",
            "cost, ", "efficiency", "tradeoffs", "and also", "as well as"
        ]
        return any(cue in q_clean for cue in decomposition_cues) or ("," in q_clean and " and " in q_clean)

    def decompose_heuristic(self, query: str, max_questions: int | None = None) -> tuple[ResearchSubQuestion, ...]:
        """Deterministically decompose query into orthogonal sub-questions using rule-based parsing."""
        effective_limit = max_questions or self.max_sub_questions
        q_clean = query.strip()

        sub_questions: list[ResearchSubQuestion] = []
        seen_queries: set[str] = set()

        # Check for comparison patterns (e.g., "X vs Y" or "compare X and Y")
        compare_match = re.search(r"(?:compare\s+)?([\w\s-]+?)\s+(?:vs\.?|versus|and)\s+([\w\s-]+?)(?:\s+on|\s+in terms of|\s+regarding|\s*$)", q_clean, re.IGNORECASE)
        aspects_match = re.search(r"(?:on|in terms of|regarding|for)\s+(.+)$", q_clean, re.IGNORECASE)

        if compare_match:
            item_a = compare_match.group(1).strip()
            item_b = compare_match.group(2).strip()
            aspects_str = aspects_match.group(1).strip() if aspects_match else ""

            if aspects_str:
                # Split aspects on commas or 'and'
                aspects = [a.strip() for a in re.split(r",|\band\b", aspects_str) if a.strip()]
                for asp in aspects:
                    sub_q = f"{item_a} vs {item_b} {asp}".strip()
                    if sub_q.lower() not in seen_queries:
                        seen_queries.add(sub_q.lower())
                        sub_questions.append(
                            ResearchSubQuestion(
                                sub_question_id=f"sub_q_{len(sub_questions)+1}",
                                query=sub_q,
                                rationale=f"Comparison of {item_a} and {item_b} on {asp}",
                            )
                        )
                        if len(sub_questions) >= effective_limit:
                            break
            else:
                for target_item in (item_a, item_b, f"{item_a} vs {item_b}"):
                    if target_item.lower() not in seen_queries:
                        seen_queries.add(target_item.lower())
                        sub_questions.append(
                            ResearchSubQuestion(
                                sub_question_id=f"sub_q_{len(sub_questions)+1}",
                                query=target_item,
                                rationale=f"Investigation of {target_item}",
                            )
                        )
                        if len(sub_questions) >= effective_limit:
                            break

        # Fallback if no specific comparison pattern matched
        if not sub_questions:
            # Check for multiple comma-separated clauses
            parts = [p.strip() for p in re.split(r",|\band also\b", q_clean) if len(p.strip().split()) >= 2]
            if len(parts) >= 2:
                for part in parts[:effective_limit]:
                    if part.lower() not in seen_queries:
                        seen_queries.add(part.lower())
                        sub_questions.append(
                            ResearchSubQuestion(
                                sub_question_id=f"sub_q_{len(sub_questions)+1}",
                                query=part,
                                rationale=f"Investigation of sub-topic: {part}",
                            )
                        )
            else:
                # Single sub-question identical to main query
                sub_questions.append(
                    ResearchSubQuestion(
                        sub_question_id="sub_q_1",
                        query=q_clean,
                        rationale="Primary query focus",
                    )
                )

        return tuple(sub_questions[:effective_limit])

    def decompose(
        self,
        query: str,
        model: ModelInterface | None = None,
        max_questions: int | None = None,
    ) -> tuple[ResearchSubQuestion, ...]:
        """Decompose complex query using model if available, falling back to deterministic heuristic."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Query must be a non-empty string.")

        effective_limit = max_questions or self.max_sub_questions

        if model is None:
            return self.decompose_heuristic(query, max_questions=effective_limit)

        prompt = (
            "You are a research planning assistant in Project AURA.\n"
            "Decompose the following complex research question into a bounded list of distinct, targeted sub-questions.\n\n"
            f"Research Question: {query.strip()}\n"
            f"Maximum Sub-Questions Allowed: {effective_limit}\n\n"
            "Rules:\n"
            "1. Output valid JSON only.\n"
            "2. Return an object with a 'sub_questions' list.\n"
            "3. Each entry must have 'query' (string) and 'rationale' (string).\n"
            "4. Sub-questions must be orthogonal and cover specific dimensions.\n"
            "5. Do NOT include any commentary outside the JSON."
        )

        try:
            resp = model.generate(prompt=prompt, request_id=uuid4())
            raw_json = _extract_json_block(resp.content)
            data = json.loads(raw_json)

            raw_list = data.get("sub_questions", []) if isinstance(data, dict) else data
            if not isinstance(raw_list, list) or not raw_list:
                return self.decompose_heuristic(query, max_questions=effective_limit)

            sub_questions: list[ResearchSubQuestion] = []
            seen_queries: set[str] = set()

            for idx, item in enumerate(raw_list):
                if isinstance(item, dict) and "query" in item:
                    sq_text = str(item["query"]).strip()
                    sq_rat = str(item.get("rationale", "")).strip()
                    if sq_text and sq_text.lower() not in seen_queries:
                        seen_queries.add(sq_text.lower())
                        sub_questions.append(
                            ResearchSubQuestion(
                                sub_question_id=f"sub_q_{len(sub_questions)+1}",
                                query=sq_text,
                                rationale=sq_rat or f"Sub-question {idx+1}",
                            )
                        )
                        if len(sub_questions) >= effective_limit:
                            break

            if sub_questions:
                return tuple(sub_questions)

        except Exception as e:
            logger.warning("Model-assisted decomposition failed for '%s': %s", query, e)

        return self.decompose_heuristic(query, max_questions=effective_limit)
