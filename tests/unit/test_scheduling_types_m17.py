import time
import pytest
from uuid import uuid4

from core.goal import GoalPriority
from core.provenance import TaintedValue, wrap_tainted, is_tainted
from core.scheduling_types import (
    ClarificationRequest,
    ClarificationResponse,
    ClarificationStatus,
    ClarificationType,
    EventSubscription,
    GoalScheduleStatus,
    LockAcquireResult,
    LockType,
    ProactiveEvent,
    ResourceAllocationResult,
    ResourceLock,
    ResourceQuota,
    ScheduledGoalTask,
    _canonical_value,
    _restore_value,
    _sanitize_metadata,
)


def test_resource_quota_validation():
    q = ResourceQuota(max_tool_calls=15, max_tokens=20000, max_execution_time_seconds=120.0, max_concurrent_steps=3)
    assert q.max_tool_calls == 15
    assert q.max_tokens == 20000
    assert q.max_execution_time_seconds == 120.0
    assert q.max_concurrent_steps == 3

    with pytest.raises(ValueError):
        ResourceQuota(max_tool_calls=-1)

    with pytest.raises(ValueError):
        ResourceQuota(max_tokens=0)


def test_resource_lock_lifecycle_and_expiry():
    now = time.time()
    lock = ResourceLock(
        resource_uri="tool:calculator",
        lock_type=LockType.EXCLUSIVE_WRITE,
        owner_goal_id="goal_1",
        acquired_at=now,
        ttl_seconds=10.0,
    )
    assert lock.resource_uri == "tool:calculator"
    assert lock.lock_type == LockType.EXCLUSIVE_WRITE
    assert not lock.is_expired(now + 5.0)
    assert lock.is_expired(now + 15.0)

    with pytest.raises(ValueError):
        ResourceLock(resource_uri="", owner_goal_id="g1")


def test_scheduled_goal_task_invariants():
    task = ScheduledGoalTask(
        goal_id="goal_123",
        priority=GoalPriority.HIGH,
        base_weight=2.0,
        effective_priority=2.5,
        required_resources=("tool:web_search", "db:users"),
    )
    assert task.goal_id == "goal_123"
    assert task.priority == GoalPriority.HIGH
    assert task.status == GoalScheduleStatus.QUEUED
    assert "db:users" in task.required_resources

    updated = task.with_status(GoalScheduleStatus.RUNNING, effective_priority=3.0, started_at=time.time())
    assert updated.status == GoalScheduleStatus.RUNNING
    assert updated.effective_priority == 3.0
    assert updated.started_at is not None


def test_proactive_event_and_subscription_matching():
    evt = ProactiveEvent(
        event_type="alert",
        topic="system.metrics.cpu",
        payload={"load": 95},
        source="monitor",
    )
    assert evt.topic == "system.metrics.cpu"
    assert evt.payload == {"load": 95}
    assert not evt.is_untrusted

    sub_exact = EventSubscription(topic_pattern="system.metrics.cpu", goal_id="g1")
    assert sub_exact.matches(evt)

    sub_wildcard = EventSubscription(topic_pattern="system.metrics.*", goal_id="g1")
    assert sub_wildcard.matches(evt)

    sub_nomatch = EventSubscription(topic_pattern="system.logs.*", goal_id="g1")
    assert not sub_nomatch.matches(evt)


def test_clarification_request_and_response():
    now = time.time()
    req = ClarificationRequest(
        goal_id="goal_99",
        task_id="task_99_act_1",
        question="Which environment should be targeted?",
        options=("staging", "production"),
        clarification_type=ClarificationType.SINGLE_CHOICE,
        timeout_seconds=60.0,
        created_at=now,
    )
    assert req.question == "Which environment should be targeted?"
    assert req.options == ("staging", "production")
    assert not req.is_expired(now + 30.0)
    assert req.is_expired(now + 100.0)

    resp = ClarificationResponse(
        clarification_id=req.clarification_id,
        goal_id=req.goal_id,
        response_data="staging",
    )
    assert resp.clarification_id == req.clarification_id
    assert resp.response_data == "staging"
    assert resp.status == ClarificationStatus.ANSWERED


def test_metadata_sanitization_strips_forbidden_keys():
    meta = {
        "approved": True,
        "is_approved": True,
        "permission": "admin",
        "authorized": True,
        "valid_key": "valid_value",
        "nested": {"auto_approve": True, "info": "ok"},
    }
    cleaned = _sanitize_metadata(meta)
    assert "approved" not in cleaned
    assert "is_approved" not in cleaned
    assert "permission" not in cleaned
    assert "authorized" not in cleaned
    assert "auto_approve" not in cleaned["nested"]
    assert cleaned["valid_key"] == "valid_value"
    assert cleaned["nested"]["info"] == "ok"


def test_provenance_preservation_in_events_and_clarifications():
    tainted_data = wrap_tainted("untrusted_payload", is_untrusted=True, source_type="web", source_urls=["http://malicious.com"])
    evt = ProactiveEvent(
        topic="webhook.incoming",
        payload=tainted_data,
    )
    assert evt.is_untrusted is True

    resp = ClarificationResponse(
        clarification_id="c1",
        goal_id="g1",
        response_data=tainted_data,
    )
    assert resp.is_untrusted is True


def test_canonical_and_restore_value_with_taint():
    tainted = wrap_tainted("secret", is_untrusted=True, source_type="api", source_urls=["http://api.com"])
    canonical = _canonical_value({"key": tainted, "num": 42})
    assert isinstance(canonical, dict)
    assert canonical["key"]["__tainted__"] is True

    restored = _restore_value(canonical)
    assert isinstance(restored["key"], TaintedValue)
    assert restored["key"].raw_value == "secret"
    assert restored["key"].is_untrusted is True
    assert restored["num"] == 42
