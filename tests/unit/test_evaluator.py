from uuid import uuid4

from core.models import AURAResponse
from evaluation.evaluator import Evaluator


def test_evaluator_accepts_non_empty_response():
    response = AURAResponse(
        request_id=uuid4(),
        content="Hello AURA.",
    )

    result = Evaluator().evaluate(response)

    assert result.passed is True
    assert result.score == 1.0
    assert result.details == {
        "reason": "response contains content",
    }


def test_evaluator_rejects_empty_response():
    response = AURAResponse(
        request_id=uuid4(),
        content="   ",
    )

    result = Evaluator().evaluate(response)

    assert result.passed is False
    assert result.score == 0.0
    assert result.details == {
        "reason": "empty response",
    }