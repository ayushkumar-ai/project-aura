from abc import ABC, abstractmethod

from core.models import AURAResponse


class ModelInterface(ABC):
    """Contract for an AURA model provider."""

    @abstractmethod
    def generate(self, prompt: str) -> AURAResponse:
        """Generate a response from a prompt."""
        raise NotImplementedError