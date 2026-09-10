import json
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from core.history import ConversationHistory, ConversationTurn
from core.provenance import TaintedValue, wrap_tainted

logger = logging.getLogger("aura.session_types")

FORBIDDEN_METADATA_KEYS = frozenset({
    "approved",
    "is_authorized",
    "authorized",
    "auto_approve",
    "permission",
    "role_override",
    "bypass_policy",
    "skip_approval",
    "system_override",
    "is_approved",
    "approval_status",
})

SESSION_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.:]{1,128}$")


def validate_session_id(session_id: str) -> str:
    """Validate and normalize a session identifier to prevent injection or directory traversal."""
    if not isinstance(session_id, str):
        raise TypeError("session_id must be a string.")
    clean = session_id.strip()
    if not clean:
        raise ValueError("session_id cannot be empty.")
    if not SESSION_ID_REGEX.match(clean) or ".." in clean or "/" in clean or "\\" in clean:
        raise ValueError(f"Invalid session_id '{session_id}': Must match alphanumeric characters, dash, dot, colon, or underscore.")
    return clean


def sanitize_session_metadata(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Recursively sanitize metadata dictionary to ensure no callables or untrusted permission overrides."""
    if meta is None:
        return {}
    if not isinstance(meta, dict):
        raise TypeError("metadata must be a dictionary.")

    cleaned: dict[str, Any] = {}
    for k, v in meta.items():
        k_str = str(k).strip()
        if not k_str or k_str.lower() in FORBIDDEN_METADATA_KEYS:
            continue
        if callable(v):
            continue
        if isinstance(v, dict):
            cleaned[k_str] = sanitize_session_metadata(v)
        elif isinstance(v, (list, tuple)):
            cleaned[k_str] = [
                sanitize_session_metadata(item) if isinstance(item, dict) else (str(item) if not callable(item) else "")
                for item in v
            ]
        elif isinstance(v, (str, int, float, bool)) or v is None:
            cleaned[k_str] = v
        else:
            cleaned[k_str] = repr(v)
    return cleaned


def canonical_session_value(val: Any) -> Any:
    """Convert values into deterministic, JSON-serializable primitives while preserving TaintedValue envelopes."""
    if isinstance(val, TaintedValue):
        return {
            "__tainted__": True,
            "raw_value": canonical_session_value(val.raw_value),
            "is_untrusted": val.is_untrusted,
            "source_type": val.source_type,
            "originating_step_id": val.originating_step_id,
            "source_urls": sorted(val.source_urls),
            "metadata": sanitize_session_metadata(val.metadata),
        }
    elif isinstance(val, (str, int, float, bool)) or val is None:
        return val
    elif isinstance(val, (list, tuple, set, frozenset)):
        return [canonical_session_value(x) for x in val]
    elif isinstance(val, dict):
        return {str(k): canonical_session_value(v) for k, v in sorted(val.items())}
    elif isinstance(val, Enum):
        return val.value
    elif hasattr(val, "to_dict") and callable(val.to_dict):
        return val.to_dict()
    elif callable(val):
        return f"callable:{getattr(val, '__qualname__', str(val))}"
    else:
        return repr(val)


def restore_session_value(val: Any) -> Any:
    """Recursively reconstruct primitives and TaintedValue envelopes from serialized dictionaries."""
    if isinstance(val, dict):
        if val.get("__tainted__") is True:
            return wrap_tainted(
                value=restore_session_value(val.get("raw_value")),
                is_untrusted=bool(val.get("is_untrusted", True)),
                source_type=str(val.get("source_type", "external_web")),
                originating_step_id=val.get("originating_step_id"),
                source_urls=val.get("source_urls", ()),
                metadata=val.get("metadata", {}),
            )
        return {k: restore_session_value(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [restore_session_value(x) for x in val]
    return val


class SessionStatus(str, Enum):
    """Lifecycle states of an AURA multi-session context."""

    ACTIVE = "active"
    IDLE = "idle"
    PAUSED = "paused"
    COMPLETED = "completed"
    EXPIRED = "expired"
    TERMINATED = "terminated"


@dataclass
class SessionMetadata:
    """Metadata tracking identity, timestamps, TTL, and operational state of a session."""

    session_id: str
    user_id: str = "default_user"
    created_at: float = field(default_factory=time.time)
    last_accessed_at: float = field(default_factory=time.time)
    status: SessionStatus = SessionStatus.ACTIVE
    metadata: dict[str, Any] = field(default_factory=dict)
    ttl_seconds: float | None = 3600.0
    version: int = 1

    def __post_init__(self):
        self.session_id = validate_session_id(self.session_id)
        if not isinstance(self.user_id, str) or not self.user_id.strip():
            self.user_id = "default_user"
        else:
            self.user_id = self.user_id.strip()

        if isinstance(self.status, str):
            self.status = SessionStatus(self.status)
        elif not isinstance(self.status, SessionStatus):
            raise TypeError("status must be a SessionStatus enum.")

        self.created_at = float(self.created_at)
        self.last_accessed_at = float(self.last_accessed_at)
        if self.ttl_seconds is not None:
            self.ttl_seconds = float(self.ttl_seconds)
            if self.ttl_seconds < 0:
                raise ValueError("ttl_seconds cannot be negative.")

        self.metadata = sanitize_session_metadata(self.metadata)
        if not isinstance(self.version, int) or self.version < 1:
            self.version = 1

    def is_expired(self, current_time: float | None = None) -> bool:
        """Check if the session has exceeded its active TTL."""
        if self.ttl_seconds is None or self.ttl_seconds <= 0:
            return False
        now = current_time if current_time is not None else time.time()
        return (now - self.last_accessed_at) > self.ttl_seconds

    def touch(self, current_time: float | None = None) -> None:
        """Update the last accessed timestamp and increment version."""
        now = current_time if current_time is not None else time.time()
        self.last_accessed_at = now
        self.version += 1

    def to_dict(self) -> dict[str, Any]:
        """Convert metadata to a serializable dictionary."""
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "created_at": self.created_at,
            "last_accessed_at": self.last_accessed_at,
            "status": self.status.value,
            "metadata": canonical_session_value(self.metadata),
            "ttl_seconds": self.ttl_seconds,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionMetadata":
        """Reconstruct metadata from a dictionary."""
        return cls(
            session_id=str(data["session_id"]),
            user_id=str(data.get("user_id", "default_user")),
            created_at=float(data.get("created_at", time.time())),
            last_accessed_at=float(data.get("last_accessed_at", time.time())),
            status=SessionStatus(data.get("status", SessionStatus.ACTIVE.value)),
            metadata=restore_session_value(data.get("metadata", {})),
            ttl_seconds=float(data["ttl_seconds"]) if data.get("ttl_seconds") is not None else None,
            version=int(data.get("version", 1)),
        )


@dataclass
class SessionContext:
    """Isolated execution and conversational context bound to a single session."""

    metadata: SessionMetadata
    history: ConversationHistory = field(default_factory=ConversationHistory)
    active_goal_ids: list[str] = field(default_factory=list)
    active_task_ids: list[str] = field(default_factory=list)
    resource_limits: dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.metadata, SessionMetadata):
            raise TypeError("metadata must be an instance of SessionMetadata.")
        if not isinstance(self.history, ConversationHistory):
            if isinstance(self.history, list):
                turns = [ConversationTurn(**t) if isinstance(t, dict) else t for t in self.history]
                self.history = ConversationHistory(turns=turns)
            elif isinstance(self.history, dict) and "turns" in self.history:
                self.history = ConversationHistory(**self.history)
            else:
                raise TypeError("history must be an instance of ConversationHistory.")

        self.active_goal_ids = [str(g).strip() for g in self.active_goal_ids if str(g).strip()]
        self.active_task_ids = [str(t).strip() for t in self.active_task_ids if str(t).strip()]
        self.resource_limits = {str(k): float(v) for k, v in self.resource_limits.items()}

    @property
    def session_id(self) -> str:
        return self.metadata.session_id

    @property
    def status(self) -> SessionStatus:
        return self.metadata.status

    def is_expired(self, current_time: float | None = None) -> bool:
        return self.metadata.is_expired(current_time)

    def touch(self, current_time: float | None = None) -> None:
        self.metadata.touch(current_time)

    def to_dict(self) -> dict[str, Any]:
        """Serialize full session context to a deterministic dictionary."""
        return {
            "metadata": self.metadata.to_dict(),
            "history": {
                "turns": [
                    {
                        "user_input": canonical_session_value(t.user_input),
                        "assistant_output": canonical_session_value(t.assistant_output),
                        "tool_name": t.tool_name,
                        "tool_result": canonical_session_value(t.tool_result),
                    }
                    for t in self.history.turns
                ]
            },
            "active_goal_ids": list(self.active_goal_ids),
            "active_task_ids": list(self.active_task_ids),
            "resource_limits": dict(self.resource_limits),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionContext":
        """Reconstruct full session context from a dictionary."""
        meta = SessionMetadata.from_dict(data["metadata"])
        history_raw = data.get("history", {}).get("turns", [])
        turns: list[ConversationTurn] = []
        for t in history_raw:
            u_in = restore_session_value(t.get("user_input", ""))
            a_out = restore_session_value(t.get("assistant_output", ""))
            t_res = restore_session_value(t.get("tool_result")) if t.get("tool_result") is not None else None
            turns.append(
                ConversationTurn(
                    user_input=str(u_in) if not isinstance(u_in, TaintedValue) else u_in,
                    assistant_output=str(a_out) if not isinstance(a_out, TaintedValue) else a_out,
                    tool_name=t.get("tool_name"),
                    tool_result=str(t_res) if (t_res is not None and not isinstance(t_res, TaintedValue)) else t_res,
                )
            )
        return cls(
            metadata=meta,
            history=ConversationHistory(turns=turns),
            active_goal_ids=list(data.get("active_goal_ids", [])),
            active_task_ids=list(data.get("active_task_ids", [])),
            resource_limits=dict(data.get("resource_limits", {})),
        )


class StreamEventType(str, Enum):
    """Event taxonomy for real-time Pub/Sub streaming."""

    TOKEN_CHUNK = "token_chunk"
    STEP_STARTED = "step_started"
    STEP_PROGRESS = "step_progress"
    STEP_COMPLETED = "step_completed"
    TOOL_CALLED = "tool_called"
    TOOL_RESULT = "tool_result"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_RESOLVED = "approval_resolved"
    CLARIFICATION_REQUIRED = "clarification_required"
    CLARIFICATION_RESOLVED = "clarification_resolved"
    GOAL_UPDATED = "goal_updated"
    SUPERVISOR_HEARTBEAT = "supervisor_heartbeat"
    ERROR = "error"
    SESSION_CLOSED = "session_closed"


@dataclass
class StreamEvent:
    """An individual event emitted over the real-time streaming gateway."""

    session_id: str
    event_type: StreamEventType
    data: dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: float = field(default_factory=time.time)
    step_id: str | None = None
    goal_id: str | None = None
    task_id: str | None = None

    def __post_init__(self):
        self.session_id = validate_session_id(self.session_id)
        if isinstance(self.event_type, str):
            self.event_type = StreamEventType(self.event_type)
        elif not isinstance(self.event_type, StreamEventType):
            raise TypeError("event_type must be a StreamEventType enum.")

        if not isinstance(self.event_id, str) or not self.event_id.strip():
            self.event_id = str(uuid4())
        else:
            self.event_id = self.event_id.strip()

        self.timestamp = float(self.timestamp)
        self.data = sanitize_session_metadata(self.data)

        if self.step_id is not None:
            self.step_id = str(self.step_id).strip() or None
        if self.goal_id is not None:
            self.goal_id = str(self.goal_id).strip() or None
        if self.task_id is not None:
            self.task_id = str(self.task_id).strip() or None

    def to_dict(self) -> dict[str, Any]:
        """Convert StreamEvent to a dictionary."""
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "data": canonical_session_value(self.data),
            "step_id": self.step_id,
            "goal_id": self.goal_id,
            "task_id": self.task_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StreamEvent":
        """Reconstruct StreamEvent from a dictionary."""
        return cls(
            event_id=str(data.get("event_id", uuid4())),
            session_id=str(data["session_id"]),
            event_type=StreamEventType(data["event_type"]),
            timestamp=float(data.get("timestamp", time.time())),
            data=restore_session_value(data.get("data", {})),
            step_id=data.get("step_id"),
            goal_id=data.get("goal_id"),
            task_id=data.get("task_id"),
        )

    def to_sse(self) -> str:
        """Format event as Server-Sent Events (SSE) compliant text chunk."""
        payload = json.dumps(self.to_dict(), sort_keys=True)
        return f"id: {self.event_id}\nevent: {self.event_type.value}\ndata: {payload}\n\n"

    def to_ws_message(self) -> str:
        """Format event as a WebSocket-compliant JSON message payload."""
        return json.dumps(self.to_dict(), sort_keys=True)


class OperatorActionType(str, Enum):
    """Allowed actions an interactive human operator can perform."""

    APPROVE = "approve"
    REJECT = "reject"
    CLARIFY = "clarify"
    ABORT = "abort"
    PAUSE = "pause"
    RESUME = "resume"


@dataclass
class OperatorAction:
    """Represents an action submitted by a human operator through the Operator Bridge."""

    session_id: str
    request_id: str
    action_type: OperatorActionType
    operator_id: str = "operator"
    action_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: float = field(default_factory=time.time)
    decision_rationale: str | None = None
    clarification_payload: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.session_id = validate_session_id(self.session_id)
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError("request_id must be a non-empty string.")
        self.request_id = self.request_id.strip()

        if isinstance(self.action_type, str):
            self.action_type = OperatorActionType(self.action_type)
        elif not isinstance(self.action_type, OperatorActionType):
            raise TypeError("action_type must be an OperatorActionType enum.")

        if not isinstance(self.operator_id, str) or not self.operator_id.strip():
            self.operator_id = "operator"
        else:
            self.operator_id = self.operator_id.strip()

        if not isinstance(self.action_id, str) or not self.action_id.strip():
            self.action_id = str(uuid4())
        else:
            self.action_id = self.action_id.strip()

        self.timestamp = float(self.timestamp)
        if self.decision_rationale is not None:
            self.decision_rationale = str(self.decision_rationale).strip() or None
        self.clarification_payload = sanitize_session_metadata(self.clarification_payload)
        self.metadata = sanitize_session_metadata(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "action_type": self.action_type.value,
            "operator_id": self.operator_id,
            "timestamp": self.timestamp,
            "decision_rationale": self.decision_rationale,
            "clarification_payload": canonical_session_value(self.clarification_payload),
            "metadata": canonical_session_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OperatorAction":
        return cls(
            action_id=str(data.get("action_id", uuid4())),
            session_id=str(data["session_id"]),
            request_id=str(data["request_id"]),
            action_type=OperatorActionType(data["action_type"]),
            operator_id=str(data.get("operator_id", "operator")),
            timestamp=float(data.get("timestamp", time.time())),
            decision_rationale=data.get("decision_rationale"),
            clarification_payload=restore_session_value(data.get("clarification_payload")),
            metadata=restore_session_value(data.get("metadata", {})),
        )


@dataclass
class OperatorResolution:
    """Outcome report for an operator action handled by the Operator Bridge."""

    action_id: str
    request_id: str
    success: bool
    status: str
    message: str
    timestamp: float = field(default_factory=time.time)
    resolution_id: str = field(default_factory=lambda: str(uuid4()))
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.action_id = str(self.action_id).strip()
        self.request_id = str(self.request_id).strip()
        self.success = bool(self.success)
        self.status = str(self.status).strip()
        self.message = str(self.message).strip()
        self.timestamp = float(self.timestamp)
        self.resolution_id = str(self.resolution_id).strip()
        self.details = sanitize_session_metadata(self.details)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolution_id": self.resolution_id,
            "action_id": self.action_id,
            "request_id": self.request_id,
            "success": self.success,
            "status": self.status,
            "message": self.message,
            "timestamp": self.timestamp,
            "details": canonical_session_value(self.details),
        }
