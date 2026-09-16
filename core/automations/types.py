"""M53 — Core Domain Types and Exceptions for Proactive Automations."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class TriggerType(str, enum.Enum):
    """Supported trigger types for automations."""
    ONE_TIME = "one_time"
    RECURRING = "recurring"
    TIME_WINDOW = "time_window"
    CONDITION = "condition"
    EVENT = "event"


class AutomationStatus(str, enum.Enum):
    """Lifecycle status of an automation."""
    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"
    COMPLETED = "completed"
    EXPIRED = "expired"
    FAILED = "failed"


class RunStatus(str, enum.Enum):
    """Execution status of an individual automation run."""
    CLAIMED = "claimed"
    EVALUATING = "evaluating"
    CONDITION_FAILED = "condition_failed"
    POLICY_DENIED = "policy_denied"
    SKIPPED = "skipped"
    ENQUEUED = "enqueued"
    COMPLETED = "completed"
    FAILED = "failed"


class CatchUpPolicy(str, enum.Enum):
    """Catch-up behavior when scheduler encounters missed historical slots."""
    RUN_LATEST = "run_latest"
    RUN_ALL = "run_all"
    SKIP = "skip"


# --- Exceptions ---

class AutomationError(Exception):
    """Base exception for all automation errors."""
    pass


class AutomationNotFoundError(AutomationError):
    """Raised when an automation is not found or inaccessible."""
    pass


class AutomationValidationError(AutomationError):
    """Raised when automation configuration fails validation."""
    pass


class AutomationQuotaExceededError(AutomationError):
    """Raised when tenant hourly automation quota is exhausted."""
    pass


class AutomationCycleDetectedError(AutomationError):
    """Raised when recursive cycle is detected in automation lineage."""
    pass


class AutomationRecursionLimitExceededError(AutomationError):
    """Raised when nested automation depth exceeds max bound (3)."""
    pass


class LeaseFencingError(AutomationError):
    """Raised when a worker attempts an operation with a stale or expired lease token."""
    pass


class TransactionDeadlineExceededError(AutomationError):
    """Raised when aggregate transaction duration exceeds the hard deadline (5.0s)."""
    pass


class LockTimeoutError(AutomationError):
    """Raised when PostgreSQL lock acquisition exceeds local lock_timeout."""
    pass


class StatementTimeoutError(AutomationError):
    """Raised when a single SQL statement exceeds statement_timeout."""
    pass


class ConditionEvaluationError(AutomationError):
    """Raised when evaluation of an automation condition encounters a fatal error."""
    pass


# --- Data Models ---

@dataclass
class TriggerConfig:
    cron: str | None = None
    run_at: float | None = None
    timezone: str = "UTC"
    catchup_policy: CatchUpPolicy = CatchUpPolicy.RUN_LATEST
    window_start: float | None = None
    window_end: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cron": self.cron,
            "run_at": self.run_at,
            "timezone": self.timezone,
            "catchup_policy": self.catchup_policy.value if isinstance(self.catchup_policy, CatchUpPolicy) else str(self.catchup_policy),
            "window_start": self.window_start,
            "window_end": self.window_end,
        }


@dataclass
class ConditionConfig:
    tier: int = 1
    predicate: str | None = None
    context_keys: list[str] = field(default_factory=list)
    llm_prompt: str | None = None
    llm_model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "predicate": self.predicate,
            "context_keys": self.context_keys,
            "llm_prompt": self.llm_prompt,
            "llm_model": self.llm_model,
        }


@dataclass
class ActionTemplate:
    title: str
    goal: str
    context: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int = 600
    idempotency_prefix: str | None = None
    parent_automation_id: str | None = None
    recursion_depth: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "goal": self.goal,
            "context": self.context,
            "timeout_seconds": self.timeout_seconds,
            "idempotency_prefix": self.idempotency_prefix,
            "parent_automation_id": self.parent_automation_id,
            "recursion_depth": self.recursion_depth,
        }


@dataclass
class Automation:
    id: str
    user_id: str
    name: str
    description: str = ""
    status: AutomationStatus = AutomationStatus.ACTIVE
    trigger_type: TriggerType = TriggerType.RECURRING
    trigger_config: dict[str, Any] = field(default_factory=dict)
    condition_config: dict[str, Any] = field(default_factory=dict)
    action_template: dict[str, Any] = field(default_factory=dict)
    next_fire_at: float | None = None
    last_fired_at: float | None = None
    fire_count: int = 0
    max_runs: int | None = None
    cooldown_seconds: int = 60
    lease_owner: str | None = None
    lease_token: str | None = None
    claimed_at: float | None = None
    lease_expires_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value if isinstance(self.status, AutomationStatus) else str(self.status),
            "trigger_type": self.trigger_type.value if isinstance(self.trigger_type, TriggerType) else str(self.trigger_type),
            "trigger_config": self.trigger_config,
            "condition_config": self.condition_config,
            "action_template": self.action_template,
            "next_fire_at": self.next_fire_at,
            "last_fired_at": self.last_fired_at,
            "fire_count": self.fire_count,
            "max_runs": self.max_runs,
            "cooldown_seconds": self.cooldown_seconds,
            "lease_owner": self.lease_owner,
            "lease_token": self.lease_token,
            "claimed_at": self.claimed_at,
            "lease_expires_at": self.lease_expires_at,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class AutomationRun:
    id: str
    automation_id: str
    user_id: str
    task_id: str | None
    status: RunStatus
    trigger_timestamp: float
    slot_timestamp: float
    lease_token: str | None = None
    condition_evaluation: dict[str, Any] | None = None
    error_message: str | None = None
    reconciled_at: float | None = None
    created_at: float = 0.0
    completed_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "automation_id": self.automation_id,
            "user_id": self.user_id,
            "task_id": self.task_id,
            "status": self.status.value if isinstance(self.status, RunStatus) else str(self.status),
            "trigger_timestamp": self.trigger_timestamp,
            "slot_timestamp": self.slot_timestamp,
            "lease_token": self.lease_token,
            "condition_evaluation": self.condition_evaluation,
            "error_message": self.error_message,
            "reconciled_at": self.reconciled_at,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }
