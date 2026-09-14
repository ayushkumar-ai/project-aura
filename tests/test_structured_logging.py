"""Tests for Structured JSON Logging & Secret Sanitization (M44)."""

import json
import logging
import pytest

from core.security_scrubber import REDACTED_STR
from core.structured_logger import StructuredJsonFormatter
from core.telemetry_context import (
    clear_correlation_context,
    set_correlation_context,
)


def test_structured_json_formatting():
    formatter = StructuredJsonFormatter(service_name="aura_test", environment="test")
    record = logging.LogRecord(
        name="aura.test",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Test event message",
        args=(),
        exc_info=None,
    )
    record.event = "http_request_start"
    record.duration_ms = 45.2
    record.status_code = 200

    out = formatter.format(record)
    data = json.loads(out)

    assert data["level"] == "INFO"
    assert data["logger"] == "aura.test"
    assert data["message"] == "Test event message"
    assert data["service"] == "aura_test"
    assert data["environment"] == "test"
    assert data["event"] == "http_request_start"
    assert data["duration_ms"] == 45.2
    assert data["status_code"] == 200
    assert "timestamp" in data


def test_automatic_contextvars_injection():
    formatter = StructuredJsonFormatter(service_name="aura", environment="production")
    clear_correlation_context()

    set_correlation_context(
        request_id="req_998811",
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        span_id="00f067aa0ba902b7",
        user_id="usr_admin_01",
    )

    record = logging.LogRecord(
        name="aura.core",
        level=logging.INFO,
        pathname="core.py",
        lineno=20,
        msg="Processing transaction",
        args=(),
        exc_info=None,
    )

    out = formatter.format(record)
    data = json.loads(out)

    assert data["request_id"] == "req_998811"
    assert data["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert data["span_id"] == "00f067aa0ba902b7"
    assert data["user_id"] == "usr_admin_01"

    clear_correlation_context()


def test_secret_scrubbing_in_logs():
    formatter = StructuredJsonFormatter(service_name="aura", environment="production", scrub_secrets=True)

    # API key, Bearer token, and DB password
    sensitive_msg = (
        "Connected to postgresql://aura_user:SuperSecretPass123@localhost:5432/aura with "
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 and OpenAI key sk-abcdef1234567890abcdef1234567890"
    )

    record = logging.LogRecord(
        name="aura.db",
        level=logging.INFO,
        pathname="db.py",
        lineno=30,
        msg=sensitive_msg,
        args=(),
        exc_info=None,
    )
    record.api_key = "sk-live-secret-key-123456789"

    out = formatter.format(record)
    data = json.loads(out)

    # Sensitive strings must NOT appear in output
    assert "SuperSecretPass123" not in out
    assert "sk-live-secret-key-123456789" not in out
    assert REDACTED_STR in data["message"]


def test_exception_sanitization_in_production():
    formatter = StructuredJsonFormatter(service_name="aura", environment="production", scrub_secrets=True)

    try:
        raise ValueError("Failed connecting to postgres://admin:P@ssword999@db:5432/aura")
    except ValueError as e:
        import sys
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="aura.error",
        level=logging.ERROR,
        pathname="error.py",
        lineno=40,
        msg="Database error occurred",
        args=(),
        exc_info=exc_info,
    )

    out = formatter.format(record)
    data = json.loads(out)

    assert data["level"] == "ERROR"
    assert "error" in data
    assert data["error"]["type"] == "ValueError"
    # Password must be redacted
    assert "P@ssword999" not in out
    # Traceback should be omitted in production mode
    assert "traceback" not in data["error"]
