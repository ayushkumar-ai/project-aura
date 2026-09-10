"""Long-Term Memory Lifecycle Management and Compaction (M15).

Orchestrates memory utility evaluation, deduplication, compaction, and utility-weighted
pruning across multi-tier agent memory stores while preserving provenance grounding.
"""

from __future__ import annotations

import copy
import logging
import time
import uuid
from typing import Any, Sequence

from core.agent_memory import AgentMemoryStore
from core.lifecycle_types import (
    CompactionRecord,
    DecayConfig,
    LifecycleAction,
    MemoryUtilityScore,
    strip_forbidden_metadata_keys,
)
from core.memory_types import (
    MemoryEntry,
    MemoryTier,
)
from core.provenance import TaintedValue, is_tainted, unwrap_tainted, wrap_tainted
from core.temporal_decay import TemporalDecayEngine

logger = logging.getLogger("aura.memory_lifecycle")


class MemoryLifecycleManager:
    """Manages memory compaction, pruning, and temporal lifecycle workflows."""

    def __init__(
        self,
        store: AgentMemoryStore | None = None,
        decay_engine: TemporalDecayEngine | None = None,
        config: DecayConfig | None = None,
    ) -> None:
        self.store = store
        self.decay_engine = decay_engine if decay_engine is not None else TemporalDecayEngine(config=config)
        self.config = self.decay_engine.config

    def evaluate_entry(
        self,
        entry: MemoryEntry,
        current_time: float | None = None,
    ) -> MemoryUtilityScore:
        """Evaluate utility score and lifecycle recommendation for a single entry."""
        return self.decay_engine.evaluate_entry(entry, current_time=current_time)

    def evaluate_entries(
        self,
        entries: Sequence[MemoryEntry],
        current_time: float | None = None,
    ) -> list[MemoryUtilityScore]:
        """Evaluate utility scores for a collection of memory entries."""
        return self.decay_engine.evaluate_entries(entries, current_time=current_time)

    def evaluate_store(
        self,
        tier: MemoryTier | None = None,
        namespace: str | None = None,
        current_time: float | None = None,
    ) -> list[MemoryUtilityScore]:
        """Evaluate all entries in the store matching optional tier and namespace filters."""
        if self.store is None:
            raise ValueError("Cannot evaluate store: no AgentMemoryStore provided.")

        entries = self.store.list_entries(tier=tier, namespace=namespace)
        return self.evaluate_entries(entries, current_time=current_time)

    def prune_expired_and_low_utility(
        self,
        entries: Sequence[MemoryEntry] | None = None,
        tier: MemoryTier | None = None,
        namespace: str | None = None,
        current_time: float | None = None,
    ) -> tuple[list[MemoryEntry], list[MemoryEntry]]:
        """Filter out expired and low-utility entries.

        Returns:
            (retained_entries, evicted_entries)
        """
        now = current_time if current_time is not None else time.time()

        if entries is None:
            if self.store is None:
                raise ValueError("Either entries or a configured store must be provided.")
            entries = self.store.list_entries(tier=tier, namespace=namespace)

        retained: list[MemoryEntry] = []
        evicted: list[MemoryEntry] = []

        for entry in entries:
            score = self.evaluate_entry(entry, current_time=now)
            if score.recommended_action == LifecycleAction.EVICT or entry.is_expired(now):
                evicted.append(entry)
                if self.store is not None:
                    try:
                        self.store.delete(entry.entry_id)
                    except Exception as e:
                        logger.warning("Failed to delete evicted entry %s from store: %s", entry.entry_id, e)
            else:
                retained.append(entry)

        return retained, evicted

    def compact_entries(
        self,
        entries: Sequence[MemoryEntry],
        namespace: str = "general",
        current_time: float | None = None,
    ) -> tuple[list[MemoryEntry], CompactionRecord]:
        """Merge duplicate or redundant memory entries, aggregating confidence and provenance.

        Returns:
            (compacted_entries, CompactionRecord)
        """
        now = current_time if current_time is not None else time.time()
        compaction_id = f"compact_{uuid.uuid4().hex[:10]}"

        if not entries:
            record = CompactionRecord(
                compaction_id=compaction_id,
                namespace=namespace,
                facts_analyzed=0,
                facts_merged=0,
                facts_evicted=0,
                memory_reclaimed_entries=0,
                timestamp=now,
            )
            return [], record

        # Group entries by (tier, namespace, key)
        groups: dict[tuple[str, str, str], list[MemoryEntry]] = {}
        for entry in entries:
            tier_val = entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier)
            group_key = (tier_val, entry.namespace, entry.key)
            groups.setdefault(group_key, []).append(entry)

        compacted: list[MemoryEntry] = []
        facts_merged_count = 0

        for (tier_val, ns_val, key_val), group in groups.items():
            if len(group) == 1:
                compacted.append(group[0])
                continue

            # Merge multiple entries
            facts_merged_count += len(group) - 1

            # Best confidence selection / blending
            max_conf = max(e.confidence for e in group)

            # Untrusted check & provenance merging
            is_untrusted = any(e.is_untrusted for e in group)
            all_urls: set[str] = set()
            for e in group:
                all_urls.update(e.source_urls)

            # Combine access count and timestamps
            min_created = min(e.created_at for e in group)
            max_updated = max(e.updated_at for e in group)

            total_access = sum(int(e.metadata.get("access_count", 0)) for e in group)
            last_accessed = max((float(e.metadata.get("last_accessed_at", e.updated_at)) for e in group), default=max_updated)

            # Pick latest value or non-empty value
            latest_entry = max(group, key=lambda e: e.updated_at)
            merged_val = latest_entry.value

            # If untrusted or tainted, wrap properly
            if is_untrusted or is_tainted(merged_val):
                raw = unwrap_tainted(merged_val)
                merged_val = wrap_tainted(
                    value=raw,
                    is_untrusted=True,
                    source_urls=tuple(sorted(all_urls)),
                )

            # Merged metadata
            combined_meta: dict[str, Any] = {}
            for e in group:
                combined_meta.update(e.metadata)
            combined_meta["access_count"] = total_access
            combined_meta["last_accessed_at"] = last_accessed
            combined_meta["compacted_from_count"] = len(group)
            clean_meta = strip_forbidden_metadata_keys(combined_meta)

            # Determine expiration
            expirations = [e.expires_at for e in group if e.expires_at is not None]
            merged_expires = max(expirations) if expirations else None

            merged_entry = MemoryEntry(
                entry_id=latest_entry.entry_id,
                tier=latest_entry.tier,
                namespace=ns_val,
                key=key_val,
                value=merged_val,
                confidence=max_conf,
                is_untrusted=is_untrusted,
                source_urls=tuple(sorted(all_urls)),
                created_at=min_created,
                updated_at=max_updated,
                expires_at=merged_expires,
                metadata=clean_meta,
            )
            compacted.append(merged_entry)

        reclaimed = len(entries) - len(compacted)
        record = CompactionRecord(
            compaction_id=compaction_id,
            namespace=namespace,
            facts_analyzed=len(entries),
            facts_merged=facts_merged_count,
            facts_evicted=0,
            memory_reclaimed_entries=reclaimed,
            timestamp=now,
        )
        return compacted, record

    def compact_store(
        self,
        tier: MemoryTier = MemoryTier.SEMANTIC,
        namespace: str | None = None,
        current_time: float | None = None,
    ) -> CompactionRecord:
        """Execute compaction pass directly against the configured AgentMemoryStore."""
        if self.store is None:
            raise ValueError("Cannot compact store: no AgentMemoryStore provided.")

        entries = self.store.list_entries(tier=tier, namespace=namespace)
        ns_label = namespace if namespace is not None else "all"
        compacted, record = self.compact_entries(entries, namespace=ns_label, current_time=current_time)

        # Update store with compacted results
        compacted_ids = {e.entry_id for e in compacted}
        for old_entry in entries:
            if old_entry.entry_id not in compacted_ids:
                try:
                    self.store.delete(old_entry.entry_id)
                except Exception as e:
                    logger.warning("Failed to delete stale entry %s during compaction: %s", old_entry.entry_id, e)

        for merged_entry in compacted:
            try:
                self.store.store(merged_entry)
            except Exception as e:
                logger.error("Failed to persist compacted entry %s: %s", merged_entry.entry_id, e)

        return record

    def run_full_lifecycle_pass(
        self,
        current_time: float | None = None,
    ) -> dict[str, Any]:
        """Execute complete pruning and compaction pass across all memory tiers."""
        if self.store is None:
            raise ValueError("Cannot run lifecycle pass: no AgentMemoryStore provided.")

        now = current_time if current_time is not None else time.time()
        # 1. Prune expired & low utility across all entries
        retained, evicted = self.prune_expired_and_low_utility(current_time=now)

        # 2. Compact semantic memory
        semantic_record = self.compact_store(tier=MemoryTier.SEMANTIC, current_time=now)

        # 3. Compact working memory
        working_record = self.compact_store(tier=MemoryTier.WORKING, current_time=now)

        return {
            "timestamp": now,
            "evicted_count": len(evicted),
            "retained_count": len(retained),
            "semantic_compaction": semantic_record.to_dict(),
            "working_compaction": working_record.to_dict(),
        }
