from abc import ABC, abstractmethod


class MemoryInterface(ABC):
    """Contract for AURA memory providers."""

    @abstractmethod
    def store(self, key: str, value: str) -> None:
        """Store a value using a key."""
        raise NotImplementedError

    @abstractmethod
    def retrieve(self, key: str) -> str | None:
        """Retrieve a value by key."""
        raise NotImplementedError