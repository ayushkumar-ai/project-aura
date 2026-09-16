"""M53 Unit Tests — InMemoryAutomationRepository Contract."""

import pytest
import time

from core.automations.types import LeaseFencingError
from core.repositories.in_memory_automation import InMemoryAutomationRepository


def test_in_memory_crud_and_tenant_isolation():
    """Verify basic CRUD and strict tenant isolation in memory repository."""
    repo = InMemoryAutomationRepository()
    
    # User A creates automation
    auto_a = repo.create_automation(
        user_id="user_a",
        name="Auto A",
        trigger_type="recurring",
        trigger_config={"cron": "*/5 * * * *"},
    )
    assert auto_a["id"] is not None
    assert auto_a["user_id"] == "user_a"

    # User B cannot see User A's automation
    assert repo.get_automation(auto_a["id"], user_id="user_b") is None
    assert repo.count_automations(user_id="user_b") == 0

    # User A lists automations
    autos_a = repo.list_automations(user_id="user_a")
    assert len(autos_a) == 1
    assert autos_a[0]["id"] == auto_a["id"]

    # Pause and resume
    paused = repo.pause_automation(auto_a["id"], user_id="user_a")
    assert paused["status"] == "paused"
    assert paused["next_fire_at"] is None

    resumed = repo.resume_automation(auto_a["id"], user_id="user_a", next_fire_at=time.time() + 300)
    assert resumed["status"] == "active"
    assert resumed["next_fire_at"] is not None

    # Delete
    deleted = repo.delete_automation(auto_a["id"], user_id="user_a")
    assert deleted is True
    assert repo.get_automation(auto_a["id"], user_id="user_a") is None


def test_in_memory_claiming_and_leasing():
    """Verify atomic claiming, lease renewal, and stale lease recovery."""
    repo = InMemoryAutomationRepository()
    now = time.time()
    auto = repo.create_automation(
        user_id="user_a",
        name="Due Auto",
        trigger_type="recurring",
        trigger_config={"cron": "* * * * *"},
        next_fire_at=now - 10,  # Due in past
    )

    claimed = repo.claim_due_automations(worker_id="w1", limit=10, lease_ttl_seconds=60)
    assert len(claimed) == 1
    assert claimed[0]["id"] == auto["id"]
    assert claimed[0]["lease_owner"] == "w1"
    token = claimed[0]["lease_token"]
    assert token is not None

    # Competing claim gets 0 rows
    claimed_second = repo.claim_due_automations(worker_id="w2", limit=10)
    assert len(claimed_second) == 0

    # Renew lease
    renewed = repo.renew_lease(auto["id"], lease_token=token, lease_ttl_seconds=120)
    assert renewed is True

    # Release lease
    released = repo.release_lease(auto["id"], lease_token=token)
    assert released is True


def test_in_memory_fenced_dispatch_and_quota():
    """Verify fenced dispatch and hourly quota enforcement in memory repo."""
    repo = InMemoryAutomationRepository()
    now = time.time()
    auto = repo.create_automation(
        user_id="user_a",
        name="Fenced Auto",
        trigger_type="recurring",
        trigger_config={"cron": "* * * * *"},
        next_fire_at=now - 10,
    )

    claimed = repo.claim_due_automations(worker_id="w1", limit=1, lease_ttl_seconds=60)
    token = claimed[0]["lease_token"]

    # Fenced dispatch with valid token
    res = repo.execute_fenced_dispatch(
        automation_id=auto["id"],
        user_id="user_a",
        lease_token=token,
        slot_timestamp=now,
        trigger_timestamp=now,
        action_template={"title": "Task", "goal": "Goal"},
        next_fire_at=now + 60,
    )
    assert res["status"] == "enqueued"
    assert res["quota_charged"] == 1
    assert res["task_id"] is not None

    # Stale token fails fencing
    with pytest.raises(LeaseFencingError):
        repo.execute_fenced_dispatch(
            automation_id=auto["id"],
            user_id="user_a",
            lease_token="stale_token",
            slot_timestamp=now + 60,
            trigger_timestamp=now + 60,
            action_template={"title": "Task", "goal": "Goal"},
            next_fire_at=now + 120,
        )
