from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class KnowledgeRecord:
    content: str
    source: str = "default"

    def __post_init__(self):
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("KnowledgeRecord content must be a non-empty string.")
        if not isinstance(self.source, str) or not self.source.strip():
            object.__setattr__(self, "source", "default")


class KnowledgeInterface(ABC):
    """Contract for AURA knowledge retrieval backends."""

    @abstractmethod
    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
    ) -> list[KnowledgeRecord]:
        """Retrieve relevant knowledge records for a query."""
        raise NotImplementedError
