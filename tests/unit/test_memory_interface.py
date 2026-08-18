import pytest

from interfaces.memory import MemoryInterface


def test_memory_interface_requires_methods():
    with pytest.raises(TypeError):
        MemoryInterface()


class FakeMemory(MemoryInterface):
    """Minimal memory implementation used only for testing."""

    def __init__(self):
        self.data = {}

    def store(self, key: str, value: str) -> None:
        self.data[key] = value

    def retrieve(self, key: str) -> str | None:
        return self.data.get(key)


def test_valid_memory_implementation():
    memory = FakeMemory()

    memory.store("user_name", "AURA")

    assert memory.retrieve("user_name") == "AURA"
    assert memory.retrieve("missing") is None