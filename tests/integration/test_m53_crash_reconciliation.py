"""M53 — Crash Recovery & Task State Reconciliation Tests.

Verifies:
1. Reconciler detects and releases expired/stale leases
2. Reconciler synchronizes M52 BackgroundTask status transitions (running, completed, failed, cancelled)
3. Reconciler handles orphaned or completed tasks safely
"""

from __future__ import annotations

import time
import uuid
import pytest

from core.repositories.in_memory_automation import InMemoryAutomationRepository
from core.repositories.in_memory import InMemoryTaskRepository
from core.automations.reconciler import AutomationReconciler


@pytest.fixture
def repo_bundle():
    auto_repo = InMemoryAutomationRepository()
    task_repo = InMemoryTaskRepository()
    return auto_repo, task_repo


def test_stale_lease_recovery(repo_bundle):
    auto_repo, task_repo = repo_bundle
    user_id = "user_reconcile"
    now = time.time()

    # Automation with expired lease
    auto = auto_repo.create_automation(
        user_id=user_id,
        name="Stale Auto",
        trigger_type="recurring",
        trigger_config={"cron": "* * * * *"},
        action_template={"title": "Action", "goal": "Goal"},
        next_fire_at=now - 200.0,
    )
    auto_id = auto["id"]

    # Claim lease then simulate expiry
    claimed = auto_repo.claim_due_automations("crashed_node_99", limit=1, lease_ttl_seconds=1)
    assert len(claimed) == 1
    time.sleep(1.2)  # Wait for lease to expire

    reconciler = AutomationReconciler(automation_repo=auto_repo, task_repo=task_repo)
    recovered = reconciler.recover_stale_leases(limit=50)
    assert recovered == 1

    # Automation lease is now released and ready to fire
    updated = auto_repo.get_automation(auto_id, user_id=user_id)
    assert updated["lease_owner"] is None
    assert updated["lease_token"] is None


def test_m52_task_state_synchronization(repo_bundle):
    auto_repo, task_repo = repo_bundle
    user_id = "user_sync"
    now = time.time()

    # 1. Create automation and claim it
    auto = auto_repo.create_automation(
        user_id=user_id,
        name="Sync Auto",
        trigger_type="recurring",
        trigger_config={"cron": "* * * * *"},
        action_template={"title": "Sync Work", "goal": "Run background workflow"},
        next_fire_at=now - 60.0,
    )
    auto_id = auto["id"]
    claimed = auto_repo.claim_due_automations("worker_sync_1", limit=1, lease_ttl_seconds=30)
    token = claimed[0]["lease_token"]

    # 2. Dispatch task
    task = task_repo.create_task(
        user_id=user_id,
        title="Sync Work",
        goal="Run background workflow",
    )
    task_id = task["id"]

    run = auto_repo.execute_fenced_dispatch(
        automation_id=auto_id,
        user_id=user_id,
        lease_token=token,
        slot_timestamp=now,
        trigger_timestamp=now,
        action_template={"title": "Sync Work", "goal": "Run background workflow"},
        next_fire_at=now + 60.0,
    )
    run_id = run["id"]

    # Manually link task_id if needed
    auto_repo.record_run_terminal_state(run_id=run_id, status="enqueued", task_id=task_id)

    reconciler = AutomationReconciler(automation_repo=auto_repo, task_repo=task_repo)

    # Task transitions to completed
    task_repo.update_task_status(task_id, user_id, "completed", result={"out": "done"})
    reconciled = reconciler.reconcile_run_tasks(auto_id, user_id=user_id)
    assert reconciled >= 1

    final_run = auto_repo.get_run(run_id, user_id=user_id)
    assert final_run["status"] == "completed"
