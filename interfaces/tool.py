import re
from abc import ABC, abstractmethod


class ToolInterface(ABC):
    """Contract for tools that can be invoked by AURA."""

    @property
    def description(self) -> str:
        """Return a human-readable description of the tool."""
        name = self.__class__.__name__
        words = re.sub(r"(?<!^)(?=[A-Z])", " ", name)
        return words.lower().capitalize()
    

    @abstractmethod
    def execute(self, input_data: str) -> str:
        """Execute the tool with the supplied input."""
        raise NotImplementedError