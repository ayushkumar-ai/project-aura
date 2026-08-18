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