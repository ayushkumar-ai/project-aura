"""Distributed Causal Tracing & Context Data Contracts (M24).

Defines immutable, bounded data contracts and schemas for OpenTelemetry-compatible
distributed causal tracing, span lifecycle records, and W3C context propagation.
"""

from __future__ import annotations

import copy
import json
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence
from uuid import uuid4

from core.provenance import TaintedValue, is_tainted, unwrap_tainted, wrap_tainted

# Maximum bounds to prevent memory leaks or unbounded growth
MAX_SPANS_PER_TRACE = 5000
MAX_ATTRIBUTES_PER_SPAN = 64
MAX_EVENTS_PER_SPAN = 128
MAX_LINKS_PER_SPAN = 32
MAX_BAGGAGE_ITEMS = 32
MAX_STRING_LENGTH = 4096

FORBIDDEN_TRACE_METADATA_KEYS = frozenset({
    "approved",
    "approval_status",
    "is_approved",
    "auto_approve",
    "permission",
    "authorized",
    "is_admin",
    "is_authorized",
    "bypass_policy",
    "sudo",
})


def _sanitize_trace_value(val: Any) -> Any:
    """Sanitize attribute values for safe tracing serialization."""
    if isinstance(val, TaintedValue):
        return {
            "__tainted__": True,
            "raw_value": _sanitize_trace_value(val.raw_value),
            "is_untrusted": val.is_untrusted,
            "source_type": val.source_type,
            "originating_step_id": val.originating_step_id,
        }
    elif isinstance(val, (str, int, float, bool)) or val is None:
        if isinstance(val, str) and len(val) > MAX_STRING_LENGTH:
            return val[:MAX_STRING_LENGTH] + "...[truncated]"
        return val
    elif isinstance(val, (list, tuple, set, frozenset)):
        return [_sanitize_trace_value(x) for x in list(val)[:50]]
    elif isinstance(val, dict):
        cleaned: dict[str, Any] = {}
        for k, v in list(val.items())[:MAX_ATTRIBUTES_PER_SPAN]:
            k_str = str(k).strip()
            if k_str.lower() in FORBIDDEN_TRACE_METADATA_KEYS or callable(v):
                continue
            cleaned[k_str] = _sanitize_trace_value(v)
        return cleaned
    elif callable(val):
        return "<callable>"
    else:
        return repr(val)[:MAX_STRING_LENGTH]


def _sanitize_attributes(attrs: dict[str, Any]) -> dict[str, Any]:
    """Sanitize and bound span attributes."""
    if not isinstance(attrs, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for k, v in list(attrs.items())[:MAX_ATTRIBUTES_PER_SPAN]:
        k_str = str(k).strip()
        if not k_str or k_str.lower() in FORBIDDEN_TRACE_METADATA_KEYS or callable(v):
            continue
        cleaned[k_str] = _sanitize_trace_value(v)
    return cleaned


class SpanKind(str, Enum):
    """Classification of span execution boundaries."""

    INTERNAL = "internal"
    SERVER = "server"
    CLIENT = "client"
    PRODUCER = "producer"
    CONSUMER = "consumer"
    AGENT_STEP = "agent_step"
    TOOL_CALL = "tool_call"
    MODEL_INFERENCE = "model_inference"
    TEAM_COORDINATION = "team_coordination"
    DELEGATION = "delegation"
    CONSENSUS_VOTE = "consensus_vote"
    EVALUATION_AUDIT = "evaluation_audit"
    ARTIFACT_OP = "artifact_op"
    MEMORY_OP = "memory_op"


class SpanStatus(str, Enum):
    """Execution outcome status of a completed span."""

    UNSET = "unset"
    OK = "ok"
    ERROR = "error"


@dataclass(frozen=True)
class TraceContext:
    """Immutable W3C/OpenTelemetry-compatible distributed trace propagation envelope."""

    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    baggage: dict[str, str] = field(default_factory=dict)
    is_sampled: bool = True
    trace_state: str = ""

    def __post_init__(self):
        tid = str(self.trace_id).strip()
        if not tid:
            raise ValueError("trace_id must be a non-empty string.")
        object.__setattr__(self, "trace_id", tid)

        sid = str(self.span_id).strip()
        if not sid:
            raise ValueError("span_id must be a non-empty string.")
        object.__setattr__(self, "span_id", sid)

        if self.parent_span_id is not None:
            psid = str(self.parent_span_id).strip()
            object.__setattr__(self, "parent_span_id", psid if psid else None)

        # Sanitize baggage: only non-empty strings, max items
        clean_baggage: dict[str, str] = {}
        if isinstance(self.baggage, dict):
            for k, v in list(self.baggage.items())[:MAX_BAGGAGE_ITEMS]:
                k_str = str(k).strip()
                if k_str and k_str.lower() not in FORBIDDEN_TRACE_METADATA_KEYS:
                    clean_baggage[k_str] = str(v)[:MAX_STRING_LENGTH]
        object.__setattr__(self, "baggage", clean_baggage)
        object.__setattr__(self, "is_sampled", bool(self.is_sampled))
        object.__setattr__(self, "trace_state", str(self.trace_state or "").strip())

    @classmethod
    def new_root(cls, trace_id: str | None = None, baggage: dict[str, str] | None = None) -> TraceContext:
        """Create a new root trace context with fresh IDs."""
        tid = trace_id.strip() if trace_id and str(trace_id).strip() else uuid4().hex
        sid = uuid4().hex[:16]
        return cls(trace_id=tid, span_id=sid, parent_span_id=None, baggage=baggage or {})

    def child_context(self, span_id: str | None = None, extra_baggage: dict[str, str] | None = None) -> TraceContext:
        """Derive a child trace context with this span as the parent."""
        sid = span_id.strip() if span_id and str(span_id).strip() else uuid4().hex[:16]
        merged_baggage = dict(self.baggage)
        if extra_baggage:
            merged_baggage.update(extra_baggage)
        return TraceContext(
            trace_id=self.trace_id,
            span_id=sid,
            parent_span_id=self.span_id,
            baggage=merged_baggage,
            is_sampled=self.is_sampled,
            trace_state=self.trace_state,
        )

    def to_traceparent(self) -> str:
        """Format as W3C traceparent header: 00-{trace_id}-{span_id}-{flags}."""
        # Ensure 32-hex trace_id and 16-hex span_id formatting
        tid = self.trace_id.replace("-", "").lower()
        if len(tid) < 32:
            tid = tid.zfill(32)
        elif len(tid) > 32:
            tid = tid[:32]

        sid = self.span_id.replace("-", "").lower()
        if len(sid) < 16:
            sid = sid.zfill(16)
        elif len(sid) > 16:
            sid = sid[:16]

        flags = "01" if self.is_sampled else "00"
        return f"00-{tid}-{sid}-{flags}"

    @classmethod
    def from_traceparent(cls, header: str, baggage: dict[str, str] | None = None) -> TraceContext | None:
        """Parse W3C traceparent header string."""
        if not isinstance(header, str):
            return None
        parts = header.strip().split("-")
        if len(parts) != 4 or parts[0] != "00":
            return None
        tid, sid, flags = parts[1], parts[2], parts[3]
        if len(tid) != 32 or len(sid) != 16:
            return None
        is_sampled = (flags == "01")
        return cls(
            trace_id=tid,
            span_id=sid,
            parent_span_id=None,
            baggage=baggage or {},
            is_sampled=is_sampled,
        )

    # Class method alias for W3C compatibility
    from_w3c_traceparent = from_traceparent


    def to_dict(self) -> dict[str, Any]:
        """Serialize trace context to primitive dictionary."""
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "baggage": dict(self.baggage),
            "is_sampled": self.is_sampled,
            "trace_state": self.trace_state,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TraceContext:
        """Restore trace context from dictionary."""
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        return cls(
            trace_id=str(data.get("trace_id", uuid4().hex)),
            span_id=str(data.get("span_id", uuid4().hex[:16])),
            parent_span_id=data.get("parent_span_id"),
            baggage=dict(data.get("baggage", {})),
            is_sampled=bool(data.get("is_sampled", True)),
            trace_state=str(data.get("trace_state", "")),
        )


@dataclass(frozen=True)
class SpanEvent:
    """Timestamped diagnostic or milestone event occurring within a span."""

    name: str
    timestamp: float = field(default_factory=time.time)
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        nm = str(self.name).strip()
        if not nm:
            raise ValueError("SpanEvent name must be non-empty.")
        object.__setattr__(self, "name", nm)
        object.__setattr__(self, "timestamp", float(self.timestamp))
        object.__setattr__(self, "attributes", _sanitize_attributes(self.attributes))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "timestamp": self.timestamp,
            "attributes": dict(self.attributes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpanEvent:
        return cls(
            name=str(data.get("name", "")),
            timestamp=float(data.get("timestamp", time.time())),
            attributes=dict(data.get("attributes", {})),
        )


@dataclass(frozen=True)
class SpanLink:
    """Causal cross-trace or cross-span link reference."""

    trace_id: str
    span_id: str
    attributes: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "trace_id", str(self.trace_id).strip())
        object.__setattr__(self, "span_id", str(self.span_id).strip())
        object.__setattr__(self, "attributes", _sanitize_attributes(self.attributes))

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "attributes": dict(self.attributes),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpanLink:
        return cls(
            trace_id=str(data.get("trace_id", "")),
            span_id=str(data.get("span_id", "")),
            attributes=dict(data.get("attributes", {})),
        )


@dataclass(frozen=True)
class SpanRecord:
    """Immutable audit record of a completed, bounded span of execution."""

    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    kind: SpanKind
    status: SpanStatus
    status_message: str
    start_time: float
    end_time: float
    duration_ms: float
    attributes: dict[str, Any] = field(default_factory=dict)
    events: tuple[SpanEvent, ...] = field(default_factory=tuple)
    links: tuple[SpanLink, ...] = field(default_factory=tuple)
    resource_usage: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "trace_id", str(self.trace_id).strip())
        object.__setattr__(self, "span_id", str(self.span_id).strip())
        if self.parent_span_id is not None:
            object.__setattr__(self, "parent_span_id", str(self.parent_span_id).strip() or None)

        nm = str(self.name).strip()
        object.__setattr__(self, "name", nm if nm else "unnamed_span")

        if isinstance(self.kind, str):
            object.__setattr__(self, "kind", SpanKind(self.kind))
        elif not isinstance(self.kind, SpanKind):
            raise TypeError("kind must be a SpanKind instance.")

        if isinstance(self.status, str):
            object.__setattr__(self, "status", SpanStatus(self.status))
        elif not isinstance(self.status, SpanStatus):
            raise TypeError("status must be a SpanStatus instance.")

        object.__setattr__(self, "status_message", str(self.status_message or "").strip())
        object.__setattr__(self, "start_time", float(self.start_time))
        object.__setattr__(self, "end_time", float(self.end_time))
        dur = max(0.0, float(self.duration_ms))
        object.__setattr__(self, "duration_ms", round(dur, 3))

        object.__setattr__(self, "attributes", _sanitize_attributes(self.attributes))

        evs = []
        for e in self.events[:MAX_EVENTS_PER_SPAN]:
            evs.append(e if isinstance(e, SpanEvent) else SpanEvent.from_dict(e))
        object.__setattr__(self, "events", tuple(evs))

        lnks = []
        for l in self.links[:MAX_LINKS_PER_SPAN]:
            lnks.append(l if isinstance(l, SpanLink) else SpanLink.from_dict(l))
        object.__setattr__(self, "links", tuple(lnks))

        # Sanitize resource usage metrics
        res: dict[str, Any] = {}
        if isinstance(self.resource_usage, dict):
            for k in ("tokens", "prompt_tokens", "completion_tokens", "tool_calls", "cpu_ms", "cost_estimate_usd"):
                if k in self.resource_usage:
                    val = self.resource_usage[k]
                    if isinstance(val, (int, float)):
                        res[k] = val
        object.__setattr__(self, "resource_usage", res)

    def to_dict(self) -> dict[str, Any]:
        """Serialize span record to JSON-serializable dictionary."""
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "kind": self.kind.value,
            "status": self.status.value,
            "status_message": self.status_message,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "attributes": dict(self.attributes),
            "events": [e.to_dict() for e in self.events],
            "links": [l.to_dict() for l in self.links],
            "resource_usage": dict(self.resource_usage),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SpanRecord:
        """Restore span record from dictionary."""
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        return cls(
            trace_id=str(data.get("trace_id", "")),
            span_id=str(data.get("span_id", "")),
            parent_span_id=data.get("parent_span_id"),
            name=str(data.get("name", "unnamed_span")),
            kind=SpanKind(data.get("kind", SpanKind.INTERNAL.value)),
            status=SpanStatus(data.get("status", SpanStatus.UNSET.value)),
            status_message=str(data.get("status_message", "")),
            start_time=float(data.get("start_time", 0.0)),
            end_time=float(data.get("end_time", 0.0)),
            duration_ms=float(data.get("duration_ms", 0.0)),
            attributes=dict(data.get("attributes", {})),
            events=tuple(SpanEvent.from_dict(e) for e in data.get("events", [])),
            links=tuple(SpanLink.from_dict(l) for l in data.get("links", [])),
            resource_usage=dict(data.get("resource_usage", {})),
        )
