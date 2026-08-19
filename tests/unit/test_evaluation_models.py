import pytest
from pydantic import ValidationError

from evaluation.models import EvaluationResult


def test_evaluation_result_defaults():
    result = EvaluationResult(
        passed=True,
        score=1.0,
    )

    assert result.passed is True
    assert result.score == 1.0
    assert result.details == {}


def test_evaluation_result_with_details():
    result = EvaluationResult(
        passed=True,
        score=0.8,
        details={"criterion": "response generated"},
    )

    assert result.score == 0.8
    assert result.details == {"criterion": "response generated"}


def test_evaluation_result_rejects_invalid_score():
    with pytest.raises(ValidationError):
        EvaluationResult(
            passed=True,
            score=1.5,
        )