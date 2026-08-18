from uuid import uuid4

from core.models import AURAResponse
from interfaces.model import ModelInterface


class FakeModelProvider(ModelInterface):
    """Deterministic model provider used for development and testing."""

    def generate(self, prompt: str) -> AURAResponse:
        return AURAResponse(
            request_id=uuid4(),
            content=f"Fake response to: {prompt}",
            metadata={"provider": "fake"},
        )