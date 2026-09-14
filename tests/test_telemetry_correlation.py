"""Tests for Request Correlation & W3C Trace Context Propagation (M44)."""

import uuid
import pytest

from core.telemetry_context import (
    clear_correlation_context,
    get_correlation_context,
    get_current_request_id,
    get_current_trace_id,
    set_correlation_context,
    validate_or_generate_request_id,
)
from core.trace_types import TraceContext


def test_validate_valid_request_id():
    valid_id = "req-1234-abcd_EFGH.5678"
    res = validate_or_generate_request_id(valid_id)
    assert res == valid_id


def test_missing_request_id_generates_uuid():
    res1 = validate_or_generate_request_id(None)
    res2 = validate_or_generate_request_id("")
    # Both should be valid UUIDs
    assert uuid.UUID(res1)
    assert uuid.UUID(res2)
    assert res1 != res2


def test_invalid_request_id_sanitized_to_uuid():
    # Header injection / control chars / malicious payload
    malicious_id = "req_123\r\nInjected-Header: evil\x00"
    res = validate_or_generate_request_id(malicious_id)
    # Must reject and generate safe UUID
    assert uuid.UUID(res)
    assert "\r" not in res
    assert "evil" not in res


def test_w3c_traceparent_parsing():
    raw_tp = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    ctx = TraceContext.from_w3c_traceparent(raw_tp)
    assert ctx.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert ctx.span_id == "00f067aa0ba902b7"
    assert ctx.is_sampled is True


def test_correlation_context_lifecycle():
    clear_correlation_context()
    assert get_current_request_id() is None
    assert get_current_trace_id() is None

    tokens = set_correlation_context(
        request_id="req_test_001",
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        span_id="00f067aa0ba902b7",
        user_id="usr_alice",
    )

    ctx = get_correlation_context()
    assert ctx["request_id"] == "req_test_001"
    assert ctx["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert ctx["span_id"] == "00f067aa0ba902b7"
    assert ctx["user_id"] == "usr_alice"

    clear_correlation_context()
    assert get_current_request_id() is None
