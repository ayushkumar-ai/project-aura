"""Unit tests for M24 Trace Types, TraceContext & Data Contracts."""

import pytest
from core.provenance import wrap_tainted
from core.trace_types import (
    SpanEvent,
    SpanKind,
    SpanLink,
    SpanRecord,
    SpanStatus,
    TraceContext,
    _sanitize_attributes,
)


def test_trace_context_root_and_child():
    root = TraceContext.new_root(baggage={"user": "alice", "approved": "true"})
    assert root.trace_id
    assert root.span_id
    assert root.parent_span_id is None
    assert root.baggage.get("user") == "alice"
    assert "approved" not in root.baggage  # forbidden key stripped

    child = root.child_context(extra_baggage={"role": "researcher"})
    assert child.trace_id == root.trace_id
    assert child.parent_span_id == root.span_id
    assert child.span_id != root.span_id
    assert child.baggage.get("user") == "alice"
    assert child.baggage.get("role") == "researcher"


def test_traceparent_serialization_and_parsing():
    ctx = TraceContext(trace_id="4bf92f3577b34da6a3ce929d0e0e4736", span_id="00f067aa0ba902b7", is_sampled=True)
    header = ctx.to_traceparent()
    assert header == "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

    parsed = TraceContext.from_traceparent(header)
    assert parsed is not None
    assert parsed.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert parsed.span_id == "00f067aa0ba902b7"
    assert parsed.is_sampled is True

    # Invalid header parsing
    assert TraceContext.from_traceparent("invalid") is None
    assert TraceContext.from_traceparent("01-abc-123-00") is None


def test_span_record_and_events():
    event = SpanEvent(name="cache_hit", attributes={"size": 1024, "is_admin": "true"})
    assert event.name == "cache_hit"
    assert "is_admin" not in event.attributes  # forbidden key stripped

    link = SpanLink(trace_id="t123", span_id="s456", attributes={"rel": "parent_goal"})
    assert link.trace_id == "t123"

    record = SpanRecord(
        trace_id="t1",
        span_id="s1",
        parent_span_id=None,
        name="execute_tool",
        kind=SpanKind.TOOL_CALL,
        status=SpanStatus.OK,
        status_message="",
        start_time=100.0,
        end_time=100.5,
        duration_ms=500.0,
        attributes={"tool_name": "web_search", "sudo": "true"},
        events=(event,),
        links=(link,),
        resource_usage={"tokens": 150, "tool_calls": 1},
    )

    assert record.name == "execute_tool"
    assert record.kind == SpanKind.TOOL_CALL
    assert record.duration_ms == 500.0
    assert "sudo" not in record.attributes
    assert record.resource_usage["tokens"] == 150

    d = record.to_dict()
    restored = SpanRecord.from_dict(d)
    assert restored.span_id == record.span_id
    assert restored.kind == SpanKind.TOOL_CALL
    assert len(restored.events) == 1
    assert restored.events[0].name == "cache_hit"


def test_tainted_value_preserved_in_trace_attributes():
    tainted = wrap_tainted("untrusted_prompt", is_untrusted=True, source_type="user_web")
    sanitized = _sanitize_attributes({"input": tainted})
    assert "__tainted__" in sanitized["input"]
    assert sanitized["input"]["is_untrusted"] is True
