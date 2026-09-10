"""Temporal Confidence Decay and Memory Utility Evaluation Engine (M15).

Computes mathematical temporal decay on memory entries using namespace-specific
half-life curves and evaluates multi-factor utility scores for intelligent lifecycle actions.
"""

from __future__ import annotations

import math
import time
from typing import Any, Sequence

from core.lifecycle_types import (
    DecayConfig,
    DecayModel,
    LifecycleAction,
    MemoryUtilityScore,
)
from core.memory_types import MemoryEntry


class TemporalDecayEngine:
    """Evaluates temporal confidence attenuation and multi-factor memory utility."""

    def __init__(self, config: DecayConfig | None = None) -> None:
        self.config = config if config is not None else DecayConfig()

    def decay_confidence(
        self,
        base_confidence: float,
        age_seconds: float,
        namespace: str = "general",
    ) -> float:
        """Compute the mathematically decayed confidence for a given age and namespace."""
        base_confidence = max(0.0, min(1.0, float(base_confidence)))
        if not self.config.enabled or self.config.decay_model == DecayModel.NONE:
            return base_confidence

        if age_seconds <= 0.0:
            return base_confidence

        half_life_days = self.config.get_half_life_days(namespace)
        half_life_seconds = max(1.0, float(half_life_days) * 86400.0)

        if self.config.decay_model == DecayModel.EXPONENTIAL:
            lambda_param = math.log(2.0) / half_life_seconds
            decay_factor = math.exp(-lambda_param * age_seconds)
        elif self.config.decay_model == DecayModel.LINEAR:
            # Linear decay to 0 at 2 * half_life
            decay_factor = max(0.0, 1.0 - (age_seconds / (2.0 * half_life_seconds)))
        elif self.config.decay_model == DecayModel.STEP:
            steps = math.floor(age_seconds / half_life_seconds)
            decay_factor = 0.5 ** steps
        else:
            decay_factor = 1.0

        decayed = base_confidence * decay_factor
        return max(self.config.min_confidence_floor, min(1.0, decayed))

    def evaluate_entry(
        self,
        entry: MemoryEntry,
        current_time: float | None = None,
    ) -> MemoryUtilityScore:
        """Evaluate a single memory entry and compute its decayed confidence and utility score."""
        if not isinstance(entry, MemoryEntry):
            raise TypeError("entry must be an instance of MemoryEntry.")

        now = current_time if current_time is not None else time.time()
        age_seconds = max(0.0, now - float(entry.created_at))

        # Check last access
        last_accessed_val = entry.metadata.get("last_accessed_at", entry.updated_at)
        try:
            last_accessed = float(last_accessed_val)
        except (ValueError, TypeError):
            last_accessed = float(entry.updated_at)

        recency_seconds = max(0.0, now - last_accessed)
        decayed_conf = self.decay_confidence(
            base_confidence=entry.confidence,
            age_seconds=age_seconds,
            namespace=entry.namespace,
        )

        access_count_val = entry.metadata.get("access_count", 0)
        try:
            access_count = max(0, int(access_count_val))
        except (ValueError, TypeError):
            access_count = 0

        # Recency score with a 7-day half-life curve
        recency_half_life = 7.0 * 86400.0
        recency_factor = math.exp(-math.log(2.0) * recency_seconds / recency_half_life)
        frequency_factor = min(1.0, access_count / 10.0)

        # Untrusted penalty
        untrusted_penalty = 0.20 if entry.is_untrusted else 0.0

        # Weighted utility formula: 40% confidence + 30% frequency + 30% recency - penalty
        utility = (0.40 * decayed_conf) + (0.30 * frequency_factor) + (0.30 * recency_factor) - untrusted_penalty
        utility_score = max(0.0, min(1.0, utility))

        # Determine recommended lifecycle action
        if entry.is_expired(now):
            action = LifecycleAction.EVICT
        elif decayed_conf <= self.config.min_confidence_floor and age_seconds > (self.config.get_half_life_days(entry.namespace) * 86400.0 * 2.0):
            action = LifecycleAction.EVICT
        elif utility_score < 0.15:
            action = LifecycleAction.EVICT
        elif utility_score < 0.35 and decayed_conf < 0.50:
            action = LifecycleAction.ARCHIVE
        elif utility_score >= 0.85 and access_count >= 5 and decayed_conf >= 0.80:
            action = LifecycleAction.PROMOTE
        else:
            action = LifecycleAction.RETAIN

        return MemoryUtilityScore(
            entry_id=entry.entry_id,
            key=entry.key,
            namespace=entry.namespace,
            tier=entry.tier.value if hasattr(entry.tier, "value") else str(entry.tier),
            base_confidence=entry.confidence,
            decayed_confidence=decayed_conf,
            access_count=access_count,
            recency_score=recency_factor,
            utility_score=utility_score,
            recommended_action=action,
            metadata=dict(entry.metadata),
        )

    def evaluate_entries(
        self,
        entries: Sequence[MemoryEntry],
        current_time: float | None = None,
    ) -> list[MemoryUtilityScore]:
        """Evaluate a collection of memory entries."""
        return [self.evaluate_entry(entry, current_time=current_time) for entry in entries]
