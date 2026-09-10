"""
Unit tests for Milestone 15 core/memory_lifecycle.py.
Tests memory compaction, deduplication, utility-weighted pruning,
taint preservation, and security sanitization across memory stores.
"""

import pytest
import time
from core.agent_memory import InMemoryAgentMemoryStore
from core.lifecycle_types import (
    DecayConfig,
    DecayModel,
    LifecycleAction,
)
from core.memory_lifecycle import MemoryLifecycleManager
from core.memory_types import (
    MemoryEntry,
    MemoryTier,
    MemoryNamespace,
)
from core.provenance import TaintedValue, is_tainted, unwrap_tainted


class TestMemoryCompaction:
    def test_compact_duplicate_keys_merges_confidence_and_urls(self):
        manager = MemoryLifecycleManager()
        now = time.time()
        entries = [
            MemoryEntry(
                key="user_city",
                value="New York",
                namespace="user_profile",
                confidence=0.80,
                is_untrusted=False,
                source_urls=("https://trusted.com/a",),
                created_at=now - 200.0,
                updated_at=now - 100.0,
                metadata={"access_count": 3},
            ),
            MemoryEntry(
                key="user_city",
                value="New York City",
                namespace="user_profile",
                confidence=0.95,
                is_untrusted=True,
                source_urls=("https://untrusted.com/b",),
                created_at=now - 100.0,
                updated_at=now - 10.0,
                metadata={"access_count": 4},
            ),
        ]

        compacted, record = manager.compact_entries(entries, namespace="user_profile", current_time=now)
        assert len(compacted) == 1
        assert record.facts_analyzed == 2
        assert record.facts_merged == 1
        assert record.memory_reclaimed_entries == 1

        merged = compacted[0]
        assert merged.key == "user_city"
        assert merged.confidence == 0.95
        assert merged.is_untrusted is True
        assert "https://trusted.com/a" in merged.source_urls
        assert "https://untrusted.com/b" in merged.source_urls
        assert merged.metadata["access_count"] == 7
        assert merged.created_at == now - 200.0
        assert merged.updated_at == now - 10.0

    def test_compact_preserves_tainted_value(self):
        manager = MemoryLifecycleManager()
        now = time.time()
        tainted_val = TaintedValue(raw_value="Secret Code", is_untrusted=True, source_urls=("https://web.org",))
        entries = [
            MemoryEntry(
                key="secret",
                value=tainted_val,
                namespace="system_facts",
                confidence=0.70,
                created_at=now,
                updated_at=now,
            ),
            MemoryEntry(
                key="secret",
                value="Plain Code",
                namespace="system_facts",
                confidence=0.85,
                created_at=now,
                updated_at=now + 5.0,
            ),
        ]

        compacted, record = manager.compact_entries(entries, namespace="system_facts", current_time=now)
        assert len(compacted) == 1
        merged = compacted[0]
        assert merged.is_untrusted is True
        assert is_tainted(merged.value)
        assert unwrap_tainted(merged.value) == "Plain Code" or unwrap_tainted(merged.value) == "Secret Code"

    def test_compact_sanitizes_forbidden_metadata(self):
        manager = MemoryLifecycleManager()
        now = time.time()
        entries = [
            MemoryEntry(
                key="setting",
                value="dark_mode",
                namespace="user_preferences",
                metadata={"permission": "admin", "approved": True, "theme": "dark"},
                created_at=now,
                updated_at=now,
            )
        ]
        compacted, _ = manager.compact_entries(entries, namespace="user_preferences", current_time=now)
        assert "permission" not in compacted[0].metadata
        assert "approved" not in compacted[0].metadata
        assert compacted[0].metadata.get("theme") == "dark"


class TestMemoryPruningAndLifecyclePass:
    def test_prune_expired_entries_from_store(self):
        store = InMemoryAgentMemoryStore()
        manager = MemoryLifecycleManager(store=store)
        now = time.time()

        fresh_entry = MemoryEntry(
            key="active_key",
            value="valid",
            tier=MemoryTier.WORKING,
            namespace="task:123",
            created_at=now,
            updated_at=now,
            expires_at=now + 1000.0,
        )
        expired_entry = MemoryEntry(
            key="stale_key",
            value="obsolete",
            tier=MemoryTier.WORKING,
            namespace="task:123",
            created_at=now - 2000.0,
            updated_at=now - 2000.0,
            expires_at=now - 100.0,
        )
        store.store(fresh_entry)
        store.store(expired_entry)

        retained, evicted = manager.prune_expired_and_low_utility(current_time=now)
        assert len(retained) == 1
        assert len(evicted) == 1
        assert retained[0].key == "active_key"
        assert evicted[0].key == "stale_key"

        # Check store state
        assert store.get_by_id(fresh_entry.entry_id) is not None
        assert store.get_by_id(expired_entry.entry_id) is None

    def test_compact_store_in_memory(self):
        store = InMemoryAgentMemoryStore()
        manager = MemoryLifecycleManager(store=store)
        now = time.time()

        entry1 = MemoryEntry(
            key="server_ip_1",
            value="192.168.1.1",
            tier=MemoryTier.SEMANTIC,
            namespace="system_facts",
            confidence=0.7,
            created_at=now - 100.0,
            updated_at=now - 100.0,
        )
        entry2 = MemoryEntry(
            key="server_ip_2",
            value="192.168.1.2",
            tier=MemoryTier.SEMANTIC,
            namespace="system_facts",
            confidence=0.9,
            created_at=now - 50.0,
            updated_at=now - 10.0,
        )
        store.store(entry1)
        store.store(entry2)

        assert len(store.list_entries(tier=MemoryTier.SEMANTIC, namespace="system_facts")) == 2

        record = manager.compact_store(tier=MemoryTier.SEMANTIC, namespace="system_facts", current_time=now)
        assert record.facts_analyzed == 2
        assert record.facts_merged == 0

        remaining = store.list_entries(tier=MemoryTier.SEMANTIC, namespace="system_facts")
        assert len(remaining) == 2

    def test_run_full_lifecycle_pass(self):
        store = InMemoryAgentMemoryStore()
        manager = MemoryLifecycleManager(store=store)
        now = time.time()

        store.store(MemoryEntry(
            key="user_name",
            value="Alice",
            tier=MemoryTier.SEMANTIC,
            namespace="user_profile",
            created_at=now,
            updated_at=now,
        ))
        store.store(MemoryEntry(
            key="temp_key",
            value="junk",
            tier=MemoryTier.WORKING,
            namespace="task:old",
            expires_at=now - 10.0,
            created_at=now - 100.0,
            updated_at=now - 100.0,
        ))

        summary = manager.run_full_lifecycle_pass(current_time=now)
        assert summary["evicted_count"] == 1
        assert summary["retained_count"] == 1
        assert "semantic_compaction" in summary
        assert "working_compaction" in summary
