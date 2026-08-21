from uuid import UUID

from core.context import AURAContext
from core.models import AURARequest


def test_aura_context_defaults():
    request = AURARequest(user_input="Hello AURA")

    context = AURAContext(
        request=request,
        request_id=request.request_id,
    )

    assert context.request == request
    assert isinstance(context.request_id, UUID)
    assert context.request_id == request.request_id
    assert context.state == {}


def test_aura_context_with_state():
    request = AURARequest(user_input="Test")

    context = AURAContext(
        request=request,
        request_id=request.request_id,
        state={"phase": "testing"},
    )

    assert context.state == {"phase": "testing"}


def test_aura_context_with_multiple_state_values():
    request = AURARequest(user_input="Hello AURA")

    context = AURAContext(
        request=request,
        request_id=request.request_id,
        state={
            "memory": "AURA",
            "history": "Previous conversation",
        },
    )

    assert context.state == {
        "memory": "AURA",
        "history": "Previous conversation",
    }


def test_aura_context_has_empty_history_by_default():
    request = AURARequest(user_input="Hello AURA")

    context = AURAContext(
        request=request,
        request_id=request.request_id,
    )

    assert context.history.turns == []