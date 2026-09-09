import copy
import json
import logging
import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.memory_types import (
    MemoryEntry,
    MemoryNamespace,
    MemoryTier,
    deserialize_memory_entry,
    serialize_memory_entry,
)
from core.provenance import TaintedValue, is_tainted, unwrap_tainted

logger = logging.getLogger("aura.agent_memory")


class AgentMemoryStore(ABC):
    """Abstract contract for persisting and querying multi-tier agent memory."""

    @abstractmethod
    def store(self, entry: MemoryEntry) -> MemoryEntry:
        """Store or update a MemoryEntry."""
        raise NotImplementedError

    @abstractmethod
    def get(self, entry_id: str) -> MemoryEntry:
        """Retrieve a MemoryEntry by ID."""
        raise NotImplementedError

    def get_by_id(self, entry_id: str) -> MemoryEntry | None:
        """Retrieve a MemoryEntry by ID, returning None if not found."""
        try:
            return self.get(entry_id)
        except Exception:
            return None

    @abstractmethod
    def get_by_key(self, tier: MemoryTier, namespace: str, key: str) -> MemoryEntry | None:
        """Retrieve a MemoryEntry by tier, namespace, and key."""
        raise NotImplementedError

    @abstractmethod
    def list_entries(
        self,
        tier: MemoryTier | None = None,
        namespace: str | None = None,
        is_untrusted: bool | None = None,
    ) -> list[MemoryEntry]:
        """List all memory entries matching optional filters."""
        raise NotImplementedError

    @abstractmethod
    def search(
        self,
        query: str,
        tier: MemoryTier | None = None,
        namespace: str | None = None,
        top_k: int = 5,
    ) -> list[MemoryEntry]:
        """Search memory entries by textual query, relevance, and filters."""
        raise NotImplementedError

    @abstractmethod
    def delete(self, entry_id: str) -> None:
        """Delete a MemoryEntry by ID."""
        raise NotImplementedError

    @abstractmethod
    def clear_tier(self, tier: MemoryTier, namespace: str | None = None) -> None:
        """Clear all entries in a specific tier and optional namespace."""
        raise NotImplementedError


def _score_entry_relevance(entry: MemoryEntry, query_tokens: set[str]) -> float:
    """Deterministic token overlap relevance scoring for keyword/semantic match."""
    if not query_tokens:
        return 1.0

    score = 0.0
    key_tokens = set(entry.key.lower().replace(":", " ").replace("_", " ").split())
    overlap_key = len(query_tokens.intersection(key_tokens))
    score += overlap_key * 3.0

    val_str = str(unwrap_tainted(entry.value)).lower()
    val_tokens = set(val_str.replace(":", " ").replace("_", " ").split())
    overlap_val = len(query_tokens.intersection(val_tokens))
    score += overlap_val * 1.5

    meta_str = str(entry.metadata).lower()
    meta_tokens = set(meta_str.replace(":", " ").replace("_", " ").split())
    overlap_meta = len(query_tokens.intersection(meta_tokens))
    score += overlap_meta * 0.5

    return score * entry.confidence


class InMemoryAgentMemoryStore(AgentMemoryStore):
    """In-memory reference implementation of AgentMemoryStore with deepcopy isolation and ring-buffers."""

    def __init__(
        self,
        max_working_entries: int = 50,
        max_semantic_facts: int = 200,
        max_episodic_records: int = 500,
    ):
        self.limits = {
            MemoryTier.WORKING: max_working_entries,
            MemoryTier.SEMANTIC: max_semantic_facts,
            MemoryTier.EPISODIC: max_episodic_records,
        }
        self._entries: dict[str, MemoryEntry] = {}
        # Track insertion order for ring buffer bounding per tier
        self._tier_index: dict[MemoryTier, list[str]] = {
            MemoryTier.WORKING: [],
            MemoryTier.SEMANTIC: [],
            MemoryTier.EPISODIC: [],
        }

    def store(self, entry: MemoryEntry) -> MemoryEntry:
        """Store an entry in memory with deepcopy isolation."""
        if not isinstance(entry, MemoryEntry):
            raise TypeError("entry must be a MemoryEntry instance.")

        tier = entry.tier
        clean_entry = copy.deepcopy(entry)

        # Check for key collision within the same tier and namespace
        existing_id = None
        for eid in self._tier_index[tier]:
            existing = self._entries.get(eid)
            if existing and existing.namespace == clean_entry.namespace and existing.key == clean_entry.key:
                existing_id = eid
                break

        if existing_id is not None:
            # Update existing entry ID or replace
            clean_entry = MemoryEntry(
                entry_id=existing_id,
                tier=clean_entry.tier,
                namespace=clean_entry.namespace,
                key=clean_entry.key,
                value=clean_entry.value,
                confidence=clean_entry.confidence,
                is_untrusted=clean_entry.is_untrusted,
                source_urls=clean_entry.source_urls,
                created_at=self._entries[existing_id].created_at,
                updated_at=clean_entry.updated_at,
                expires_at=clean_entry.expires_at,
                metadata=clean_entry.metadata,
            )
            self._entries[existing_id] = clean_entry
            return copy.deepcopy(clean_entry)

        # New entry: check tier limit
        tier_list = self._tier_index[tier]
        max_limit = self.limits[tier]

        while len(tier_list) >= max_limit:
            oldest_id = tier_list.pop(0)
            self._entries.pop(oldest_id, None)

        self._entries[clean_entry.entry_id] = clean_entry
        tier_list.append(clean_entry.entry_id)

        return copy.deepcopy(clean_entry)

    def get(self, entry_id: str) -> MemoryEntry:
        """Retrieve entry by ID."""
        if not isinstance(entry_id, str) or not entry_id.strip():
            raise KeyError(f"Invalid entry_id: {entry_id}")
        clean_id = entry_id.strip()
        if clean_id not in self._entries:
            raise KeyError(f"MemoryEntry '{clean_id}' not found.")
        return copy.deepcopy(self._entries[clean_id])

    def get_by_key(self, tier: MemoryTier, namespace: str, key: str) -> MemoryEntry | None:
        """Retrieve entry by tier, namespace, and key."""
        clean_tier = MemoryTier(tier) if isinstance(tier, str) else tier
        clean_ns = str(namespace).strip()
        clean_k = str(key).strip()

        for eid in self._tier_index[clean_tier]:
            e = self._entries.get(eid)
            if e and e.namespace == clean_ns and e.key == clean_k:
                return copy.deepcopy(e)
        return None

    def list_entries(
        self,
        tier: MemoryTier | None = None,
        namespace: str | None = None,
        is_untrusted: bool | None = None,
    ) -> list[MemoryEntry]:
        """List entries matching filters."""
        results: list[MemoryEntry] = []
        for e in self._entries.values():
            if tier is not None and e.tier != tier:
                continue
            if namespace is not None and e.namespace != namespace:
                continue
            if is_untrusted is not None and e.is_untrusted != is_untrusted:
                continue
            results.append(copy.deepcopy(e))
        return results

    def search(
        self,
        query: str,
        tier: MemoryTier | None = None,
        namespace: str | None = None,
        top_k: int = 5,
    ) -> list[MemoryEntry]:
        """Search memory entries by textual query and relevance score."""
        candidates = self.list_entries(tier=tier, namespace=namespace)
        if not candidates:
            return []

        tokens = set(query.lower().replace(":", " ").replace("_", " ").split())
        scored: list[tuple[float, float, MemoryEntry]] = []

        for e in candidates:
            score = _score_entry_relevance(e, tokens)
            if score > 0.0 or not tokens:
                scored.append((score, e.updated_at, e))

        # Sort descending by relevance score, then recency
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [item[2] for item in scored[:top_k]]

    def delete(self, entry_id: str) -> None:
        """Delete an entry by ID."""
        clean_id = str(entry_id).strip()
        if clean_id not in self._entries:
            raise KeyError(f"MemoryEntry '{clean_id}' not found.")
        e = self._entries.pop(clean_id)
        if clean_id in self._tier_index[e.tier]:
            self._tier_index[e.tier].remove(clean_id)

    def clear_tier(self, tier: MemoryTier, namespace: str | None = None) -> None:
        """Clear all entries in a tier and optional namespace."""
        clean_tier = MemoryTier(tier) if isinstance(tier, str) else tier
        ids_to_remove = []
        for eid in self._tier_index[clean_tier]:
            e = self._entries.get(eid)
            if e and (namespace is None or e.namespace == namespace):
                ids_to_remove.append(eid)

        for eid in ids_to_remove:
            self.delete(eid)


class FileAgentMemoryStore(AgentMemoryStore):
    """Persistent, file-backed AgentMemoryStore with atomic JSON file writes and ring-buffer bounds."""

    def __init__(
        self,
        storage_dir: str | Path,
        max_working_entries: int = 50,
        max_semantic_facts: int = 200,
        max_episodic_records: int = 500,
    ):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.limits = {
            MemoryTier.WORKING: max_working_entries,
            MemoryTier.SEMANTIC: max_semantic_facts,
            MemoryTier.EPISODIC: max_episodic_records,
        }

    def _tier_file(self, tier: MemoryTier) -> Path:
        return self.storage_dir / f"{tier.value}_memory.json"

    def _read_tier_entries(self, tier: MemoryTier) -> list[MemoryEntry]:
        fpath = self._tier_file(tier)
        if not fpath.exists():
            return []
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                return []
            entries = []
            for item in data:
                try:
                    entries.append(deserialize_memory_entry(item))
                except Exception as ex:
                    logger.warning("Failed to deserialize memory entry in %s: %s", fpath, ex)
            return entries
        except Exception as ex:
            logger.error("Failed to read memory file %s: %s", fpath, ex)
            return []

    def _write_tier_entries(self, tier: MemoryTier, entries: list[MemoryEntry]) -> None:
        fpath = self._tier_file(tier)
        temp_dir = self.storage_dir / ".tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        temp_path = temp_dir / f"{tier.value}_{uuid4().hex}.tmp"

        payload = [serialize_memory_entry(e) for e in entries]

        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            # Atomic replacement
            os.replace(temp_path, fpath)
        except Exception as ex:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            raise IOError(f"Failed to atomically write memory file {fpath}: {ex}") from ex

    def store(self, entry: MemoryEntry) -> MemoryEntry:
        """Store or update a MemoryEntry in persistent storage."""
        if not isinstance(entry, MemoryEntry):
            raise TypeError("entry must be a MemoryEntry instance.")

        tier = entry.tier
        entries = self._read_tier_entries(tier)

        existing_index = -1
        for i, e in enumerate(entries):
            if e.namespace == entry.namespace and e.key == entry.key:
                existing_index = i
                break

        if existing_index >= 0:
            existing = entries[existing_index]
            clean_entry = MemoryEntry(
                entry_id=existing.entry_id,
                tier=entry.tier,
                namespace=entry.namespace,
                key=entry.key,
                value=entry.value,
                confidence=entry.confidence,
                is_untrusted=entry.is_untrusted,
                source_urls=entry.source_urls,
                created_at=existing.created_at,
                updated_at=entry.updated_at,
                expires_at=entry.expires_at,
                metadata=entry.metadata,
            )
            entries[existing_index] = clean_entry
        else:
            clean_entry = copy.deepcopy(entry)
            entries.append(clean_entry)

        # Enforce ring buffer limits
        max_limit = self.limits[tier]
        if len(entries) > max_limit:
            entries = entries[-max_limit:]

        self._write_tier_entries(tier, entries)
        return copy.deepcopy(clean_entry)

    def get(self, entry_id: str) -> MemoryEntry:
        """Retrieve entry by ID across all tiers."""
        clean_id = str(entry_id).strip()
        for tier in (MemoryTier.WORKING, MemoryTier.SEMANTIC, MemoryTier.EPISODIC):
            for e in self._read_tier_entries(tier):
                if e.entry_id == clean_id:
                    return copy.deepcopy(e)
        raise KeyError(f"MemoryEntry '{clean_id}' not found.")

    def get_by_key(self, tier: MemoryTier, namespace: str, key: str) -> MemoryEntry | None:
        """Retrieve entry by tier, namespace, and key."""
        clean_tier = MemoryTier(tier) if isinstance(tier, str) else tier
        clean_ns = str(namespace).strip()
        clean_k = str(key).strip()

        for e in self._read_tier_entries(clean_tier):
            if e.namespace == clean_ns and e.key == clean_k:
                return copy.deepcopy(e)
        return None

    def list_entries(
        self,
        tier: MemoryTier | None = None,
        namespace: str | None = None,
        is_untrusted: bool | None = None,
    ) -> list[MemoryEntry]:
        """List entries matching filters."""
        results: list[MemoryEntry] = []
        tiers = [tier] if tier is not None else [MemoryTier.WORKING, MemoryTier.SEMANTIC, MemoryTier.EPISODIC]
        for t in tiers:
            for e in self._read_tier_entries(t):
                if namespace is not None and e.namespace != namespace:
                    continue
                if is_untrusted is not None and e.is_untrusted != is_untrusted:
                    continue
                results.append(copy.deepcopy(e))
        return results

    def search(
        self,
        query: str,
        tier: MemoryTier | None = None,
        namespace: str | None = None,
        top_k: int = 5,
    ) -> list[MemoryEntry]:
        """Search entries by relevance and filters."""
        candidates = self.list_entries(tier=tier, namespace=namespace)
        if not candidates:
            return []

        tokens = set(query.lower().replace(":", " ").replace("_", " ").split())
        scored: list[tuple[float, float, MemoryEntry]] = []

        for e in candidates:
            score = _score_entry_relevance(e, tokens)
            if score > 0.0 or not tokens:
                scored.append((score, e.updated_at, e))

        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [item[2] for item in scored[:top_k]]

    def delete(self, entry_id: str) -> None:
        """Delete entry by ID."""
        clean_id = str(entry_id).strip()
        found = False
        for tier in (MemoryTier.WORKING, MemoryTier.SEMANTIC, MemoryTier.EPISODIC):
            entries = self._read_tier_entries(tier)
            new_entries = [e for e in entries if e.entry_id != clean_id]
            if len(new_entries) != len(entries):
                self._write_tier_entries(tier, new_entries)
                found = True
                break
        if not found:
            raise KeyError(f"MemoryEntry '{clean_id}' not found.")

    def clear_tier(self, tier: MemoryTier, namespace: str | None = None) -> None:
        """Clear all entries in a specific tier and optional namespace."""
        clean_tier = MemoryTier(tier) if isinstance(tier, str) else tier
        if namespace is None:
            self._write_tier_entries(clean_tier, [])
        else:
            entries = self._read_tier_entries(clean_tier)
            filtered = [e for e in entries if e.namespace != namespace]
            self._write_tier_entries(clean_tier, filtered)
