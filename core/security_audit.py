"""Structured Security Audit Stream (M44).

Provides a low-overhead, structured security audit event stream for tracking
authentication attempts, authorization decisions, tool policy enforcement,
prompt-injection containment, and credential scrubbing.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from core.metrics import get_metrics_registry
from core.security_scrubber import scrub_dict
from core.telemetry_context import (
    get_current_request_id,
    get_current_trace_id,
    get_current_user_id,
)

logger = logging.getLogger("aura.security.audit")


class SecurityEventType(str, Enum):
    """Canonical security event types."""

    AUTH_SUCCESS = "AUTH_SUCCESS"
    AUTH_FAILURE = "AUTH_FAILURE"
    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"
    TOOL_POLICY_VIOLATION = "TOOL_POLICY_VIOLATION"
    PROMPT_INJECTION_FLAGGED = "PROMPT_INJECTION_FLAGGED"
    SECRET_REDACTED = "SECRET_REDACTED"
    AUTOMATION_POLICY_DENIED = "AUTOMATION_POLICY_DENIED"
    AUTOMATION_CYCLE_DETECTED = "AUTOMATION_CYCLE_DETECTED"
    AUTOMATION_RECURSION_LIMIT_EXCEEDED = "AUTOMATION_RECURSION_LIMIT_EXCEEDED"
    AUTOMATION_LEASE_FENCING_REJECTED = "AUTOMATION_LEASE_FENCING_REJECTED"
    AUTOMATION_DISPATCH_TIMEOUT = "AUTOMATION_DISPATCH_TIMEOUT"


@dataclass(frozen=True)
class SecurityAuditEvent:
    """Immutable structured security audit record."""

    event_type: SecurityEventType
    outcome: str  # "allow", "deny", "flagged", "redacted", "success", "failure"
    timestamp: float = field(default_factory=time.time)
    request_id: str | None = None
    trace_id: str | None = None
    user_id: str | None = None
    client_ip: str | None = None
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert event to sanitized dictionary."""
        return {
            "event_type": self.event_type.value,
            "outcome": self.outcome,
            "timestamp": self.timestamp,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "user_id": self.user_id,
            "client_ip": self.client_ip,
            "reason": self.reason,
            "metadata": scrub_dict(self.metadata),
        }


class SecurityAuditLogger:
    """Thread-safe structured security audit stream with bounded in-memory buffer."""

    def __init__(self, max_buffer_size: int = 1000) -> None:
        self.max_buffer_size = max_buffer_size
        self._events: list[SecurityAuditEvent] = []
        self._lock = threading.RLock()
        self._listeners: list[Callable[[SecurityAuditEvent], None]] = []

    def add_listener(self, listener: Callable[[SecurityAuditEvent], None]) -> None:
        """Register sink or listener callback for security audit events."""
        with self._lock:
            self._listeners.append(listener)

    def record_event(
        self,
        event_type: SecurityEventType | str,
        outcome: str,
        reason: str = "",
        user_id: str | None = None,
        request_id: str | None = None,
        trace_id: str | None = None,
        client_ip: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SecurityAuditEvent:
        """Record and dispatch a structured security audit event."""
        ev_type = (
            event_type
            if isinstance(event_type, SecurityEventType)
            else SecurityEventType(str(event_type).upper())
        )

        # Resolve IDs from context if omitted
        eff_req_id = request_id or get_current_request_id()
        eff_tr_id = trace_id or get_current_trace_id()
        eff_user_id = user_id or get_current_user_id()

        event = SecurityAuditEvent(
            event_type=ev_type,
            outcome=str(outcome).lower(),
            timestamp=time.time(),
            request_id=eff_req_id,
            trace_id=eff_tr_id,
            user_id=eff_user_id,
            client_ip=client_ip,
            reason=str(reason),
            metadata=dict(metadata or {}),
        )

        # 1. Store in bounded ring buffer
        with self._lock:
            if len(self._events) >= self.max_buffer_size:
                self._events.pop(0)
            self._events.append(event)
            listeners = list(self._listeners)

        # 2. Increment security metrics counter
        try:
            metrics = get_metrics_registry()
            sec_counter = metrics.get_counter("aura_security_events_total")
            sec_counter.inc(labels={"event_type": ev_type.value.lower(), "outcome": event.outcome})
        except Exception:
            pass

        # 3. Log event
        log_level = logging.WARNING if event.outcome in ("deny", "failure", "flagged") else logging.INFO
        logger.log(
            log_level,
            f"Security event: {ev_type.value} outcome={event.outcome} reason={event.reason}",
            extra={
                "event": f"security_{ev_type.value.lower()}",
                "event_type": ev_type.value,
                "outcome": event.outcome,
                "reason": event.reason,
                "request_id": eff_req_id,
                "trace_id": eff_tr_id,
                "user_id": eff_user_id,
                "client_ip": client_ip,
            },
        )

        # 4. Notify listeners
        for l in listeners:
            try:
                l(event)
            except Exception as e:
                logger.debug(f"Error invoking security audit listener: {e}")

        return event

    def get_events(
        self,
        event_type: SecurityEventType | None = None,
        limit: int = 100,
    ) -> list[SecurityAuditEvent]:
        """Retrieve recent security audit events."""
        with self._lock:
            if event_type is None:
                return list(self._events[-limit:])
            return [e for e in self._events if e.event_type == event_type][-limit:]

    def clear(self) -> None:
        """Clear the in-memory buffer (for testing)."""
        with self._lock:
            self._events.clear()


# Global default security audit logger
_global_security_audit_logger = SecurityAuditLogger()


def get_security_audit_logger() -> SecurityAuditLogger:
    """Return central singleton security audit logger."""
    return _global_security_audit_logger
