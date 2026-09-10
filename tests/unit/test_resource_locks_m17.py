import time
import pytest
from core.resource_locks import SharedResourceLockManager
from core.scheduling_types import LockType


def test_shared_read_locks_compatibility():
    mgr = SharedResourceLockManager(default_ttl_seconds=10.0)
    res1 = mgr.acquire_lock("db:orders", "goal_1", lock_type=LockType.SHARED_READ)
    assert res1.success is True
    assert res1.lock is not None

    # Goal 2 can also acquire shared read on the same resource
    res2 = mgr.acquire_lock("db:orders", "goal_2", lock_type=LockType.SHARED_READ)
    assert res2.success is True


def test_exclusive_write_lock_conflicts():
    mgr = SharedResourceLockManager(default_ttl_seconds=10.0)
    res1 = mgr.acquire_lock("file:/app/state.json", "goal_1", lock_type=LockType.EXCLUSIVE_WRITE)
    assert res1.success is True

    # Goal 2 cannot acquire shared read while Goal 1 holds exclusive write
    res2 = mgr.acquire_lock("file:/app/state.json", "goal_2", lock_type=LockType.SHARED_READ)
    assert res2.success is False
    assert res2.conflict_owner_goal_id == "goal_1"

    # Goal 2 cannot acquire exclusive write either
    res3 = mgr.acquire_lock("file:/app/state.json", "goal_2", lock_type=LockType.EXCLUSIVE_WRITE)
    assert res3.success is False


def test_exclusive_write_blocked_by_shared_read():
    mgr = SharedResourceLockManager(default_ttl_seconds=10.0)
    mgr.acquire_lock("tool:deploy", "goal_1", lock_type=LockType.SHARED_READ)

    # Exclusive write fails while shared read is active
    res_write = mgr.acquire_lock("tool:deploy", "goal_2", lock_type=LockType.EXCLUSIVE_WRITE)
    assert res_write.success is False
    assert res_write.conflict_owner_goal_id == "goal_1"


def test_lock_release_and_acquisition():
    mgr = SharedResourceLockManager()
    res1 = mgr.acquire_lock("res:1", "goal_1", lock_type=LockType.EXCLUSIVE_WRITE)
    assert res1.success is True

    # Goal 2 blocked
    assert mgr.acquire_lock("res:1", "goal_2", lock_type=LockType.EXCLUSIVE_WRITE).success is False

    # Goal 1 releases lock
    assert mgr.release_lock(res1.lock.lock_id, "goal_1") is True

    # Now Goal 2 succeeds
    assert mgr.acquire_lock("res:1", "goal_2", lock_type=LockType.EXCLUSIVE_WRITE).success is True


def test_lock_ttl_auto_expiry():
    mgr = SharedResourceLockManager(default_ttl_seconds=5.0)
    res1 = mgr.acquire_lock("res:ephemeral", "goal_1", lock_type=LockType.EXCLUSIVE_WRITE, current_time=100.0)
    assert res1.success is True

    # At t=102.0, still locked
    assert mgr.is_locked("res:ephemeral", current_time=102.0) is True

    # At t=106.0, expired and Goal 2 can acquire
    res2 = mgr.acquire_lock("res:ephemeral", "goal_2", lock_type=LockType.EXCLUSIVE_WRITE, current_time=106.0)
    assert res2.success is True


def test_batch_atomic_acquisition_and_rollback():
    mgr = SharedResourceLockManager()
    # Lock resource C with Goal 1
    mgr.acquire_lock("res:c", "goal_1", lock_type=LockType.EXCLUSIVE_WRITE)

    # Goal 2 attempts batch on res:a, res:b, res:c
    batch_res = mgr.acquire_locks_batch(["res:a", "res:b", "res:c"], "goal_2", lock_type=LockType.EXCLUSIVE_WRITE)
    assert batch_res.success is False
    assert batch_res.conflict_owner_goal_id == "goal_1"

    # Verify res:a and res:b were rolled back and are not locked by Goal 2
    assert mgr.is_locked("res:a") is False
    assert mgr.is_locked("res:b") is False
