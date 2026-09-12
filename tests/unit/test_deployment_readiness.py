"""Unit tests for Production & Deployment Readiness Hardening."""

import json
import time
from http import HTTPStatus
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.server import (
    AURAHTTPRequestHandler,
    AURAHTTPServer,
    _default_json_encoder,
)
from core.security_scrubber import (
    REDACTED_STR,
    sanitize_error_message,
    scrub_dict,
    scrub_string,
)
from core.models import AURARequest, AURAResponse
from uuid import UUID
from pathlib import Path
from enum import Enum


def test_config_production_defaults():
    """Verify production-safe default configuration parameters."""
    cfg = Settings()
    assert cfg.aura_server_host == "0.0.0.0"
    assert cfg.aura_server_port == 8000
    assert cfg.aura_api_key_auth_enabled is False
    assert cfg.aura_max_request_body_bytes == 1048576
    assert cfg.aura_checkpoint_dir == ".aura_checkpoints"
    assert cfg.aura_artifact_storage_dir == ".aura_artifacts"
    assert cfg.aura_trace_storage_dir == ".aura_traces"
    assert cfg.aura_skills_storage_dir == ".aura_skills"
    assert cfg.aura_knowledge_storage_dir == ".aura_knowledge"


def test_security_scrubber_masks_keys():
    """Verify API keys and sensitive headers are redacted."""
    raw_str = "Error with OpenAI key sk-1234567890abcdef1234567890 and Bearer secret-token-abcdef123456"
    scrubbed = scrub_string(raw_str)
    assert "sk-123" in scrubbed
    assert REDACTED_STR in scrubbed
    assert "secret-token-abcdef" not in scrubbed


def test_security_scrubber_masks_dict():
    """Verify sensitive dictionary fields are recursively masked."""
    data = {
        "user": "alice",
        "api_key": "secret-123",
        "nested": {
            "token": "tok-456",
            "safe_val": 42,
        },
        "list_items": [{"authorization": "Bearer xyz"}, "regular string"],
    }
    scrubbed = scrub_dict(data)
    assert scrubbed["user"] == "alice"
    assert scrubbed["api_key"] == REDACTED_STR
    assert scrubbed["nested"]["token"] == REDACTED_STR
    assert scrubbed["nested"]["safe_val"] == 42
    assert scrubbed["list_items"][0]["authorization"] == REDACTED_STR


def test_sanitize_error_message_production():
    """Verify error message sanitization in production mode."""
    err = "Traceback (most recent call last):\n  File 'app.py', line 10\nValueError: failed"
    # In production, tracebacks are replaced
    msg = sanitize_error_message(err, is_production=True)
    assert "Internal server error occurred." in msg

    # In development, traceback is preserved but scrubbed
    dev_msg = sanitize_error_message(err, is_production=False)
    assert "Traceback" in dev_msg


def test_server_initialization_and_configuration():
    """Verify AURAHTTPServer properly initializes with custom host/port."""
    cfg = Settings(aura_server_host="127.0.0.1", aura_server_port=9090)
    server = AURAHTTPServer(config=cfg, host="127.0.0.1", port=9090)
    assert server.host == "127.0.0.1"
    assert server.port == 9090
    assert server.aura is not None


def test_default_json_encoder():
    """Verify custom types serialization in server JSON encoder."""
    class SampleEnum(Enum):
        ALPHA = "alpha"

    uid = UUID("12345678-1234-5678-1234-567812345678")
    pth = Path("/app/data")
    assert _default_json_encoder(uid) == "12345678-1234-5678-1234-567812345678"
    assert _default_json_encoder(pth) == str(Path("/app/data"))
    assert _default_json_encoder(SampleEnum.ALPHA) == "alpha"
    assert _default_json_encoder({1, 2, 3}) == [1, 2, 3] or _default_json_encoder({1, 2, 3}) == [3, 2, 1] or sorted(_default_json_encoder({1, 2, 3})) == [1, 2, 3]


def test_server_graceful_shutdown_without_active_instance():
    """Verify that calling stop on an unstarted server handles safely."""
    server = AURAHTTPServer()
    # Should not raise
    server.stop()
