"""Telemetry and Correlation Context Management (M44).

Provides thread-safe and async-safe ContextVars for correlation identifiers:
- request_id (UUID or validated safe correlation slug)
- trace_id (32-hex W3C trace ID)
- span_id (16-hex W3C span ID)
- user_id (principal identifier)

Enforces strict separation between correlation metadata (X-Request-ID / traceparent)
and security identity (authentication principal).
"""

from __future__ import annotations

import contextvars
import re
import uuid
from typing import Any

# Regex for validating safe incoming X-Request-ID strings (alphanumeric, dashes, underscores, max 128 chars)
SAFE_REQUEST_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]{1,128}$")

# ContextVars for active request correlation
_current_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_request_id", default=None
)
_current_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_trace_id", default=None
)
_current_span_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_span_id", default=None
)
_current_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_user_id", default=None
)


def validate_or_generate_request_id(incoming_id: str | None = None) -> str:
    """Validate incoming X-Request-ID or generate a safe UUIDv4 string.
    
    Ensures the correlation ID is safe against header injection, control characters,
    and excessive length. Note: X-Request-ID is correlation metadata, NOT security identity.
    """
    if incoming_id and isinstance(incoming_id, str):
        cleaned = incoming_id.strip()
        if SAFE_REQUEST_ID_REGEX.match(cleaned):
            return cleaned
    return str(uuid.uuid4())


def set_correlation_context(
    request_id: str | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, contextvars.Token]:
    """Set active correlation ContextVars and return tokens for reset."""
    tokens: dict[str, contextvars.Token] = {}
    if request_id is not None:
        tokens["request_id"] = _current_request_id.set(request_id)
    if trace_id is not None:
        tokens["trace_id"] = _current_trace_id.set(trace_id)
    if span_id is not None:
        tokens["span_id"] = _current_span_id.set(span_id)
    if user_id is not None:
        tokens["user_id"] = _current_user_id.set(user_id)
    return tokens


def reset_correlation_context(tokens: dict[str, contextvars.Token]) -> None:
    """Reset correlation ContextVars using previous tokens."""
    if "request_id" in tokens:
        _current_request_id.reset(tokens["request_id"])
    if "trace_id" in tokens:
        _current_trace_id.reset(tokens["trace_id"])
    if "span_id" in tokens:
        _current_span_id.reset(tokens["span_id"])
    if "user_id" in tokens:
        _current_user_id.reset(tokens["user_id"])


def clear_correlation_context() -> None:
    """Clear all correlation ContextVars."""
    _current_request_id.set(None)
    _current_trace_id.set(None)
    _current_span_id.set(None)
    _current_user_id.set(None)


def get_current_request_id() -> str | None:
    """Return active correlation request_id."""
    return _current_request_id.get()


def get_current_trace_id() -> str | None:
    """Return active W3C trace_id."""
    val = _current_trace_id.get()
    if val:
        return val
    try:
        from core.tracing import get_active_trace_context
        active = get_active_trace_context()
        return active.trace_id if active else None
    except Exception:
        return None


def get_current_span_id() -> str | None:
    """Return active W3C span_id."""
    val = _current_span_id.get()
    if val:
        return val
    try:
        from core.tracing import get_active_trace_context
        active = get_active_trace_context()
        return active.span_id if active else None
    except Exception:
        return None


def get_current_user_id() -> str | None:
    """Return active principal user_id."""
    return _current_user_id.get()


def get_correlation_context() -> dict[str, Any]:
    """Return snapshot of active correlation identifiers."""
    return {
        "request_id": get_current_request_id(),
        "trace_id": get_current_trace_id(),
        "span_id": get_current_span_id(),
        "user_id": get_current_user_id(),
    }
