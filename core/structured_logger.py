"""Structured JSON Lines Logger & Formatter (M44).

Provides low-overhead, production JSON logging with automatic ContextVars injection
for request_id, trace_id, span_id, and user_id, integrated with deterministic
security scrubbing for credentials, tokens, and exception payloads.
"""

from __future__ import annotations

import datetime
import json
import logging
import sys
import traceback
from typing import Any

from core.security_scrubber import (
    sanitize_error_message,
    scrub_dict,
    scrub_string,
)
from core.telemetry_context import (
    get_current_request_id,
    get_current_span_id,
    get_current_trace_id,
    get_current_user_id,
)

# Standard logging record attributes to exclude from custom extra fields
RESERVED_RECORD_ATTRS = frozenset({
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "message",
    "asctime",
})


def _json_serial_default(obj: Any) -> Any:
    """Safe default serializer for JSON encoding."""
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
    return str(obj)


class StructuredJsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON with ContextVars and security scrubbing."""

    def __init__(
        self,
        service_name: str = "aura",
        environment: str = "development",
        scrub_secrets: bool = True,
    ) -> None:
        super().__init__()
        self.service_name = service_name
        self.environment = environment
        self.scrub_secrets = scrub_secrets

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record into a scrubbed, structured JSON line."""
        # 1. Base log record fields
        timestamp = datetime.datetime.fromtimestamp(
            record.created, tz=datetime.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        raw_msg = record.getMessage()
        msg = scrub_string(raw_msg) if self.scrub_secrets else raw_msg

        # 2. Extract ContextVars
        req_id = getattr(record, "request_id", None) or get_current_request_id()
        tr_id = getattr(record, "trace_id", None) or get_current_trace_id()
        sp_id = getattr(record, "span_id", None) or get_current_span_id()
        usr_id = getattr(record, "user_id", None) or get_current_user_id()

        payload: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": msg,
            "service": self.service_name,
            "environment": self.environment,
        }

        # Event classification
        event = getattr(record, "event", None)
        if event:
            payload["event"] = str(event)

        # Correlation IDs
        if req_id:
            payload["request_id"] = str(req_id)
        if tr_id:
            payload["trace_id"] = str(tr_id)
        if sp_id:
            payload["span_id"] = str(sp_id)
        if usr_id:
            payload["user_id"] = str(usr_id)

        # 3. Add explicit duration or status code if present
        if hasattr(record, "duration_ms"):
            payload["duration_ms"] = float(record.duration_ms)
        if hasattr(record, "status_code"):
            payload["status_code"] = int(record.status_code)

        # 4. Exception / Error details
        if record.exc_info:
            exc_type, exc_val, exc_tb = record.exc_info
            if exc_val is not None:
                err_msg = str(exc_val)
                safe_err = (
                    sanitize_error_message(err_msg, is_production=(self.environment == "production"))
                    if self.scrub_secrets
                    else err_msg
                )
                payload["error"] = {
                    "type": exc_type.__name__ if exc_type else "Exception",
                    "message": safe_err,
                }
                if self.environment != "production":
                    # Include sanitized traceback in non-production
                    raw_tb = "".join(traceback.format_exception(exc_type, exc_val, exc_tb))
                    payload["error"]["traceback"] = scrub_string(raw_tb) if self.scrub_secrets else raw_tb

        # 5. Extract additional extra fields
        for k, v in record.__dict__.items():
            if k not in RESERVED_RECORD_ATTRS and k not in payload and not k.startswith("_"):
                cleaned_val = scrub_dict(v) if (isinstance(v, dict) and self.scrub_secrets) else v
                payload[k] = cleaned_val

        if self.scrub_secrets:
            payload = scrub_dict(payload)

        return json.dumps(payload, default=_json_serial_default, ensure_ascii=False)


def configure_structured_logging(
    level: str = "INFO",
    json_format: bool = True,
    service_name: str = "aura",
    environment: str = "development",
) -> None:
    """Configure root logger with StructuredJsonFormatter or standard text formatter."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers to prevent duplicate lines
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)

    if json_format:
        formatter = StructuredJsonFormatter(
            service_name=service_name,
            environment=environment,
            scrub_secrets=True,
        )
    else:
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
