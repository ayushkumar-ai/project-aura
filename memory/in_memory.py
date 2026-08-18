from interfaces.memory import MemoryInterface


class InMemoryStore(MemoryInterface):
    """Simple in-process memory implementation for AURA."""

    def __init__(self):
        self.data: dict[str, str] = {}

    def store(self, key: str, value: str) -> None:
        self.data[key] = value

    def retrieve(self, key: str) -> str | None:
        return self.data.get(key)