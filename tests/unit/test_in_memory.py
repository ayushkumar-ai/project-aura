import pytest

from memory.in_memory import InMemoryStore


def test_in_memory_store_and_retrieve():
    memory = InMemoryStore()

    memory.store("user_name", "AURA")

    assert memory.retrieve("user_name") == "AURA"


def test_in_memory_missing_key():
    memory = InMemoryStore()

    assert memory.retrieve("missing") is None


def test_in_memory_multiple_values():
    memory = InMemoryStore()

    memory.store("name", "AURA")
    memory.store("mode", "development")

    assert memory.retrieve("name") == "AURA"
    assert memory.retrieve("mode") == "development"


def test_in_memory_store_rejects_empty_or_non_string_key():
    memory = InMemoryStore()

    with pytest.raises(ValueError):
        memory.store("", "val")

    with pytest.raises(ValueError):
        memory.store("   ", "val")

    with pytest.raises(ValueError):
        memory.store(None, "val")

    with pytest.raises(ValueError):
        memory.store(123, "val")


def test_in_memory_store_rejects_non_string_value():
    memory = InMemoryStore()

    with pytest.raises(ValueError):
        memory.store("key", None)

    with pytest.raises(ValueError):
        memory.store("key", 123)


def test_in_memory_retrieve_handles_empty_or_non_string_key():
    memory = InMemoryStore()

    assert memory.retrieve("") is None
    assert memory.retrieve("   ") is None
    assert memory.retrieve(None) is None
    assert memory.retrieve(123) is None
