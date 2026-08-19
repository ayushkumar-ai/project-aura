from core.models import AURARequest, AURAResponse
from evaluation.models import EvaluationResult


class Evaluator:
    """Evaluates the basic quality of an AURA response."""

    def evaluate(
        self,
        request: AURARequest,
        response: AURAResponse,
    ) -> EvaluationResult:
        """Evaluate whether the response contains usable content."""

        if not response.content.strip():
            return EvaluationResult(
                passed=False,
                score=0.0,
                details={"reason": "empty response"},
            )

        return EvaluationResult(
            passed=True,
            score=1.0,
            details={"reason": "response contains content"},
        )