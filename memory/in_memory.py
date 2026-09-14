from typing import Any
from interfaces.memory import MemoryInterface
from core.agent_memory import AgentMemoryStore
from core.memory_types import MemoryEntry, MemoryTier, MemoryNamespace


class InMemoryStore(MemoryInterface):
    """Simple in-process memory implementation for AURA with optional AgentMemoryStore bridging and user isolation."""

    def __init__(self, agent_memory_store: AgentMemoryStore | None = None):
        self.data: dict[str, str] = {}
        self.agent_memory_store = agent_memory_store

    def store(self, key: str, value: str, user_id: str | None = None) -> None:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Memory key must be a non-empty string.")
        if not isinstance(value, str):
            raise ValueError("Memory value must be a string.")
        
        self.data[key] = value
        if user_id and str(user_id).strip():
            scoped_key = f"{str(user_id).strip()}:{key}"
            self.data[scoped_key] = value

        if self.agent_memory_store is not None:
            entry = MemoryEntry(
                tier=MemoryTier.SEMANTIC,
                namespace=f"user_{user_id}" if user_id else MemoryNamespace.USER_PROFILE.value,
                key=key,
                value=value,
            )
            self.agent_memory_store.store(entry)

    def retrieve(self, key: str, user_id: str | None = None) -> str | None:
        if not isinstance(key, str) or not key.strip():
            return None
        
        if user_id and str(user_id).strip():
            scoped_key = f"{str(user_id).strip()}:{key}"
            if scoped_key in self.data:
                return self.data[scoped_key]

        val = self.data.get(key)
        if val is not None:
            return val
        if self.agent_memory_store is not None:
            entry = self.agent_memory_store.get_by_key(
                tier=MemoryTier.SEMANTIC,
                namespace=f"user_{user_id}" if user_id else MemoryNamespace.USER_PROFILE.value,
                key=key,
            )
            if entry is not None and isinstance(entry.value, str):
                return entry.value
        return None
