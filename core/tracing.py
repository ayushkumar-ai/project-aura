"""Distributed Tracer & Context Management Engine (M24).

Provides thread-safe and async-safe distributed tracing, span lifecycle management,
contextvars-based context propagation, and exporter dispatch.
"""

from __future__ import annotations

import contextvars
import logging
import threading
import time
from types import TracebackType
from typing import Any, Callable, Sequence
from uuid import uuid4

from core.trace_types import (
    MAX_ATTRIBUTES_PER_SPAN,
    MAX_EVENTS_PER_SPAN,
    MAX_LINKS_PER_SPAN,
    SpanEvent,
    SpanKind,
    SpanLink,
    SpanRecord,
    SpanStatus,
    TraceContext,
    _sanitize_attributes,
    _sanitize_trace_value,
)

logger = logging.getLogger("aura.tracing")

# ContextVar for async-safe and thread-safe active trace context
_active_trace_context: contextvars.ContextVar[TraceContext | None] = contextvars.ContextVar(
    "active_trace_context", default=None
)


class Span:
    """Active, mutable span representing an ongoing bounded execution block."""

    def __init__(
        self,
        tracer: "Tracer",
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        parent_context: TraceContext | None = None,
        attributes: dict[str, Any] | None = None,
        links: Sequence[SpanLink] | None = None,
        start_time: float | None = None,
    ):
        self.tracer = tracer
        self.name = str(name).strip() or "unnamed_span"
        self.kind = kind if isinstance(kind, SpanKind) else SpanKind(kind)
        self.start_time = float(start_time) if start_time is not None else time.time()
        self.end_time: float | None = None
        self.status: SpanStatus = SpanStatus.UNSET
        self.status_message: str = ""

        # Determine Context
        if parent_context is not None:
            self.context = parent_context.child_context()
        else:
            current = tracer.get_current_context()
            if current is not None:
                self.context = current.child_context()
            else:
                self.context = TraceContext.new_root()

        self._attributes: dict[str, Any] = _sanitize_attributes(attributes or {})
        self._events: list[SpanEvent] = []
        self._links: list[SpanLink] = list(links or [])[:MAX_LINKS_PER_SPAN]
        self._resource_usage: dict[str, Any] = {}
        self._is_ended = False
        self._token: contextvars.Token[TraceContext | None] | None = None
        self._lock = threading.RLock()

    @property
    def trace_id(self) -> str:
        return self.context.trace_id

    @property
    def span_id(self) -> str:
        return self.context.span_id

    @property
    def parent_span_id(self) -> str | None:
        return self.context.parent_span_id

    def set_attribute(self, key: str, value: Any) -> "Span":
        """Set a single bounded attribute on the span."""
        with self._lock:
            if not self._is_ended:
                sanitized = _sanitize_attributes({key: value})
                self._attributes.update(sanitized)
        return self

    def set_attributes(self, attributes: dict[str, Any]) -> "Span":
        """Set multiple bounded attributes on the span."""
        with self._lock:
            if not self._is_ended and isinstance(attributes, dict):
                self._attributes.update(_sanitize_attributes(attributes))
        return self

    def add_event(self, name: str, attributes: dict[str, Any] | None = None, timestamp: float | None = None) -> "Span":
        """Record a timestamped event within the span."""
        with self._lock:
            if not self._is_ended and len(self._events) < MAX_EVENTS_PER_SPAN:
                event = SpanEvent(
                    name=name,
                    timestamp=timestamp if timestamp is not None else time.time(),
                    attributes=attributes or {},
                )
                self._events.append(event)
        return self

    def record_exception(self, exception: BaseException, escaped: bool = False) -> "Span":
        """Record an exception event and mark span status as ERROR."""
        with self._lock:
            if not self._is_ended:
                ex_type = type(exception).__name__
                ex_msg = str(exception)
                self.add_event(
                    name="exception",
                    attributes={
                        "exception.type": ex_type,
                        "exception.message": ex_msg[:1024],
                        "exception.escaped": bool(escaped),
                    },
                )
                self.set_status(SpanStatus.ERROR, message=f"{ex_type}: {ex_msg[:256]}")
        return self

    def set_status(self, status: SpanStatus, message: str = "") -> "Span":
        """Update span completion status."""
        with self._lock:
            if not self._is_ended:
                self.status = status if isinstance(status, SpanStatus) else SpanStatus(status)
                self.status_message = str(message or "").strip()
        return self

    def record_resource_usage(
        self,
        tokens: int = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cpu_ms: float = 0.0,
        tool_calls: int = 0,
        cost_estimate_usd: float = 0.0,
        **kwargs: Any,
    ) -> "Span":
        """Record execution resource consumption metrics."""
        with self._lock:
            if not self._is_ended:
                if tokens > 0:
                    self._resource_usage["tokens"] = int(self._resource_usage.get("tokens", 0) + tokens)
                if prompt_tokens > 0:
                    self._resource_usage["prompt_tokens"] = int(self._resource_usage.get("prompt_tokens", 0) + prompt_tokens)
                if completion_tokens > 0:
                    self._resource_usage["completion_tokens"] = int(self._resource_usage.get("completion_tokens", 0) + completion_tokens)
                if cpu_ms > 0:
                    self._resource_usage["cpu_ms"] = float(self._resource_usage.get("cpu_ms", 0.0) + cpu_ms)
                if tool_calls > 0:
                    self._resource_usage["tool_calls"] = int(self._resource_usage.get("tool_calls", 0) + tool_calls)
                if cost_estimate_usd > 0:
                    self._resource_usage["cost_estimate_usd"] = float(self._resource_usage.get("cost_estimate_usd", 0.0) + cost_estimate_usd)
                for k, v in kwargs.items():
                    if isinstance(v, (int, float)):
                        self._resource_usage[k] = v
        return self

    def end(self, end_time: float | None = None) -> SpanRecord:
        """End the span, construct an immutable SpanRecord, and notify the tracer."""
        with self._lock:
            if self._is_ended:
                raise RuntimeError(f"Span '{self.name}' ({self.span_id}) has already ended.")
            self._is_ended = True
            self.end_time = float(end_time) if end_time is not None else time.time()
            if self.status == SpanStatus.UNSET:
                self.status = SpanStatus.OK

            duration_ms = max(0.0, (self.end_time - self.start_time) * 1000.0)

            record = SpanRecord(
                trace_id=self.context.trace_id,
                span_id=self.context.span_id,
                parent_span_id=self.context.parent_span_id,
                name=self.name,
                kind=self.kind,
                status=self.status,
                status_message=self.status_message,
                start_time=self.start_time,
                end_time=self.end_time,
                duration_ms=duration_ms,
                attributes=dict(self._attributes),
                events=tuple(self._events),
                links=tuple(self._links),
                resource_usage=dict(self._resource_usage),
            )

        # Restore previous ContextVar state
        if self._token is not None:
            _active_trace_context.reset(self._token)
            self._token = None

        self.tracer._dispatch_span(record)
        return record

    def __enter__(self) -> "Span":
        self._token = _active_trace_context.set(self.context)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        if exc_val is not None and not self._is_ended:
            self.record_exception(exc_val, escaped=False)
        if not self._is_ended:
            self.end()
        return False


class Tracer:
    """Core distributed tracer coordinating span lifecycle and context propagation."""

    def __init__(self, service_name: str = "project-aura"):
        self.service_name = service_name
        self._exporters: list[Any] = []
        self._lock = threading.RLock()

    def get_current_context(self) -> TraceContext | None:
        """Get the currently active TraceContext in this async/thread context."""
        return _active_trace_context.get()

    def set_current_context(self, context: TraceContext | None) -> contextvars.Token[TraceContext | None]:
        """Manually set the active TraceContext."""
        return _active_trace_context.set(context)

    def start_span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        parent_context: TraceContext | None = None,
        attributes: dict[str, Any] | None = None,
        links: Sequence[SpanLink] | None = None,
        start_time: float | None = None,
    ) -> Span:
        """Create a new bounded Span context."""
        eff_attrs = dict(attributes or {})
        eff_attrs["service.name"] = self.service_name
        return Span(
            tracer=self,
            name=name,
            kind=kind,
            parent_context=parent_context,
            attributes=eff_attrs,
            links=links,
            start_time=start_time,
        )

    def inject(self, carrier: dict[str, Any], context: TraceContext | None = None) -> dict[str, Any]:
        """Inject traceparent, tracestate, and baggage into a carrier dictionary."""
        ctx = context if context is not None else self.get_current_context()
        if ctx is None or not isinstance(carrier, dict):
            return carrier
        carrier["traceparent"] = ctx.to_traceparent()
        if ctx.trace_state:
            carrier["tracestate"] = ctx.trace_state
        if ctx.baggage:
            carrier["baggage"] = dict(ctx.baggage)
        return carrier

    def extract(self, carrier: dict[str, Any]) -> TraceContext | None:
        """Extract TraceContext from carrier dictionary headers or metadata."""
        if not isinstance(carrier, dict):
            return None
        header = carrier.get("traceparent") or carrier.get("trace_parent")
        baggage = carrier.get("baggage")
        clean_baggage = dict(baggage) if isinstance(baggage, dict) else {}
        if isinstance(header, str):
            ctx = TraceContext.from_traceparent(header, baggage=clean_baggage)
            if ctx is not None:
                return ctx
        # Fallback to direct dict representation
        if "trace_id" in carrier and "span_id" in carrier:
            try:
                return TraceContext.from_dict(carrier)
            except Exception:
                pass
        return None

    def register_exporter(self, exporter: Any) -> None:
        """Register an exporter for receiving completed SpanRecords."""
        with self._lock:
            if exporter not in self._exporters:
                self._exporters.append(exporter)

    def unregister_exporter(self, exporter: Any) -> None:
        """Unregister a trace exporter."""
        with self._lock:
            if exporter in self._exporters:
                self._exporters.remove(exporter)

    def _dispatch_span(self, record: SpanRecord) -> None:
        """Internal dispatch of completed span record to all exporters."""
        with self._lock:
            exporters = list(self._exporters)
        for exp in exporters:
            try:
                exp.export([record])
            except Exception as e:
                logger.warning("Trace exporter %s failed to export span: %s", exp, e)

    def flush(self) -> None:
        """Flush all registered exporters."""
        with self._lock:
            exporters = list(self._exporters)
        for exp in exporters:
            if hasattr(exp, "flush") and callable(exp.flush):
                try:
                    exp.flush()
                except Exception as e:
                    logger.warning("Failed to flush exporter %s: %s", exp, e)

    def shutdown(self) -> None:
        """Shutdown all registered exporters."""
        with self._lock:
            exporters = list(self._exporters)
        for exp in exporters:
            if hasattr(exp, "shutdown") and callable(exp.shutdown):
                try:
                    exp.shutdown()
                except Exception as e:
                    logger.warning("Failed to shutdown exporter %s: %s", exp, e)
