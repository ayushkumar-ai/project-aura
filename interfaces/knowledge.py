from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class KnowledgeRecord:
    content: str
    source: str = "default"


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
