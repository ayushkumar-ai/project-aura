"""M30/M41 — Durable Personal State Types and Schema Definitions with User Isolation.

Defines unified representations for user preferences, personal state snapshots,
episodic experiences, unified memory records, and schema version migrations.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class StateSchemaVersion(str, Enum):
    V1_0 = "1.0"
    V2_0 = "2.0"
    CURRENT = "2.0"


class MemoryCategory(str, Enum):
    CONVERSATIONAL = "conversational"
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    PREFERENCE = "preference"


@dataclass
class UserPreferences:
    user_id: str = "default"
    preferred_name: str = "User"
    interaction_style: str = "concise"  # concise, detailed, technical, conversational
    verbosity: int = 2  # 1 (terse) to 5 (exhaustive)
    auto_approval_tier: str = "safe_only"  # none, safe_only, low_risk, full
    timezone: str = "UTC"
    custom_rules: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserPreferences:
        valid_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


@dataclass
class UnifiedMemoryRecord:
    record_id: str
    category: MemoryCategory
    content: str
    user_id: str = "default"
    confidence: float = 1.0
    importance: float = 0.5
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    provenance: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["category"] = self.category.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UnifiedMemoryRecord:
        d = dict(data)
        if isinstance(d.get("category"), str):
            d["category"] = MemoryCategory(d["category"])
        valid_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in d.items() if k in valid_fields}
        return cls(**filtered)


@dataclass
class EpisodicExperienceRecord:
    experience_id: str
    task_description: str
    plan_summary: str
    user_id: str = "default"
    action_sequence: list[str] = field(default_factory=list)
    outcome: str = "success"  # success, partial, failure
    reward_score: float = 1.0  # -1.0 to +1.0
    lessons_learned: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    provenance: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EpisodicExperienceRecord:
        valid_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


@dataclass
class PersonalStateSnapshot:
    snapshot_id: str
    schema_version: StateSchemaVersion = StateSchemaVersion.CURRENT
    created_at: float = field(default_factory=time.time)
    checksum: str = ""
    user_preferences: UserPreferences = field(default_factory=UserPreferences)
    memory_records: list[UnifiedMemoryRecord] = field(default_factory=list)
    episodic_experiences: list[EpisodicExperienceRecord] = field(default_factory=list)
    knowledge_entity_ids: list[str] = field(default_factory=list)
    artifact_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def compute_checksum(self) -> str:
        """Compute SHA256 checksum over canonical state fields."""
        payload = {
            "snapshot_id": self.snapshot_id,
            "schema_version": self.schema_version.value,
            "user_preferences": self.user_preferences.to_dict(),
            "memory_records": [m.to_dict() for m in self.memory_records],
            "episodic_experiences": [e.to_dict() for e in self.episodic_experiences],
            "knowledge_entity_ids": sorted(self.knowledge_entity_ids),
            "artifact_ids": sorted(self.artifact_ids),
        }
        raw = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def seal(self) -> None:
        """Calculate and store state checksum."""
        self.checksum = self.compute_checksum()

    def verify_integrity(self) -> bool:
        """Check if stored checksum matches computed content checksum."""
        if not self.checksum:
            return False
        return self.checksum == self.compute_checksum()

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "schema_version": self.schema_version.value,
            "created_at": self.created_at,
            "checksum": self.checksum,
            "user_preferences": self.user_preferences.to_dict(),
            "memory_records": [m.to_dict() for m in self.memory_records],
            "episodic_experiences": [e.to_dict() for e in self.episodic_experiences],
            "knowledge_entity_ids": self.knowledge_entity_ids,
            "artifact_ids": self.artifact_ids,
            "metadata": self.metadata,
            "provenance": self.provenance,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PersonalStateSnapshot:
        schema_v = data.get("schema_version", StateSchemaVersion.CURRENT.value)
        if isinstance(schema_v, str):
            try:
                schema_enum = StateSchemaVersion(schema_v)
            except ValueError:
                schema_enum = StateSchemaVersion.CURRENT
        else:
            schema_enum = StateSchemaVersion.CURRENT

        prefs_data = data.get("user_preferences", {})
        prefs = UserPreferences.from_dict(prefs_data) if isinstance(prefs_data, dict) else UserPreferences()

        mems_data = data.get("memory_records", [])
        mems = [UnifiedMemoryRecord.from_dict(m) for m in mems_data if isinstance(m, dict)]

        exps_data = data.get("episodic_experiences", [])
        exps = [EpisodicExperienceRecord.from_dict(e) for e in exps_data if isinstance(e, dict)]

        return cls(
            snapshot_id=data.get("snapshot_id", f"snap_{int(time.time())}"),
            schema_version=schema_enum,
            created_at=data.get("created_at", time.time()),
            checksum=data.get("checksum", ""),
            user_preferences=prefs,
            memory_records=mems,
            episodic_experiences=exps,
            knowledge_entity_ids=list(data.get("knowledge_entity_ids", [])),
            artifact_ids=list(data.get("artifact_ids", [])),
            metadata=data.get("metadata", {}),
            provenance=data.get("provenance", {}),
        )
