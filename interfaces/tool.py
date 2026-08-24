import re
from abc import ABC, abstractmethod


class ToolInterface(ABC):
    """Contract for tools that can be invoked by AURA."""

    @property
    def name(self) -> str:
        """Return the tool's canonical name."""
        class_name = self.__class__.__name__

        if class_name.endswith("Tool"):
            class_name = class_name[:-4]

        words = re.sub(r"(?<!^)(?=[A-Z])", " ", class_name)

        return words.lower()

    @property
    def description(self) -> str:
        """Return a human-readable description of the tool."""
        name = self.__class__.__name__
        words = re.sub(r"(?<!^)(?=[A-Z])", " ", name)
        return words.lower().capitalize()

    @property
    def keywords(self) -> tuple[str, ...]:
        """Return keywords that can indicate this tool's intent."""
        return ()

    @abstractmethod
    def execute(self, input_data: str) -> str:
        """Execute the tool with the supplied input."""
        raise NotImplementedError