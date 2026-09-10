import logging
import threading
import time
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from app.config import settings
from core.scheduling_types import (
    LockAcquireResult,
    LockStatus,
    LockType,
    ResourceLock,
)

logger = logging.getLogger("aura.resource_locks")


class SharedResourceLockManager:
    """Read/Write lock lease manager providing shared read and exclusive write mutexes across goals."""

    def __init__(self, default_ttl_seconds: float | None = None):
        self.default_ttl_seconds = (
            default_ttl_seconds
            if default_ttl_seconds is not None
            else getattr(settings, "aura_resource_lock_default_ttl_seconds", 30.0)
        )
        self._lock = threading.RLock()
        # active_locks: lock_id -> ResourceLock
        self._locks: dict[str, ResourceLock] = {}
        # resource_index: normalized_uri -> list of lock_id
        self._resource_index: dict[str, list[str]] = {}

    def _normalize_uri(self, uri: str) -> str:
        clean = str(uri).strip().lower()
        if not clean:
            raise ValueError("resource_uri must be a non-empty string.")
        return clean

    def prune_expired_locks(self, current_time: float | None = None) -> int:
        """Remove all expired lock leases across all resources."""
        now = current_time if current_time is not None else time.time()
        expired_ids: list[str] = []

        with self._lock:
            for lock_id, lk in list(self._locks.items()):
                if lk.is_expired(now):
                    expired_ids.append(lock_id)

            for lock_id in expired_ids:
                lk = self._locks.pop(lock_id, None)
                if lk:
                    uri = lk.resource_uri
                    if uri in self._resource_index:
                        self._resource_index[uri] = [lid for lid in self._resource_index[uri] if lid != lock_id]
                        if not self._resource_index[uri]:
                            del self._resource_index[uri]

        return len(expired_ids)

    def acquire_lock(
        self,
        resource_uri: str,
        goal_id: str,
        lock_type: LockType | str = LockType.SHARED_READ,
        ttl_seconds: float | None = None,
        current_time: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LockAcquireResult:
        """Attempt to acquire a read/write lock lease on a resource URI."""
        uri_norm = self._normalize_uri(resource_uri)
        clean_goal_id = str(goal_id).strip()
        if not clean_goal_id:
            raise ValueError("goal_id must be a non-empty string.")

        eff_type = LockType(lock_type) if isinstance(lock_type, str) else lock_type
        eff_ttl = float(ttl_seconds) if ttl_seconds is not None else self.default_ttl_seconds
        now = current_time if current_time is not None else time.time()

        with self._lock:
            self.prune_expired_locks(now)

            active_lock_ids = self._resource_index.get(uri_norm, [])
            active_locks = [self._locks[lid] for lid in active_lock_ids if lid in self._locks]

            # Check conflicts
            for lk in active_locks:
                if lk.owner_goal_id == clean_goal_id:
                    # Reentrant / refresh lease
                    new_lock = ResourceLock(
                        lock_id=lk.lock_id,
                        resource_uri=uri_norm,
                        lock_type=eff_type,
                        owner_goal_id=clean_goal_id,
                        acquired_at=now,
                        ttl_seconds=eff_ttl,
                        metadata=dict(metadata or {}),
                    )
                    self._locks[new_lock.lock_id] = new_lock
                    return LockAcquireResult(
                        success=True,
                        lock=new_lock,
                        reason=f"Refreshed existing lock lease '{new_lock.lock_id}'.",
                    )

                if lk.lock_type == LockType.EXCLUSIVE_WRITE:
                    return LockAcquireResult(
                        success=False,
                        conflict_owner_goal_id=lk.owner_goal_id,
                        reason=f"Resource '{uri_norm}' is exclusively locked by goal '{lk.owner_goal_id}'.",
                    )

                if eff_type == LockType.EXCLUSIVE_WRITE and lk.lock_type == LockType.SHARED_READ:
                    return LockAcquireResult(
                        success=False,
                        conflict_owner_goal_id=lk.owner_goal_id,
                        reason=f"Cannot acquire exclusive write lock on '{uri_norm}' because goal '{lk.owner_goal_id}' holds a shared read lock.",
                    )

            # Grant new lock
            new_lock = ResourceLock(
                lock_id=str(uuid4()),
                resource_uri=uri_norm,
                lock_type=eff_type,
                owner_goal_id=clean_goal_id,
                acquired_at=now,
                ttl_seconds=eff_ttl,
                metadata=dict(metadata or {}),
            )
            self._locks[new_lock.lock_id] = new_lock
            self._resource_index.setdefault(uri_norm, []).append(new_lock.lock_id)

            return LockAcquireResult(
                success=True,
                lock=new_lock,
                reason="Lock acquired successfully.",
            )

    def acquire_locks_batch(
        self,
        resource_uris: Sequence[str],
        goal_id: str,
        lock_type: LockType | str = LockType.SHARED_READ,
        ttl_seconds: float | None = None,
        current_time: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LockAcquireResult:
        """Atomically acquire locks on multiple resources in monotonic order to eliminate deadlocks."""
        if not resource_uris:
            return LockAcquireResult(success=True, reason="Empty resource batch granted.")

        sorted_uris = sorted(list(set(self._normalize_uri(u) for u in resource_uris)))
        now = current_time if current_time is not None else time.time()

        with self._lock:
            acquired_locks: list[ResourceLock] = []
            for uri in sorted_uris:
                res = self.acquire_lock(
                    resource_uri=uri,
                    goal_id=goal_id,
                    lock_type=lock_type,
                    ttl_seconds=ttl_seconds,
                    current_time=now,
                    metadata=metadata,
                )
                if not res.success:
                    # Rollback partially acquired locks
                    for acq in acquired_locks:
                        self.release_lock(acq.lock_id, goal_id)
                    return LockAcquireResult(
                        success=False,
                        conflict_owner_goal_id=res.conflict_owner_goal_id,
                        reason=f"Batch lock acquisition failed at resource '{uri}': {res.reason}",
                    )
                if res.lock:
                    acquired_locks.append(res.lock)

            return LockAcquireResult(
                success=True,
                lock=acquired_locks[0] if acquired_locks else None,
                reason=f"Batch acquired {len(acquired_locks)} locks successfully.",
            )

    def release_lock(self, lock_id: str, goal_id: str) -> bool:
        """Release a specific lock lease held by goal_id."""
        clean_lid = str(lock_id).strip()
        clean_gid = str(goal_id).strip()

        with self._lock:
            lk = self._locks.get(clean_lid)
            if not lk:
                return False
            if lk.owner_goal_id != clean_gid:
                logger.warning("Goal '%s' attempted to release lock '%s' owned by '%s'.", clean_gid, clean_lid, lk.owner_goal_id)
                return False

            del self._locks[clean_lid]
            uri = lk.resource_uri
            if uri in self._resource_index:
                self._resource_index[uri] = [lid for lid in self._resource_index[uri] if lid != clean_lid]
                if not self._resource_index[uri]:
                    del self._resource_index[uri]
            return True

    def release_all_locks_for_goal(self, goal_id: str) -> int:
        """Release all active lock leases held by a goal."""
        clean_gid = str(goal_id).strip()
        if not clean_gid:
            return 0

        with self._lock:
            target_ids = [lid for lid, lk in self._locks.items() if lk.owner_goal_id == clean_gid]
            for lid in target_ids:
                self.release_lock(lid, clean_gid)
            return len(target_ids)

    def is_locked(
        self,
        resource_uri: str,
        lock_type: LockType | None = None,
        current_time: float | None = None,
    ) -> bool:
        """Check if a resource URI currently has active locks."""
        uri_norm = self._normalize_uri(resource_uri)
        now = current_time if current_time is not None else time.time()

        with self._lock:
            self.prune_expired_locks(now)
            active_lock_ids = self._resource_index.get(uri_norm, [])
            if not active_lock_ids:
                return False
            if lock_type is None:
                return True
            return any(self._locks[lid].lock_type == lock_type for lid in active_lock_ids if lid in self._locks)
