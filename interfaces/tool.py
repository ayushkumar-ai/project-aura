from abc import ABC, abstractmethod


class ToolInterface(ABC):
    """Contract for tools that can be invoked by AURA."""

    @abstractmethod
    def execute(self, input_data: str) -> str:
        """Execute the tool with the supplied input."""
        raise NotImplementedError