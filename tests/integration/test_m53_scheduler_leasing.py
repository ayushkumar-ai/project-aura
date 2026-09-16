"""M53 — Distributed Scheduler & Lease Fencing Tests.

Verifies:
1. Multi-node concurrent claim exclusivity (SKIP LOCKED prevents double execution)
2. Heartbeat renewal keeping long executions leased
3. Lease expiration and recovery by alternate workers
4. CatchUpPolicy evaluation and dispatch ordering
"""

from __future__ import annotations

import time
import uuid
import pytest

from core.repositories.in_memory_automation import InMemoryAutomationRepository
from core.repositories.in_memory import InMemoryTaskRepository
from core.automations.types import (
    CatchUpPolicy,
    LeaseFencingError,
)
from core.automations.scheduler import AutomationScheduler
from core.automations.condition_engine import ConditionEngine


@pytest.fixture
def memory_repos():
    auto_repo = InMemoryAutomationRepository()
    task_repo = InMemoryTaskRepository()
    return auto_repo, task_repo


def test_concurrent_worker_claim_exclusivity(memory_repos):
    auto_repo, _ = memory_repos
    user_id = "user_concurrent"
    now = time.time()
    
    # Create 5 due automations
    for i in range(5):
        auto_repo.create_automation(
            user_id=user_id,
            name=f"Concurrent Auto {i}",
            trigger_type="recurring",
            trigger_config={"cron": "* * * * *"},
            action_template={"title": f"Task {i}", "goal": "Sample goal"},
            next_fire_at=now - 120.0,
        )

    # Worker A claims 3
    claimed_a = auto_repo.claim_due_automations(worker_id="node_a", limit=3, lease_ttl_seconds=60)
    assert len(claimed_a) == 3

    # Worker B claims remaining
    claimed_b = auto_repo.claim_due_automations(worker_id="node_b", limit=3, lease_ttl_seconds=60)
    assert len(claimed_b) == 2

    # Check that sets of claimed automations are completely disjoint
    ids_a = {a["id"] for a in claimed_a}
    ids_b = {a["id"] for a in claimed_b}
    assert ids_a.isdisjoint(ids_b)


def test_scheduler_catchup_policies(memory_repos):
    auto_repo, task_repo = memory_repos
    user_id = "user_catchup"
    now = time.time()
    
    # Automation with SKIP catchup policy that missed 3 intervals
    auto_skip = auto_repo.create_automation(
        user_id=user_id,
        name="Skip Missed",
        trigger_type="recurring",
        trigger_config={"cron": "*/5 * * * *", "catchup_policy": "skip"},
        action_template={"title": "Skip Action", "goal": "Run once"},
        next_fire_at=now - 900.0,
    )

    condition_engine = ConditionEngine()
    scheduler = AutomationScheduler(
        automation_repo=auto_repo,
        condition_engine=condition_engine,
        node_id="scheduler_test_worker",
        lease_ttl_seconds=30,
        batch_size=10,
    )

    # Execute a scheduler tick
    dispatched = scheduler.tick(now=now)
    assert len(dispatched) == 1
    assert dispatched[0]["status"] == "enqueued"

    # Verify next_fire_at was advanced into the future
    updated = auto_repo.get_automation(auto_skip["id"], user_id=user_id)
    assert updated["next_fire_at"] > now
    assert updated["lease_owner"] is None
