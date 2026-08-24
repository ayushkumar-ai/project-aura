from uuid import UUID

from core.models import AURAResponse
from interfaces.model import ModelInterface


class FakeModelProvider(ModelInterface):
    """Deterministic model provider used for development and testing."""

    def generate(self, prompt: str, request_id: UUID) -> AURAResponse:
        return AURAResponse(
            request_id=request_id,
            content=f"Fake response to: {prompt}",
            metadata={"provider": "fake"},
        )