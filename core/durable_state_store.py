"""M30 — Durable Personal State Store for Project AURA.

Provides atomic, checksum-verified, schema-versioned persistence for unified memories,
episodic experiences, user preferences, and cross-session personal state.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.personal_state_types import (
    EpisodicExperienceRecord,
    MemoryCategory,
    PersonalStateSnapshot,
    StateSchemaVersion,
    UnifiedMemoryRecord,
    UserPreferences,
)

logger = logging.getLogger("aura.durable_state")


class DurablePersonalStateStore:
    """Thread-safe, atomic, checksum-verified durable state store."""

    SNAPSHOT_FILENAME = "personal_state.json"
    BACKUP_FILENAME = "personal_state.json.bak"

    def __init__(self, storage_dir: str | Path | None = None):
        self.storage_dir = Path(storage_dir) if storage_dir else None
        self._lock = threading.RLock()
        self._preferences = UserPreferences()
        self._memories: dict[str, UnifiedMemoryRecord] = {}
        self._experiences: dict[str, EpisodicExperienceRecord] = {}
        self._knowledge_entity_ids: set[str] = set()
        self._artifact_ids: set[str] = set()
        self._metadata: dict[str, Any] = {}
        self._provenance: dict[str, Any] = {}

        if self.storage_dir:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            self._load_from_disk()

    def get_preferences(self) -> UserPreferences:
        """Get current user preferences."""
        with self._lock:
            return UserPreferences.from_dict(self._preferences.to_dict())

    def update_preferences(self, preferences: UserPreferences | dict[str, Any]) -> UserPreferences:
        """Update and persist user preferences."""
        with self._lock:
            if isinstance(preferences, dict):
                current_dict = self._preferences.to_dict()
                current_dict.update(preferences)
                current_dict["updated_at"] = time.time()
                self._preferences = UserPreferences.from_dict(current_dict)
            elif isinstance(preferences, UserPreferences):
                preferences.updated_at = time.time()
                self._preferences = preferences
            else:
                raise TypeError("preferences must be UserPreferences or dict")

            if self.storage_dir:
                self.save_snapshot()
            return self.get_preferences()

    def record_memory(
        self,
        category: MemoryCategory | str,
        content: str,
        confidence: float = 1.0,
        importance: float = 0.5,
        tags: list[str] | None = None,
        provenance: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> UnifiedMemoryRecord:
        """Record a unified memory entry."""
        with self._lock:
            cat_enum = MemoryCategory(category) if isinstance(category, str) else category
            rec_id = f"mem_{uuid4().hex[:12]}"
            record = UnifiedMemoryRecord(
                record_id=rec_id,
                category=cat_enum,
                content=content,
                confidence=confidence,
                importance=importance,
                created_at=time.time(),
                last_accessed=time.time(),
                provenance=provenance or {},
                tags=tags or [],
                metadata=metadata or {},
            )
            self._memories[rec_id] = record
            if self.storage_dir:
                self.save_snapshot()
            return record

    def query_memories(
        self,
        category: MemoryCategory | str | None = None,
        tag: str | None = None,
        query: str = "",
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[UnifiedMemoryRecord]:
        """Query unified memory records with filtering and keyword matching."""
        with self._lock:
            cat_enum = MemoryCategory(category) if isinstance(category, str) else category
            results: list[UnifiedMemoryRecord] = []
            q_lower = query.lower().strip() if query else ""

            for mem in self._memories.values():
                if cat_enum is not None and mem.category != cat_enum:
                    continue
                if mem.confidence < min_confidence:
                    continue
                if tag is not None and tag not in mem.tags:
                    continue
                if q_lower and q_lower not in mem.content.lower():
                    continue
                results.append(mem)

            results.sort(key=lambda m: (m.importance, m.last_accessed), reverse=True)
            return results[:limit]

    def record_experience(
        self,
        task_description: str,
        plan_summary: str,
        action_sequence: list[str] | None = None,
        outcome: str = "success",
        reward_score: float = 1.0,
        lessons_learned: list[str] | None = None,
        provenance: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EpisodicExperienceRecord:
        """Record an episodic experience item."""
        with self._lock:
            exp_id = f"exp_{uuid4().hex[:12]}"
            record = EpisodicExperienceRecord(
                experience_id=exp_id,
                task_description=task_description,
                plan_summary=plan_summary,
                action_sequence=action_sequence or [],
                outcome=outcome,
                reward_score=reward_score,
                lessons_learned=lessons_learned or [],
                created_at=time.time(),
                provenance=provenance or {},
                metadata=metadata or {},
            )
            self._experiences[exp_id] = record
            if self.storage_dir:
                self.save_snapshot()
            return record

    def query_experiences(
        self,
        outcome: str | None = None,
        query: str = "",
        limit: int = 20,
    ) -> list[EpisodicExperienceRecord]:
        """Query episodic experiences."""
        with self._lock:
            results: list[EpisodicExperienceRecord] = []
            q_lower = query.lower().strip() if query else ""

            for exp in self._experiences.values():
                if outcome is not None and exp.outcome != outcome:
                    continue
                if q_lower and (q_lower not in exp.task_description.lower() and q_lower not in exp.plan_summary.lower()):
                    continue
                results.append(exp)

            results.sort(key=lambda e: (e.reward_score, e.created_at), reverse=True)
            return results[:limit]

    def track_knowledge_entity(self, entity_id: str) -> None:
        """Track associated knowledge graph entity ID."""
        with self._lock:
            self._knowledge_entity_ids.add(entity_id)

    def track_artifact(self, artifact_id: str) -> None:
        """Track associated artifact ID."""
        with self._lock:
            self._artifact_ids.add(artifact_id)

    def create_snapshot(self) -> PersonalStateSnapshot:
        """Build an in-memory snapshot sealed with a SHA256 checksum."""
        with self._lock:
            snap = PersonalStateSnapshot(
                snapshot_id=f"snap_{uuid4().hex[:12]}",
                schema_version=StateSchemaVersion.CURRENT,
                created_at=time.time(),
                user_preferences=self.get_preferences(),
                memory_records=list(self._memories.values()),
                episodic_experiences=list(self._experiences.values()),
                knowledge_entity_ids=list(self._knowledge_entity_ids),
                artifact_ids=list(self._artifact_ids),
                metadata=dict(self._metadata),
                provenance=dict(self._provenance),
            )
            snap.seal()
            return snap

    def save_snapshot(self) -> str:
        """Atomically persist current state snapshot to disk."""
        with self._lock:
            if not self.storage_dir:
                return "in_memory"

            snap = self.create_snapshot()
            target_path = self.storage_dir / self.SNAPSHOT_FILENAME
            backup_path = self.storage_dir / self.BACKUP_FILENAME

            # Rotate existing target to backup if it exists
            if target_path.exists():
                try:
                    shutil.copyfile(target_path, backup_path)
                except Exception as e:
                    logger.warning(f"Failed to create state backup: {e}")

            # Write atomically via tempfile in same directory
            temp_file = None
            try:
                raw_json = json.dumps(snap.to_dict(), indent=2).encode("utf-8")
                with tempfile.NamedTemporaryFile("wb", dir=str(self.storage_dir), delete=False) as tf:
                    tf.write(raw_json)
                    temp_file = Path(tf.name)
                # Atomic replace
                temp_file.replace(target_path)
                logger.debug(f"Saved state snapshot '{snap.snapshot_id}' to {target_path}")
                return snap.snapshot_id
            except Exception as e:
                logger.error(f"Failed to persist state snapshot: {e}")
                if temp_file and temp_file.exists():
                    temp_file.unlink(missing_ok=True)
                raise

    def _load_from_disk(self) -> bool:
        """Load and verify latest state snapshot from disk, falling back to backup on corruption."""
        if not self.storage_dir:
            return False

        target_path = self.storage_dir / self.SNAPSHOT_FILENAME
        backup_path = self.storage_dir / self.BACKUP_FILENAME

        for file_path in (target_path, backup_path):
            if not file_path.exists():
                continue

            try:
                raw_text = file_path.read_text(encoding="utf-8")
                data = json.loads(raw_text)
                snap = PersonalStateSnapshot.from_dict(data)

                # Verify checksum integrity
                if snap.verify_integrity():
                    self._apply_snapshot(snap)
                    logger.info(f"Loaded valid state snapshot from {file_path}")
                    return True
                else:
                    logger.warning(f"Corrupted checksum detected in state file: {file_path}")
            except Exception as e:
                logger.error(f"Error loading state snapshot from {file_path}: {e}")

        return False

    def _apply_snapshot(self, snap: PersonalStateSnapshot) -> None:
        """Apply a loaded snapshot to in-memory state with schema handling."""
        self._preferences = snap.user_preferences
        self._memories = {m.record_id: m for m in snap.memory_records}
        self._experiences = {e.experience_id: e for e in snap.episodic_experiences}
        self._knowledge_entity_ids = set(snap.knowledge_entity_ids)
        self._artifact_ids = set(snap.artifact_ids)
        self._metadata = snap.metadata
        self._provenance = snap.provenance

    def verify_integrity(self) -> tuple[bool, str]:
        """Verify the integrity of on-disk state storage."""
        with self._lock:
            if not self.storage_dir:
                return True, "in_memory"

            target_path = self.storage_dir / self.SNAPSHOT_FILENAME
            if not target_path.exists():
                return True, "no_persisted_state_file"

            try:
                raw_text = target_path.read_text(encoding="utf-8")
                data = json.loads(raw_text)
                snap = PersonalStateSnapshot.from_dict(data)
                if snap.verify_integrity():
                    return True, "valid"
                return False, "checksum_mismatch"
            except Exception as e:
                return False, f"load_error: {e}"
