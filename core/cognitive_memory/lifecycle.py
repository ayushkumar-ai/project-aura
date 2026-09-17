"""M56 — Cognitive Memory Lifecycle Management & Temporal Decay Engine.

Manages state transitions (ACTIVE, STALE, SUPERSEDED, ARCHIVED, DELETED),
calculates exponential temporal confidence decay per category and provenance,
and provides vector synchronization and filtering guards.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from core.cognitive_memory.types import (
    CognitiveMemory,
    CognitiveMemoryType,
    LifecycleState,
    ProvenanceType,
)

logger = logging.getLogger("aura.cognitive_memory.lifecycle")


@dataclass
class MemoryLifecycleConfig:
    """Configuration for cognitive memory decay, expiration, and compaction."""
    decay_enabled: bool = True
    default_half_life_days: float = 30.0
    category_half_life_days: dict[str, float] = field(
        default_factory=lambda: {
            CognitiveMemoryType.EPISODIC.value: 14.0,
            CognitiveMemoryType.SEMANTIC.value: 90.0,
            CognitiveMemoryType.PREFERENCE.value: 180.0,
            CognitiveMemoryType.EXPERIENCE.value: 60.0,
            CognitiveMemoryType.USER_PROFILE.value: 365.0,
        }
    )
    stale_confidence_threshold: float = 0.3
    purge_threshold_days: float = 180.0


class MemoryLifecycleManager:
    """Calculates temporal decay, evaluates state machine transitions, and filters recall indices."""

    def __init__(self, config: MemoryLifecycleConfig | None = None):
        self.config = config or MemoryLifecycleConfig()

    def calculate_decayed_confidence(
        self,
        memory: CognitiveMemory,
        current_time: float | None = None,
    ) -> float:
        """Calculate time-decayed confidence score based on category and provenance."""
        if not self.config.decay_enabled:
            return memory.confidence

        # Invariant M56-F09: Explicit user statements do not experience temporal decay
        if memory.provenance_type == ProvenanceType.USER_EXPLICIT:
            return memory.confidence

        now = current_time if current_time is not None else time.time()
        elapsed_seconds = max(0.0, now - memory.last_accessed_at)

        mem_type_val = (
            memory.memory_type.value
            if isinstance(memory.memory_type, CognitiveMemoryType)
            else str(memory.memory_type)
        )
        half_life_days = self.config.category_half_life_days.get(
            mem_type_val,
            self.config.default_half_life_days,
        )
        half_life_seconds = max(1.0, half_life_days * 86400.0)

        decay_factor = 2.0 ** (-elapsed_seconds / half_life_seconds)
        decayed = memory.confidence * decay_factor
        return max(0.0, min(1.0, round(decayed, 4)))

    def evaluate_lifecycle_state(
        self,
        memory: CognitiveMemory,
        current_time: float | None = None,
    ) -> LifecycleState:
        """Determine the updated lifecycle state for a given memory entry."""
        # Superseded, archived, or deleted states are terminal/explicitly set
        if memory.lifecycle_state in (
            LifecycleState.SUPERSEDED,
            LifecycleState.ARCHIVED,
            LifecycleState.DELETED,
        ):
            return memory.lifecycle_state

        now = current_time if current_time is not None else time.time()

        # Check explicit expiration timestamp
        if memory.expires_at is not None and now >= memory.expires_at:
            return LifecycleState.STALE

        # Check confidence decay below threshold (Invariant M56-F10)
        decayed_conf = self.calculate_decayed_confidence(memory, current_time=now)
        if decayed_conf < self.config.stale_confidence_threshold:
            return LifecycleState.STALE

        return LifecycleState.ACTIVE

    def filter_active_for_retrieval(
        self,
        memories: list[CognitiveMemory],
        current_time: float | None = None,
    ) -> list[CognitiveMemory]:
        """Filter memories to strictly active, non-superseded, non-deleted entries (Invariant M56-F07)."""
        now = current_time if current_time is not None else time.time()
        active_list: list[CognitiveMemory] = []

        for mem in memories:
            state = self.evaluate_lifecycle_state(mem, current_time=now)
            if state == LifecycleState.ACTIVE:
                active_list.append(mem)

        return active_list

    def transition_state(
        self,
        memory: CognitiveMemory,
        target_state: LifecycleState,
        reason: str = "",
    ) -> CognitiveMemory:
        """Produce an updated CognitiveMemory with new lifecycle state and audit metadata."""
        updated_meta = dict(memory.metadata)
        if reason:
            transitions = updated_meta.setdefault("state_transitions", [])
            if isinstance(transitions, list):
                transitions.append({
                    "from_state": memory.lifecycle_state.value,
                    "to_state": target_state.value,
                    "reason": reason,
                    "timestamp": time.time(),
                })

        memory_dict = memory.to_dict()
        memory_dict["lifecycle_state"] = target_state.value
        memory_dict["updated_at"] = time.time()
        memory_dict["metadata"] = updated_meta

        return CognitiveMemory.from_dict(memory_dict)
