import time
import pytest
from core.memory_types import (
    MemoryTier,
    MemoryNamespace,
    MemoryEntry,
    SemanticFact,
    EpisodicRecord,
)
from core.agent_memory import (
    InMemoryAgentMemoryStore,
    FileAgentMemoryStore,
)
from core.provenance import TaintedValue, wrap_tainted, is_tainted, unwrap_tainted


def test_in_memory_store_crud():
    store = InMemoryAgentMemoryStore()

    entry = MemoryEntry(
        key="user:theme",
        value="dark_mode",
        tier=MemoryTier.SEMANTIC,
        namespace=MemoryNamespace.USER_PROFILE,
        confidence=0.9,
    )
    saved = store.store(entry)
    assert saved.entry_id == entry.entry_id

    # Retrieve by ID
    fetched = store.get(entry.entry_id)
    assert fetched.key == "user:theme"
    assert fetched.value == "dark_mode"

    # Retrieve by key
    by_key = store.get_by_key(MemoryTier.SEMANTIC, "user_profile", "user:theme")
    assert by_key is not None
    assert by_key.value == "dark_mode"

    # Update existing key
    updated_entry = entry.with_value("light_mode")
    store.store(updated_entry)
    fetched_updated = store.get_by_key(MemoryTier.SEMANTIC, "user_profile", "user:theme")
    assert fetched_updated is not None
    assert fetched_updated.value == "light_mode"

    # Delete
    store.delete(entry.entry_id)
    with pytest.raises(KeyError):
        store.get(entry.entry_id)
    assert store.get_by_key(MemoryTier.SEMANTIC, "user_profile", "user:theme") is None


def test_in_memory_store_ring_buffer_limits():
    store = InMemoryAgentMemoryStore(
        max_working_entries=3,
        max_semantic_facts=3,
        max_episodic_records=3,
    )

    # Store 4 working entries
    for i in range(4):
        store.store(MemoryEntry(
            key=f"scratch_{i}",
            value=f"val_{i}",
            tier=MemoryTier.WORKING,
            namespace="task-1",
        ))

    working_entries = store.list_entries(tier=MemoryTier.WORKING)
    assert len(working_entries) == 3
    keys = {e.key for e in working_entries}
    assert "scratch_0" not in keys  # Oldest pruned
    assert "scratch_1" in keys
    assert "scratch_2" in keys
    assert "scratch_3" in keys


def test_in_memory_store_search_relevance():
    store = InMemoryAgentMemoryStore()

    store.store(MemoryEntry(
        key="finance:budget_2026",
        value="The quarterly budget is 50000 USD for operations.",
        tier=MemoryTier.SEMANTIC,
        namespace="finance",
        confidence=1.0,
    ))
    store.store(MemoryEntry(
        key="weather:san_francisco",
        value="Sunny with 22 degrees Celsius.",
        tier=MemoryTier.SEMANTIC,
        namespace="environment",
        confidence=1.0,
    ))

    # Search for budget
    results = store.search(query="budget quarterly", tier=MemoryTier.SEMANTIC)
    assert len(results) >= 1
    assert results[0].key == "finance:budget_2026"

    # Search for weather
    results_weather = store.search(query="degrees celsius weather", tier=MemoryTier.SEMANTIC)
    assert len(results_weather) >= 1
    assert results_weather[0].key == "weather:san_francisco"


def test_in_memory_store_clear_tier():
    store = InMemoryAgentMemoryStore()

    store.store(MemoryEntry(key="w1", value="1", tier=MemoryTier.WORKING, namespace="t1"))
    store.store(MemoryEntry(key="w2", value="2", tier=MemoryTier.WORKING, namespace="t2"))
    store.store(MemoryEntry(key="s1", value="1", tier=MemoryTier.SEMANTIC, namespace="user"))

    assert len(store.list_entries(tier=MemoryTier.WORKING)) == 2

    # Clear only namespace t1
    store.clear_tier(MemoryTier.WORKING, namespace="t1")
    assert len(store.list_entries(tier=MemoryTier.WORKING)) == 1
    assert store.list_entries(tier=MemoryTier.WORKING)[0].namespace == "t2"

    # Clear all working
    store.clear_tier(MemoryTier.WORKING)
    assert len(store.list_entries(tier=MemoryTier.WORKING)) == 0
    assert len(store.list_entries(tier=MemoryTier.SEMANTIC)) == 1


def test_file_agent_memory_store(tmp_path):
    store = FileAgentMemoryStore(
        storage_dir=tmp_path / "memory",
        max_working_entries=3,
        max_semantic_facts=3,
        max_episodic_records=3,
    )

    tainted_obj = wrap_tainted("untrusted research output", is_untrusted=True, source_urls=["https://news.org"])
    entry = MemoryEntry(
        key="research:ai_news",
        value=tainted_obj,
        tier=MemoryTier.SEMANTIC,
        namespace="research",
        confidence=0.85,
    )

    saved = store.store(entry)
    assert saved.is_untrusted is True

    # Retrieve from disk by ID
    loaded = store.get(saved.entry_id)
    assert loaded.key == "research:ai_news"
    assert loaded.is_untrusted is True
    assert isinstance(loaded.value, TaintedValue)
    assert loaded.value.raw_value == "untrusted research output"
    assert loaded.value.source_urls == ("https://news.org",)

    # Retrieve by key from disk
    by_key = store.get_by_key(MemoryTier.SEMANTIC, "research", "research:ai_news")
    assert by_key is not None
    assert by_key.entry_id == saved.entry_id

    # Test file-based ring buffer
    for i in range(4):
        store.store(MemoryEntry(
            key=f"task_scratch_{i}",
            value=f"data_{i}",
            tier=MemoryTier.WORKING,
            namespace="task_run",
        ))

    working_entries = store.list_entries(tier=MemoryTier.WORKING)
    assert len(working_entries) == 3

    # Delete
    store.delete(saved.entry_id)
    with pytest.raises(KeyError):
        store.get(saved.entry_id)
