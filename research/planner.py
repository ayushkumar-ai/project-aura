import logging
from interfaces.model import ModelInterface
from research.decomposition import QueryDecomposer, should_decompose_query
from research.models import ResearchSubQuestion

logger = logging.getLogger("aura.research.planner")


class ResearchPlanner:
    """Decomposes complex research queries into bounded, orthogonal sub-questions."""

    def __init__(self, max_sub_questions: int = 3):
        if max_sub_questions <= 0:
            raise ValueError("max_sub_questions must be a positive integer.")
        self.max_sub_questions = max_sub_questions
        self._decomposer = QueryDecomposer(max_sub_questions=max_sub_questions)

    def should_decompose(self, query: str) -> bool:
        """Determine if a query is complex and would benefit from sub-question decomposition."""
        return self._decomposer.should_decompose(query)

    def decompose_heuristic(self, query: str, max_questions: int | None = None) -> tuple[ResearchSubQuestion, ...]:
        """Deterministically decompose query into orthogonal sub-questions using rule-based parsing."""
        return self._decomposer.decompose_heuristic(query, max_questions=max_questions)

    def decompose(
        self,
        query: str,
        model: ModelInterface | None = None,
        max_questions: int | None = None,
    ) -> tuple[ResearchSubQuestion, ...]:
        """Decompose complex query using model if available, falling back to deterministic heuristic."""
        return self._decomposer.decompose(
            query=query,
            model=model,
            max_questions=max_questions,
            force_decompose=True,  # ResearchPlanner.decompose historically always decomposed if called
        )
