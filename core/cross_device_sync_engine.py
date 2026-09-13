"""M39 — Cross-Device AURA State Synchronization Engine.

Provides vector clock tracking, delta generation, idempotent replay, offline buffering,
and conflict-resolved peer synchronization.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any
from uuid import uuid4

from core.cross_device_types import (
    ConflictResolutionStrategy,
    SyncDelta,
    SyncOperationType,
    SyncStatusReport,
    VectorClock,
)

logger = logging.getLogger("aura.cross_device_sync")


class CrossDeviceSyncEngine:
    """Distributed state synchronization engine for multi-device AURA nodes."""

    def __init__(
        self,
        device_id: str = "device_default",
        durable_state_store: Any | None = None,
        default_conflict_strategy: ConflictResolutionStrategy = ConflictResolutionStrategy.LAST_WRITE_WINS,
    ):
        self.device_id = device_id
        self.durable_state_store = durable_state_store
        self.conflict_strategy = default_conflict_strategy
        self.is_online = True

        self._vector_clock = VectorClock({self.device_id: 0})
        self._applied_idempotency_keys: set[str] = set()
        self._applied_deltas_count = 0
        self._conflicts_resolved_count = 0
        self._outgoing_buffer: list[SyncDelta] = []
        self._local_state_cache: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def set_online(self, is_online: bool) -> None:
        with self._lock:
            self.is_online = is_online
            logger.debug(f"Device '{self.device_id}' online status set to: {is_online}")

    def generate_delta(
        self,
        operation: SyncOperationType,
        entity_id: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> SyncDelta:
        """Create and buffer an outgoing state delta with incremented local clock."""
        with self._lock:
            self._vector_clock.increment(self.device_id)
            delta_id = f"delta_{uuid4().hex[:12]}"
            idem_key = idempotency_key or f"idem_{uuid4().hex[:12]}"

            delta = SyncDelta(
                delta_id=delta_id,
                source_device_id=self.device_id,
                operation=operation,
                entity_id=entity_id,
                payload=payload,
                vector_clock=self._vector_clock.to_dict(),
                timestamp=time.time(),
                idempotency_key=idem_key,
            )

            # Record locally
            self._local_state_cache[entity_id] = {
                "payload": payload,
                "timestamp": delta.timestamp,
                "source": self.device_id,
            }
            self._applied_idempotency_keys.add(idem_key)
            self._applied_deltas_count += 1
            self._outgoing_buffer.append(delta)

            return delta

    def receive_delta(self, delta: SyncDelta) -> tuple[bool, str]:
        """Apply an incoming delta with idempotency and conflict resolution."""
        with self._lock:
            # 1. Idempotency Check
            if delta.idempotency_key and delta.idempotency_key in self._applied_idempotency_keys:
                return True, "already_applied_idempotent"

            # 2. Conflict Resolution Check
            existing = self._local_state_cache.get(delta.entity_id)
            if existing:
                existing_time = existing.get("timestamp", 0.0)
                if self.conflict_strategy == ConflictResolutionStrategy.LAST_WRITE_WINS:
                    if delta.timestamp < existing_time:
                        self._conflicts_resolved_count += 1
                        return False, "rejected_older_timestamp"
                elif self.conflict_strategy == ConflictResolutionStrategy.REJECT_STALE:
                    if delta.timestamp <= existing_time:
                        self._conflicts_resolved_count += 1
                        return False, "rejected_stale_update"

            # 3. Apply Delta to Local State Cache
            self._local_state_cache[delta.entity_id] = {
                "payload": delta.payload,
                "timestamp": delta.timestamp,
                "source": delta.source_device_id,
            }
            if delta.idempotency_key:
                self._applied_idempotency_keys.add(delta.idempotency_key)
            self._applied_deltas_count += 1

            # 4. Merge Vector Clock
            self._vector_clock.merge(delta.vector_clock)

            # 5. Dispatch to Durable State Store if available
            if self.durable_state_store is not None:
                self._apply_to_durable_store(delta)

            return True, "applied"

    def _apply_to_durable_store(self, delta: SyncDelta) -> None:
        """Mirror applied delta into underlying persistent stores."""
        try:
            if delta.operation == SyncOperationType.SET_PREFERENCE:
                if hasattr(self.durable_state_store, "update_preferences"):
                    self.durable_state_store.update_preferences(delta.payload)
            elif delta.operation == SyncOperationType.UPSERT_MEMORY:
                if hasattr(self.durable_state_store, "record_memory"):
                    self.durable_state_store.record_memory(
                        category=delta.payload.get("category", "semantic"),
                        content=delta.payload.get("content", ""),
                        confidence=delta.payload.get("confidence", 1.0),
                        tags=delta.payload.get("tags", []),
                    )
            elif delta.operation == SyncOperationType.RECORD_EXPERIENCE:
                if hasattr(self.durable_state_store, "record_experience"):
                    self.durable_state_store.record_experience(
                        task_description=delta.payload.get("task_description", ""),
                        plan_summary=delta.payload.get("plan_summary", ""),
                        outcome=delta.payload.get("outcome", "success"),
                    )
        except Exception as e:
            logger.warning(f"Error applying delta to durable store: {e}")

    def sync_with_peer(self, peer: CrossDeviceSyncEngine) -> int:
        """Bidirectionally synchronize pending deltas with a peer node."""
        if not self.is_online or not peer.is_online:
            return 0

        synced_count = 0
        # Deterministic global lock ordering to eliminate cyclic AB/BA deadlocks
        first_lock, second_lock = (
            (self._lock, peer._lock)
            if id(self._lock) < id(peer._lock)
            else (peer._lock, self._lock)
        )
        with first_lock, second_lock:
            # Send local outgoing deltas to peer
            for d in list(self._outgoing_buffer):
                applied, _ = peer.receive_delta(d)
                if applied:
                    synced_count += 1

            # Receive peer outgoing deltas
            for d in list(peer._outgoing_buffer):
                applied, _ = self.receive_delta(d)
                if applied:
                    synced_count += 1

            # Clear flushed buffers
            self._outgoing_buffer.clear()
            peer._outgoing_buffer.clear()

        return synced_count

    def get_cached_entity(self, entity_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._local_state_cache.get(entity_id)

    def get_status(self) -> SyncStatusReport:
        with self._lock:
            return SyncStatusReport(
                device_id=self.device_id,
                is_online=self.is_online,
                vector_clock=self._vector_clock.to_dict(),
                pending_outgoing_deltas=len(self._outgoing_buffer),
                applied_deltas_count=self._applied_deltas_count,
                conflicts_resolved_count=self._conflicts_resolved_count,
                last_sync_timestamp=time.time(),
            )
