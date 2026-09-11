from __future__ import annotations

from typing import Any

from core.models import AURARequest, AURAResponse
from evaluation.engine import EvaluationEngine
from evaluation.models import EvaluationReport, EvaluationResult


class Evaluator:
    """Evaluator supporting both legacy request-response evaluation and comprehensive multi-dimensional grading."""

    def __init__(self, engine: EvaluationEngine | None = None):
        self.engine = engine or EvaluationEngine()

    def evaluate(
        self,
        request: AURARequest,
        response: AURAResponse,
    ) -> EvaluationResult:
        """Evaluate whether the response is valid for the request (M6 Backward Compatibility)."""
        if request.request_id != response.request_id:
            return EvaluationResult(
                passed=False,
                score=0.0,
                details={"reason": "request ID mismatch"},
            )

        if not response.content.strip():
            return EvaluationResult(
                passed=False,
                score=0.0,
                details={"reason": "empty response"},
            )

        return EvaluationResult(
            passed=True,
            score=1.0,
            details={"reason": "response is valid"},
        )

    def evaluate_report(
        self,
        target: Any,
        target_id: str | None = None,
        target_type: str | None = None,
        context: dict[str, Any] | None = None,
        expected_criteria: tuple[str, ...] | list[str] = (),
    ) -> EvaluationReport:
        """Generate a comprehensive multi-dimensional evaluation report."""
        return self.engine.evaluate(
            target=target,
            target_id=target_id,
            target_type=target_type,
            context=context,
            expected_criteria=expected_criteria,
        )