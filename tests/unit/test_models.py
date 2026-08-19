from uuid import UUID

import pytest
from pydantic import ValidationError

from core.models import AURARequest, AURAResponse


def test_aura_request_defaults():
    request = AURARequest(user_input="Hello AURA")

    assert isinstance(request.request_id, UUID)
    assert request.user_input == "Hello AURA"
    assert request.metadata == {}


def test_aura_request_with_metadata():
    request = AURARequest(
        user_input="Test request",
        metadata={"source": "test"},
    )

    assert request.user_input == "Test request"
    assert request.metadata == {"source": "test"}


def test_aura_request_requires_user_input():
    with pytest.raises(ValidationError):
        AURARequest()


def test_aura_response():
    request = AURARequest(user_input="Hello AURA")

    response = AURAResponse(
        request_id=request.request_id,
        content="Hello! I am AURA.",
    )

    assert response.request_id == request.request_id
    assert response.content == "Hello! I am AURA."
    assert response.metadata == {}


def test_aura_response_with_metadata():
    request = AURARequest(user_input="Test")

    response = AURAResponse(
        request_id=request.request_id,
        content="Test response",
        metadata={"provider": "test"},
    )

    assert response.request_id == request.request_id
    assert response.metadata == {"provider": "test"}

def test_aura_request_with_memory_key():
    request = AURARequest(
        user_input="What is my name?",
        metadata={"memory_key": "user_name"},
    )

    assert request.metadata["memory_key"] == "user_name"
