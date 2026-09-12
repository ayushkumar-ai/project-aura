"""M35 — Proactive Assistance Engine Types.

Defines schemas for trigger definitions, proactive action types, proposals,
cooldown controls, and audit trails.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class TriggerType(str, Enum):
    INTERVAL = "interval"
    SYSTEM_STATE = "system_state"
    EVENT_DRIVEN = "event_driven"
    CONDITION_PREDICATE = "condition_predicate"


class ProactiveActionType(str, Enum):
    NOTIFICATION = "notification"
    SUGGESTION = "suggestion"
    MAINTENANCE = "maintenance"
    BACKGROUND_TASK = "background_task"
    APPROVAL_REQUEST = "approval_request"


class ProposalStatus(str, Enum):
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    EXPIRED = "expired"


@dataclass
class TriggerDefinition:
    trigger_id: str
    name: str
    description: str
    trigger_type: TriggerType
    action_type: ProactiveActionType
    condition_predicate: str = ""  # e.g. "stale_goals > 0"
    interval_seconds: float = 300.0
    cooldown_seconds: float = 60.0
    requires_user_approval: bool = True
    max_firings_per_hour: int = 10
    enabled: bool = True
    proposed_payload: dict[str, Any] = field(default_factory=dict)
    last_fired_at: float = 0.0
    fire_count_recent: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["trigger_type"] = self.trigger_type.value
        d["action_type"] = self.action_type.value
        return d


@dataclass
class ProactiveProposal:
    proposal_id: str
    trigger_id: str
    action_type: ProactiveActionType
    title: str
    description: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    status: ProposalStatus = ProposalStatus.PENDING_APPROVAL
    user_decision: str = ""
    decision_reason: str = ""
    execution_result: Any = None
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["action_type"] = self.action_type.value
        d["status"] = self.status.value
        return d


@dataclass
class ProactiveAuditEntry:
    timestamp: float
    trigger_id: str
    proposal_id: str
    action_type: str
    status: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
