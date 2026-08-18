from uuid import UUID, uuid4

import pytest

from core.models import AURAResponse
from interfaces.model import ModelInterface


def test_model_interface_requires_generate():
    with pytest.raises(TypeError):
        ModelInterface()


class FakeModel(ModelInterface):
    """Minimal model implementation used only for testing."""

    def generate(self, prompt: str) -> AURAResponse:
        return AURAResponse(
            request_id=uuid4(),
            content=f"Fake response to: {prompt}",
        )


def test_valid_model_implementation():
    model = FakeModel()

    response = model.generate("Hello AURA")

    assert isinstance(response, AURAResponse)
    assert isinstance(response.request_id, UUID)
    assert response.content == "Fake response to: Hello AURA"