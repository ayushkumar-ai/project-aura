import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from core.goal import GoalPriority
from core.provenance import TaintedValue, is_tainted, wrap_tainted

logger = logging.getLogger("aura.scheduling_types")


def _sanitize_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Recursively sanitize metadata dictionary to ensure no callables or untrusted permission overrides."""
    if not isinstance(meta, dict):
        raise TypeError("metadata must be a dictionary.")

    forbidden_keys = frozenset({
        "approved",
        "approval_status",
        "is_approved",
        "auto_approve",
        "permission",
        "authorized",
    })

    cleaned: dict[str, Any] = {}
    for k, v in meta.items():
        k_str = str(k)
        if k_str.lower() in forbidden_keys:
            continue
        if callable(v):
            continue
        if isinstance(v, dict):
            cleaned[k_str] = _sanitize_metadata(v)
        elif isinstance(v, (list, tuple)):
            cleaned[k_str] = [
                _sanitize_metadata(item) if isinstance(item, dict) else (str(item) if not callable(item) else "")
                for item in v
            ]
        elif isinstance(v, TaintedValue):
            cleaned[k_str] = v
        elif isinstance(v, (str, int, float, bool)) or v is None:
            cleaned[k_str] = v
        else:
            cleaned[k_str] = repr(v)
    return cleaned


def _canonical_value(val: Any) -> Any:
    """Convert values into deterministic, JSON-serializable primitives preserving TaintedValue."""
    if isinstance(val, TaintedValue):
        return {
            "__tainted__": True,
            "raw_value": _canonical_value(val.raw_value),
            "is_untrusted": val.is_untrusted,
            "source_type": val.source_type,
            "originating_step_id": val.originating_step_id,
            "source_urls": list(val.source_urls),
            "metadata": {str(k): _canonical_value(v) for k, v in sorted(val.metadata.items())},
        }
    elif isinstance(val, (str, int, float, bool)) or val is None:
        return val
    elif isinstance(val, (list, tuple, set, frozenset)):
        return [_canonical_value(x) for x in val]
    elif isinstance(val, dict):
        return {str(k): _canonical_value(v) for k, v in sorted(val.items())}
    elif callable(val):
        raise ValueError("Cannot serialize callable value in scheduling models.")
    else:
        return repr(val)


def _restore_value(val: Any) -> Any:
    """Restore values from serialized JSON primitives restoring TaintedValue envelopes."""
    if isinstance(val, dict):
        if val.get("__tainted__") is True and "raw_value" in val:
            return wrap_tainted(
                value=_restore_value(val.get("raw_value")),
                is_untrusted=bool(val.get("is_untrusted", True)),
                source_type=str(val.get("source_type", "external_web")),
                originating_step_id=val.get("originating_step_id"),
                source_urls=val.get("source_urls", ()),
                metadata=dict(val.get("metadata", {})),
            )
        return {k: _restore_value(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [_restore_value(x) for x in val]
    return val


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class LockType(str, Enum):
    """Category of resource lock for shared state/tools."""
    SHARED_READ = "shared_read"
    EXCLUSIVE_WRITE = "exclusive_write"


class LockStatus(str, Enum):
    """Lifecycle status of a resource lock."""
    ACQUIRED = "acquired"
    CONFLICT = "conflict"
    EXPIRED = "expired"
    RELEASED = "released"


class GoalScheduleStatus(str, Enum):
    """Lifecycle status of a scheduled goal in the multi-goal scheduler."""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class ClarificationType(str, Enum):
    """Type of interactive user clarification requested."""
    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"
    FREE_TEXT = "free_text"


class ClarificationStatus(str, Enum):
    """Lifecycle status of an interactive clarification request."""
    PENDING = "pending"
    ANSWERED = "answered"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResourceQuota:
    """Deterministic resource bounds and quotas for a goal or runtime pool."""
    max_tool_calls: int = 20
    max_tokens: int = 50000
    max_execution_time_seconds: float = 300.0
    max_concurrent_steps: int = 2
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.max_tool_calls, int) or self.max_tool_calls <= 0:
            raise ValueError("max_tool_calls must be a positive integer.")
        if not isinstance(self.max_tokens, int) or self.max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer.")
        if not isinstance(self.max_execution_time_seconds, (int, float)) or self.max_execution_time_seconds <= 0:
            raise ValueError("max_execution_time_seconds must be a positive number.")
        if not isinstance(self.max_concurrent_steps, int) or self.max_concurrent_steps <= 0:
            raise ValueError("max_concurrent_steps must be a positive integer.")
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))


@dataclass(frozen=True)
class ResourceAllocationResult:
    """Outcome of a resource quota acquisition request."""
    is_granted: bool = False
    allocated_tokens: int = 0
    allocated_tool_calls: int = 0
    reason: str = ""
    quota_remaining: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.is_granted, bool):
            raise TypeError("is_granted must be a boolean.")
        if not isinstance(self.allocated_tokens, int) or self.allocated_tokens < 0:
            raise ValueError("allocated_tokens must be a non-negative integer.")
        if not isinstance(self.allocated_tool_calls, int) or self.allocated_tool_calls < 0:
            raise ValueError("allocated_tool_calls must be a non-negative integer.")
        if not isinstance(self.reason, str):
            raise TypeError("reason must be a string.")


@dataclass(frozen=True)
class ResourceLock:
    """Immutable record of an active resource lock lease."""
    lock_id: str = field(default_factory=lambda: str(uuid4()))
    resource_uri: str = ""
    lock_type: LockType = LockType.SHARED_READ
    owner_goal_id: str = ""
    acquired_at: float = field(default_factory=time.time)
    ttl_seconds: float = 30.0
    expires_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.lock_id, str) or not self.lock_id.strip():
            raise ValueError("lock_id must be a non-empty string.")
        object.__setattr__(self, "lock_id", self.lock_id.strip())

        if not isinstance(self.resource_uri, str) or not self.resource_uri.strip():
            raise ValueError("resource_uri must be a non-empty string.")
        object.__setattr__(self, "resource_uri", self.resource_uri.strip().lower())

        if isinstance(self.lock_type, str):
            object.__setattr__(self, "lock_type", LockType(self.lock_type))
        elif not isinstance(self.lock_type, LockType):
            raise TypeError("lock_type must be a LockType instance.")

        if not isinstance(self.owner_goal_id, str) or not self.owner_goal_id.strip():
            raise ValueError("owner_goal_id must be a non-empty string.")
        object.__setattr__(self, "owner_goal_id", self.owner_goal_id.strip())

        if not isinstance(self.acquired_at, (int, float)):
            raise TypeError("acquired_at must be numeric.")
        object.__setattr__(self, "acquired_at", float(self.acquired_at))

        if not isinstance(self.ttl_seconds, (int, float)) or self.ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be a positive number.")
        object.__setattr__(self, "ttl_seconds", float(self.ttl_seconds))

        exp = self.acquired_at + self.ttl_seconds
        object.__setattr__(self, "expires_at", float(exp))
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))

    def is_expired(self, current_time: float | None = None) -> bool:
        """Check if lock lease has expired."""
        now = current_time if current_time is not None else time.time()
        return now >= self.expires_at


@dataclass(frozen=True)
class LockAcquireResult:
    """Outcome of attempting to acquire a resource lock."""
    success: bool = False
    lock: ResourceLock | None = None
    conflict_owner_goal_id: str | None = None
    reason: str = ""

    def __post_init__(self):
        if not isinstance(self.success, bool):
            raise TypeError("success must be a boolean.")


@dataclass(frozen=True)
class ScheduledGoalTask:
    """A goal scheduled for concurrent evaluation in the multi-goal scheduler."""
    schedule_id: str = field(default_factory=lambda: str(uuid4()))
    goal_id: str = ""
    priority: GoalPriority = GoalPriority.MEDIUM
    base_weight: float = 1.0
    effective_priority: float = 1.0
    status: GoalScheduleStatus = GoalScheduleStatus.QUEUED
    enqueued_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    required_resources: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.schedule_id, str) or not self.schedule_id.strip():
            raise ValueError("schedule_id must be a non-empty string.")
        object.__setattr__(self, "schedule_id", self.schedule_id.strip())

        if not isinstance(self.goal_id, str) or not self.goal_id.strip():
            raise ValueError("goal_id must be a non-empty string.")
        object.__setattr__(self, "goal_id", self.goal_id.strip())

        if isinstance(self.priority, str):
            object.__setattr__(self, "priority", GoalPriority(self.priority))
        elif not isinstance(self.priority, GoalPriority):
            raise TypeError("priority must be a GoalPriority instance.")

        if isinstance(self.status, str):
            object.__setattr__(self, "status", GoalScheduleStatus(self.status))
        elif not isinstance(self.status, GoalScheduleStatus):
            raise TypeError("status must be a GoalScheduleStatus instance.")

        if not isinstance(self.base_weight, (int, float)) or self.base_weight <= 0:
            raise ValueError("base_weight must be a positive number.")
        object.__setattr__(self, "base_weight", float(self.base_weight))

        if not isinstance(self.effective_priority, (int, float)):
            raise TypeError("effective_priority must be numeric.")
        object.__setattr__(self, "effective_priority", float(self.effective_priority))

        if not isinstance(self.enqueued_at, (int, float)):
            raise TypeError("enqueued_at must be numeric.")
        object.__setattr__(self, "enqueued_at", float(self.enqueued_at))

        if isinstance(self.required_resources, (list, tuple, set)):
            clean_res = tuple(sorted({str(r).strip().lower() for r in self.required_resources if str(r).strip()}))
            object.__setattr__(self, "required_resources", clean_res)
        else:
            raise TypeError("required_resources must be a sequence of strings.")

        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))

    def with_status(
        self,
        status: GoalScheduleStatus,
        effective_priority: float | None = None,
        started_at: float | None = None,
        completed_at: float | None = None,
    ) -> "ScheduledGoalTask":
        """Return a copy with updated schedule status and metrics."""
        new_pri = effective_priority if effective_priority is not None else self.effective_priority
        new_start = started_at if started_at is not None else self.started_at
        new_comp = completed_at if completed_at is not None else self.completed_at
        return ScheduledGoalTask(
            schedule_id=self.schedule_id,
            goal_id=self.goal_id,
            priority=self.priority,
            base_weight=self.base_weight,
            effective_priority=new_pri,
            status=status,
            enqueued_at=self.enqueued_at,
            started_at=new_start,
            completed_at=new_comp,
            required_resources=self.required_resources,
            metadata=dict(self.metadata),
        )


@dataclass(frozen=True)
class ProactiveEvent:
    """Structured event envelope for asynchronous trigger dispatch."""
    event_id: str = field(default_factory=lambda: str(uuid4()))
    event_type: str = "generic"
    topic: str = ""
    payload: Any = None
    source: str = "external"
    is_untrusted: bool = False
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("event_id must be a non-empty string.")
        object.__setattr__(self, "event_id", self.event_id.strip())

        if not isinstance(self.event_type, str) or not self.event_type.strip():
            raise ValueError("event_type must be a non-empty string.")
        object.__setattr__(self, "event_type", self.event_type.strip().lower())

        if not isinstance(self.topic, str) or not self.topic.strip():
            raise ValueError("topic must be a non-empty string.")
        object.__setattr__(self, "topic", self.topic.strip().lower())

        if callable(self.payload):
            raise ValueError("Event payload cannot be callable.")

        untrusted = bool(self.is_untrusted)
        if isinstance(self.payload, TaintedValue) or is_tainted(self.payload):
            untrusted = True
        object.__setattr__(self, "is_untrusted", untrusted)

        if not isinstance(self.source, str):
            raise TypeError("source must be a string.")
        object.__setattr__(self, "source", self.source.strip())

        if not isinstance(self.timestamp, (int, float)):
            raise TypeError("timestamp must be numeric.")
        object.__setattr__(self, "timestamp", float(self.timestamp))

        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))


@dataclass(frozen=True)
class EventSubscription:
    """Subscription rule mapping an event topic pattern to a goal trigger."""
    subscription_id: str = field(default_factory=lambda: str(uuid4()))
    topic_pattern: str = ""
    goal_id: str = ""
    trigger_id: str | None = None
    created_at: float = field(default_factory=time.time)
    cooldown_seconds: float = 0.0
    last_dispatched_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.subscription_id, str) or not self.subscription_id.strip():
            raise ValueError("subscription_id must be a non-empty string.")
        object.__setattr__(self, "subscription_id", self.subscription_id.strip())

        if not isinstance(self.topic_pattern, str) or not self.topic_pattern.strip():
            raise ValueError("topic_pattern must be a non-empty string.")
        object.__setattr__(self, "topic_pattern", self.topic_pattern.strip().lower())

        if not isinstance(self.goal_id, str) or not self.goal_id.strip():
            raise ValueError("goal_id must be a non-empty string.")
        object.__setattr__(self, "goal_id", self.goal_id.strip())

        if self.trigger_id is not None:
            if not isinstance(self.trigger_id, str) or not self.trigger_id.strip():
                raise ValueError("trigger_id must be a non-empty string or None.")
            object.__setattr__(self, "trigger_id", self.trigger_id.strip())

        if not isinstance(self.cooldown_seconds, (int, float)) or self.cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be a non-negative number.")
        object.__setattr__(self, "cooldown_seconds", float(self.cooldown_seconds))

        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))

    def matches(self, event: ProactiveEvent) -> bool:
        """Check if an event matches this subscription's topic pattern."""
        if not isinstance(event, ProactiveEvent):
            return False
        pattern = self.topic_pattern
        topic = event.topic
        if pattern == "*" or pattern == topic:
            return True
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            return topic.startswith(prefix)
        return False

    def is_on_cooldown(self, current_time: float | None = None) -> bool:
        """Check if subscription is in cooldown."""
        if self.last_dispatched_at is None:
            return False
        now = current_time if current_time is not None else time.time()
        return (now - self.last_dispatched_at) < self.cooldown_seconds


@dataclass(frozen=True)
class ClarificationRequest:
    """Structured question submitted to user when goal requires interactive disambiguation."""
    clarification_id: str = field(default_factory=lambda: str(uuid4()))
    goal_id: str = ""
    task_id: str = ""
    question: str = ""
    options: tuple[str, ...] = field(default_factory=tuple)
    clarification_type: ClarificationType = ClarificationType.SINGLE_CHOICE
    status: ClarificationStatus = ClarificationStatus.PENDING
    created_at: float = field(default_factory=time.time)
    timeout_seconds: float = 600.0
    expires_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.clarification_id, str) or not self.clarification_id.strip():
            raise ValueError("clarification_id must be a non-empty string.")
        object.__setattr__(self, "clarification_id", self.clarification_id.strip())

        if not isinstance(self.goal_id, str) or not self.goal_id.strip():
            raise ValueError("goal_id must be a non-empty string.")
        object.__setattr__(self, "goal_id", self.goal_id.strip())

        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("task_id must be a non-empty string.")
        object.__setattr__(self, "task_id", self.task_id.strip())

        if not isinstance(self.question, str) or not self.question.strip():
            raise ValueError("question must be a non-empty string.")
        object.__setattr__(self, "question", self.question.strip())

        if isinstance(self.options, (list, tuple, set)):
            clean_opts = tuple(str(o).strip() for o in self.options if str(o).strip())
            object.__setattr__(self, "options", clean_opts)
        else:
            raise TypeError("options must be a sequence of strings.")

        if isinstance(self.clarification_type, str):
            object.__setattr__(self, "clarification_type", ClarificationType(self.clarification_type))
        elif not isinstance(self.clarification_type, ClarificationType):
            raise TypeError("clarification_type must be a ClarificationType instance.")

        if isinstance(self.status, str):
            object.__setattr__(self, "status", ClarificationStatus(self.status))
        elif not isinstance(self.status, ClarificationStatus):
            raise TypeError("status must be a ClarificationStatus instance.")

        if not isinstance(self.created_at, (int, float)):
            raise TypeError("created_at must be numeric.")
        object.__setattr__(self, "created_at", float(self.created_at))

        if not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive number.")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

        exp = self.created_at + self.timeout_seconds
        object.__setattr__(self, "expires_at", float(exp))
        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))

    def is_expired(self, current_time: float | None = None) -> bool:
        """Check if request has timed out."""
        now = current_time if current_time is not None else time.time()
        return now >= self.expires_at


@dataclass(frozen=True)
class ClarificationResponse:
    """User response to a pending clarification request."""
    clarification_id: str
    goal_id: str
    response_data: Any
    status: ClarificationStatus = ClarificationStatus.ANSWERED
    answered_at: float = field(default_factory=time.time)
    is_untrusted: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.clarification_id, str) or not self.clarification_id.strip():
            raise ValueError("clarification_id must be a non-empty string.")
        object.__setattr__(self, "clarification_id", self.clarification_id.strip())

        if not isinstance(self.goal_id, str) or not self.goal_id.strip():
            raise ValueError("goal_id must be a non-empty string.")
        object.__setattr__(self, "goal_id", self.goal_id.strip())

        if callable(self.response_data):
            raise ValueError("response_data cannot be callable.")

        if isinstance(self.status, str):
            object.__setattr__(self, "status", ClarificationStatus(self.status))
        elif not isinstance(self.status, ClarificationStatus):
            raise TypeError("status must be a ClarificationStatus instance.")

        if not isinstance(self.answered_at, (int, float)):
            raise TypeError("answered_at must be numeric.")
        object.__setattr__(self, "answered_at", float(self.answered_at))

        untrusted = bool(self.is_untrusted)
        if isinstance(self.response_data, TaintedValue) or is_tainted(self.response_data):
            untrusted = True
        object.__setattr__(self, "is_untrusted", untrusted)

        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))
