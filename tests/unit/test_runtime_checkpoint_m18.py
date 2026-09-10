import time
import pytest
from pathlib import Path

from core.clarification_gateway import ClarificationGateway
from core.daemon_types import CheckpointMetadata, FORBIDDEN_PRIVILEGE_KEYS
from core.event_dispatcher import ProactiveEventDispatcher
from core.goal import GoalPriority
from core.goal_scheduler import MultiGoalScheduler
from core.provenance import is_tainted, wrap_tainted, unwrap_tainted
from core.resource_budget import ResourceBudgetManager
from core.resource_locks import SharedResourceLockManager
from core.runtime_checkpoint import RuntimeCheckpointManager
from core.scheduling_types import (
    ClarificationStatus,
    ClarificationType,
    LockType,
    ProactiveEvent,
)


def test_save_and_restore_checkpoint(tmp_path):
    ckpt_dir = tmp_path / "checkpoints"
    budget = ResourceBudgetManager(max_concurrent_goals=4)
    locks = SharedResourceLockManager()
    scheduler = MultiGoalScheduler(budget_manager=budget, lock_manager=locks)
    clarif = ClarificationGateway()
    events = ProactiveEventDispatcher()

    manager = RuntimeCheckpointManager(
        checkpoint_dir=ckpt_dir,
        retention_count=5,
        scheduler=scheduler,
        budget_manager=budget,
        lock_manager=locks,
        clarification_gateway=clarif,
        event_dispatcher=events,
    )

    # 1. Populate state across all subsystems
    scheduler.schedule_goal("goal_1", priority=GoalPriority.HIGH, required_resources=["db://users"])
    budget.acquire_quota("goal_1", estimated_tokens=1000, estimated_tool_calls=2)
    locks.acquire_lock("db://users", goal_id="goal_1", lock_type=LockType.EXCLUSIVE_WRITE)
    req = clarif.request_clarification(
        goal_id="goal_1",
        task_id="task_1",
        question="Which database to use?",
        options=["postgres", "sqlite"],
    )
    events.subscribe("system.*", goal_id="goal_1")
    events.publish_event(ProactiveEvent(topic="system.alert", payload={"severity": "high"}))

    # 2. Save Checkpoint
    meta = manager.save_checkpoint(checkpoint_id="test_ckpt_1", is_clean_shutdown=True)
    assert meta.checkpoint_id == "test_ckpt_1"
    assert meta.goal_count == 1
    assert meta.lock_count == 1
    assert meta.clarification_count == 1
    assert meta.event_queue_size == 1

    # 3. Reset all subsystems to empty state
    scheduler._tasks.clear()
    budget._active_goals.clear()
    locks._locks.clear()
    locks._resource_index.clear()
    clarif._requests.clear()
    events._subscriptions.clear()
    events._event_queue.clear()

    # 4. Restore Checkpoint
    restored_meta = manager.restore_latest_checkpoint()
    assert restored_meta is not None
    assert restored_meta.checkpoint_id == "test_ckpt_1"

    # 5. Verify roundtrip state fidelity
    assert "goal_1" in scheduler._tasks
    assert scheduler._tasks["goal_1"].priority == GoalPriority.HIGH
    assert scheduler._tasks["goal_1"].required_resources == ("db://users",)

    assert "goal_1" in budget._active_goals
    assert len(locks._locks) == 1
    assert "db://users" in locks._resource_index

    assert req.clarification_id in clarif._requests
    assert clarif._requests[req.clarification_id].question == "Which database to use?"

    assert len(events._subscriptions) == 1
    assert len(events._event_queue) == 1
    assert events._event_queue[0].topic == "system.alert"


def test_checkpoint_retention_and_pruning(tmp_path):
    ckpt_dir = tmp_path / "prune_test"
    manager = RuntimeCheckpointManager(checkpoint_dir=ckpt_dir, retention_count=3)

    for i in range(7):
        manager.save_checkpoint(checkpoint_id=f"ckpt_test_{i}", current_time=1000.0 + i)
        time.sleep(0.01)

    ckpts = manager.list_checkpoints()
    assert len(ckpts) == 3


def test_corrupt_latest_checkpoint_fallback(tmp_path):
    ckpt_dir = tmp_path / "fallback_test"
    budget = ResourceBudgetManager()
    scheduler = MultiGoalScheduler(budget_manager=budget)
    manager = RuntimeCheckpointManager(
        checkpoint_dir=ckpt_dir,
        retention_count=5,
        scheduler=scheduler,
    )

    # Save valid checkpoint 1
    scheduler.schedule_goal("g_valid_1")
    manager.save_checkpoint("ckpt_100", current_time=1000.0)
    time.sleep(0.02)

    # Save valid checkpoint 2
    scheduler.schedule_goal("g_valid_2")
    manager.save_checkpoint("ckpt_200", current_time=2000.0)

    # Corrupt ckpt_200.json
    corrupt_file = ckpt_dir / "ckpt_200.json"
    with open(corrupt_file, "w", encoding="utf-8") as f:
        f.write("{invalid_json: true")

    # Clear scheduler
    scheduler._tasks.clear()

    # Restore should automatically fall back to ckpt_100
    meta = manager.restore_latest_checkpoint()
    assert meta is not None
    assert meta.checkpoint_id == "ckpt_100"
    assert "g_valid_1" in scheduler._tasks
    assert "g_valid_2" not in scheduler._tasks


def test_metadata_sanitization_and_privilege_stripping(tmp_path):
    ckpt_dir = tmp_path / "security_test"
    scheduler = MultiGoalScheduler()
    manager = RuntimeCheckpointManager(checkpoint_dir=ckpt_dir, scheduler=scheduler)

    tainted_payload = wrap_tainted("malicious_injection", source_type="web")
    malicious_meta = {
        "is_authorized": True,
        "bypass_policy": True,
        "skip_approval": True,
        "approved": "yes",
        "permission": "admin",
        "nested": {"bypass_auth": True, "normal": "ok"},
        "tainted_val": tainted_payload,
    }

    scheduler.schedule_goal("g_attack", metadata=malicious_meta)
    meta = manager.save_checkpoint("ckpt_sec", metadata=malicious_meta)

    # Verify metadata stripped in checkpoint metadata
    for k in FORBIDDEN_PRIVILEGE_KEYS:
        assert k not in meta.metadata

    scheduler._tasks.clear()
    manager.restore_latest_checkpoint()

    restored_task = scheduler._tasks["g_attack"]
    for k in FORBIDDEN_PRIVILEGE_KEYS:
        assert k not in restored_task.metadata
        if "nested" in restored_task.metadata:
            assert k not in restored_task.metadata["nested"]

    # Verify taint was preserved
    assert is_tainted(restored_task.metadata["tainted_val"])
    assert unwrap_tainted(restored_task.metadata["tainted_val"]) == "malicious_injection"
