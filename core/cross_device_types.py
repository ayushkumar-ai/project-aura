"""M39 — Cross-Device AURA State Synchronization Types.

Defines schemas for vector clocks, sync deltas, conflict resolution strategies,
and cross-device synchronization state reports.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class SyncOperationType(str, Enum):
    SET_PREFERENCE = "set_preference"
    UPSERT_MEMORY = "upsert_memory"
    UPDATE_TASK_STATE = "update_task_state"
    RECORD_EXPERIENCE = "record_experience"
    SYNC_HEARTBEAT = "sync_heartbeat"


class ConflictResolutionStrategy(str, Enum):
    LAST_WRITE_WINS = "last_write_wins"
    VECTOR_CLOCK = "vector_clock"
    MERGE_UNION = "merge_union"
    REJECT_STALE = "reject_stale"


@dataclass
class VectorClock:
    clock: dict[str, int] = field(default_factory=dict)

    def increment(self, device_id: str) -> None:
        self.clock[device_id] = self.clock.get(device_id, 0) + 1

    def merge(self, other_clock: dict[str, int] | VectorClock) -> None:
        other_dict = other_clock.clock if isinstance(other_clock, VectorClock) else other_clock
        for dev, count in other_dict.items():
            self.clock[dev] = max(self.clock.get(dev, 0), count)

    def is_causally_newer(self, other_clock: dict[str, int] | VectorClock) -> bool:
        """Check if self has strictly greater or equal timestamps on all devices and strictly greater on at least one."""
        other_dict = other_clock.clock if isinstance(other_clock, VectorClock) else other_clock
        has_greater = False
        for dev, other_val in other_dict.items():
            self_val = self.clock.get(dev, 0)
            if self_val < other_val:
                return False
            if self_val > other_val:
                has_greater = True
        for dev, self_val in self.clock.items():
            if dev not in other_dict and self_val > 0:
                has_greater = True
        return has_greater

    def to_dict(self) -> dict[str, int]:
        return dict(self.clock)


@dataclass
class SyncDelta:
    delta_id: str
    source_device_id: str
    operation: SyncOperationType
    entity_id: str
    payload: dict[str, Any]
    vector_clock: dict[str, int] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    idempotency_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["operation"] = self.operation.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SyncDelta:
        d = dict(data)
        if isinstance(d.get("operation"), str):
            d["operation"] = SyncOperationType(d["operation"])
        valid_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in d.items() if k in valid_fields}
        return cls(**filtered)


@dataclass
class SyncStatusReport:
    device_id: str
    is_online: bool
    vector_clock: dict[str, int]
    pending_outgoing_deltas: int
    applied_deltas_count: int
    conflicts_resolved_count: int
    last_sync_timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
