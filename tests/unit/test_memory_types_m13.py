import time
import pytest
from core.memory_types import (
    MemoryTier,
    MemoryNamespace,
    MemoryEntry,
    SemanticFact,
    EpisodicRecord,
    serialize_memory_entry,
    deserialize_memory_entry,
)
from core.provenance import TaintedValue, wrap_tainted, is_tainted, unwrap_tainted


def test_memory_tier_and_namespace_enums():
    assert MemoryTier.WORKING.value == "working"
    assert MemoryTier.SEMANTIC.value == "semantic"
    assert MemoryTier.EPISODIC.value == "episodic"

    assert MemoryNamespace.USER_PROFILE.value == "user_profile"
    assert MemoryNamespace.SYSTEM_FACTS.value == "system_facts"
    assert MemoryNamespace.TASK_SCRATCHPAD.value == "task_scratchpad"
    assert MemoryNamespace.EXECUTION_HISTORY.value == "execution_history"
    assert MemoryNamespace.CUSTOM.value == "custom"


def test_memory_entry_creation_and_immutability():
    entry = MemoryEntry(
        key="user_name",
        value="Alice",
        tier=MemoryTier.SEMANTIC,
        namespace=MemoryNamespace.USER_PROFILE,
        confidence=0.95,
        metadata={"source": "direct_input"},
    )
    assert entry.key == "user_name"
    assert entry.value == "Alice"
    assert entry.tier == MemoryTier.SEMANTIC
    assert entry.namespace == "user_profile"
    assert entry.confidence == 0.95
    assert entry.is_untrusted is False
    assert entry.metadata == {"source": "direct_input"}

    # Verify immutability
    with pytest.raises(Exception):
        entry.key = "new_key"  # type: ignore


def test_memory_entry_validation():
    # Invalid key
    with pytest.raises(ValueError, match="key must be a non-empty string"):
        MemoryEntry(key="")

    # Invalid tier
    with pytest.raises(TypeError, match="tier must be an instance of MemoryTier"):
        MemoryEntry(key="k", tier=123)  # type: ignore

    # Invalid confidence
    with pytest.raises(TypeError, match="confidence must be a number"):
        MemoryEntry(key="k", confidence="high")  # type: ignore

    # Confidence clamped
    e1 = MemoryEntry(key="k", confidence=1.5)
    assert e1.confidence == 1.0
    e2 = MemoryEntry(key="k", confidence=-0.5)
    assert e2.confidence == 0.0

    # Callable value rejected
    with pytest.raises(ValueError, match="value cannot be callable"):
        MemoryEntry(key="k", value=lambda: "secret")


def test_memory_entry_taint_propagation():
    tainted_val = wrap_tainted("untrusted web data", is_untrusted=True, source_urls=["https://example.com"])
    entry = MemoryEntry(
        key="research_fact",
        value=tainted_val,
    )
    assert entry.is_untrusted is True
    assert "https://example.com" in entry.source_urls


def test_memory_entry_serialization_roundtrip():
    tainted_val = wrap_tainted("malicious instruction", is_untrusted=True, source_type="web", source_urls=["http://malicious.org"])
    entry = MemoryEntry(
        key="profile_note",
        value=tainted_val,
        tier=MemoryTier.SEMANTIC,
        namespace="notes",
        confidence=0.8,
        expires_at=time.time() + 3600,
        metadata={"tag": "research", "approved": True},  # 'approved' should be sanitized out
    )
    assert "approved" not in entry.metadata

    serialized = serialize_memory_entry(entry)
    assert serialized["key"] == "profile_note"
    assert serialized["is_untrusted"] is True
    assert serialized["value"]["__tainted__"] is True

    deserialized = deserialize_memory_entry(serialized)
    assert deserialized.entry_id == entry.entry_id
    assert deserialized.key == entry.key
    assert deserialized.tier == entry.tier
    assert deserialized.namespace == entry.namespace
    assert deserialized.confidence == entry.confidence
    assert deserialized.is_untrusted is True
    assert isinstance(deserialized.value, TaintedValue)
    assert deserialized.value.raw_value == "malicious instruction"
    assert deserialized.value.source_urls == ("http://malicious.org",)


def test_memory_entry_expiry():
    now = time.time()
    e_future = MemoryEntry(key="k", expires_at=now + 100)
    assert e_future.is_expired(current_time=now) is False

    e_past = MemoryEntry(key="k", expires_at=now - 10)
    assert e_past.is_expired(current_time=now) is True

    e_no_expiry = MemoryEntry(key="k", expires_at=None)
    assert e_no_expiry.is_expired(current_time=now) is False


def test_semantic_fact_model():
    fact = SemanticFact(
        subject="user",
        predicate="preferred_units",
        object_value="celsius",
        confidence=1.0,
    )
    assert fact.key == "user:preferred_units"
    assert fact.is_untrusted is False

    entry = fact.to_memory_entry(namespace=MemoryNamespace.USER_PROFILE)
    assert entry.tier == MemoryTier.SEMANTIC
    assert entry.namespace == "user_profile"
    assert entry.key == "user:preferred_units"
    assert entry.value == "celsius"
    assert entry.metadata["subject"] == "user"
    assert entry.metadata["predicate"] == "preferred_units"


def test_episodic_record_model():
    episode = EpisodicRecord(
        task_id="task-123",
        plan_id="plan-456",
        task_goal="Calculate quarterly profit",
        success=True,
        executed_skills=("calculator", "echo"),
        execution_time_ms=150.0,
        trace_summary={"step_count": 2, "replanned": False},
    )
    assert episode.task_id == "task-123"
    assert episode.success is True

    entry = episode.to_memory_entry()
    assert entry.tier == MemoryTier.EPISODIC
    assert entry.namespace == "execution_history"
    assert entry.key == "episode:task-123"
    assert entry.value["task_goal"] == "Calculate quarterly profit"
    assert entry.value["executed_skills"] == ["calculator", "echo"]
    assert entry.metadata["plan_id"] == "plan-456"
