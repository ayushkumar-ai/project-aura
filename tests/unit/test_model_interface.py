from uuid import UUID, uuid4

import pytest

from core.models import AURAResponse
from interfaces.model import ModelInterface


def test_model_interface_requires_generate():
    with pytest.raises(TypeError):
        ModelInterface()


class FakeModel(ModelInterface):
    """Minimal model implementation used only for testing."""

    def generate(self, prompt: str, request_id: UUID) -> AURAResponse:
        return AURAResponse(
            request_id=request_id,
            content=f"Fake response to: {prompt}",
        )


def test_valid_model_implementation():
    model = FakeModel()
    request_id = uuid4()

    response = model.generate("Hello AURA", request_id)

    assert isinstance(response, AURAResponse)
    assert isinstance(response.request_id, UUID)
    assert response.request_id == request_id
    assert response.content == "Fake response to: Hello AURA"