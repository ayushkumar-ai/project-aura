from interfaces.memory import MemoryInterface


class InMemoryStore(MemoryInterface):
    """Simple in-process memory implementation for AURA."""

    def __init__(self):
        self.data: dict[str, str] = {}

    def store(self, key: str, value: str) -> None:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Memory key must be a non-empty string.")
        if not isinstance(value, str):
            raise ValueError("Memory value must be a string.")
        self.data[key] = value

    def retrieve(self, key: str) -> str | None:
        if not isinstance(key, str) or not key.strip():
            return None
        return self.data.get(key)
