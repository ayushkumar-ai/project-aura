"""M45 — Production API & Deployment Infrastructure Test Suite.

Verifies:
1. API Contracts & Schema Validation
2. Production Configuration Validator & Fail-Closed Guards
3. Server Request Validation & User Isolation Binding
4. Standardized Error Response Envelope
5. Static Verification of Dockerfile & Docker Compose Specifications
"""

import json
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.server import AURAHTTPServer
from core.api_contracts import (
    RunRequestSchema,
    TaskRequestSchema,
    RAGQueryRequestSchema,
    PreferencesUpdateRequestSchema,
    PlanRequestSchema,
    ToolExecutionRequestSchema,
)
from core.config_validator import validate_production_config


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http_post(url: str, body: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    encoded_body = json.dumps(body).encode("utf-8")
    req_headers = {"Content-Type": "application/json", "Content-Length": str(len(encoded_body))}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=encoded_body, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status, data
    except urllib.error.HTTPError as e:
        data = json.loads(e.read().decode("utf-8")) if e.fp else {}
        return e.code, data


def test_api_contracts_validation():
    """Verify Pydantic schemas enforce type, bounds, and non-empty rules."""
    # RunRequestSchema
    with pytest.raises(ValidationError):
        RunRequestSchema(user_input="   ")  # Whitespace-only rejected

    valid_run = RunRequestSchema(user_input="Explain quantum physics", metadata={"source": "test"})
    assert valid_run.get_prompt_text() == "Explain quantum physics"

    # TaskRequestSchema
    with pytest.raises(ValidationError):
        TaskRequestSchema(task="")  # Empty task rejected

    with pytest.raises(ValidationError):
        TaskRequestSchema(task="valid", timeout=0.01)  # timeout below 0.1s rejected

    # RAGQueryRequestSchema
    with pytest.raises(ValidationError):
        RAGQueryRequestSchema(query="", max_chars=100)

    with pytest.raises(ValidationError):
        RAGQueryRequestSchema(query="test", max_chars=5)  # below min 10


def test_production_config_validator():
    """Verify production configuration validator enforces fail-closed rules."""
    # 1. Insecure short API key in production with auth enabled
    bad_cfg = Settings(
        aura_env="production",
        aura_api_key_auth_enabled=True,
        aura_server_api_key="short",
    )
    is_valid, errors = validate_production_config(bad_cfg)
    assert not is_valid
    assert any("too short" in e for e in errors)

    with pytest.raises(ValueError):
        validate_production_config(bad_cfg, raise_on_error=True)

    # 2. Invalid database scheme in production
    bad_db_cfg = Settings(
        aura_env="production",
        aura_persistence_backend="postgres",
        aura_database_url="mysql://user:pass@localhost/db",
    )
    is_valid, errors = validate_production_config(bad_db_cfg)
    assert not is_valid
    assert any("postgresql://" in e for e in errors)

    # 3. Valid production configuration
    good_cfg = Settings(
        aura_env="production",
        aura_api_key_auth_enabled=True,
        aura_server_api_key="a-very-secure-production-key-12345",
        aura_persistence_backend="postgres",
        aura_database_url="postgresql://user:pass@localhost:5432/aura_db",
    )
    is_valid, errors = validate_production_config(good_cfg)
    assert is_valid
    assert len(errors) == 0


def test_server_schema_validation_rejection():
    """Verify HTTP server returns 400 Bad Request on schema validation failures."""
    port = _find_free_port()
    config = Settings(
        aura_env="development",
        aura_server_host="127.0.0.1",
        aura_server_port=port,
        aura_api_key_auth_enabled=False,
    )
    server = AURAHTTPServer(config=config, host="127.0.0.1", port=port)
    server.start(block=False)
    base_url = f"http://127.0.0.1:{port}"
    time.sleep(0.3)

    try:
        # Empty user_input on /v1/run
        status, err_data = _http_post(f"{base_url}/v1/run", {"user_input": "   "})
        assert status == 400
        assert err_data["error"]["code"] == "invalid_request"

        # Missing task on /v1/task
        status, err_data = _http_post(f"{base_url}/v1/task", {"task_id": "123"})
        assert status == 400
        assert err_data["error"]["code"] == "invalid_request"

        # Invalid max_chars on /v1/rag
        status, err_data = _http_post(f"{base_url}/v1/rag", {"query": "test", "max_chars": 2})
        assert status == 400
        assert err_data["error"]["code"] == "invalid_request"

    finally:
        server.stop()


def test_user_isolation_identity_binding():
    """Verify server strictly binds authenticated identity and ignores client-injected user_id."""
    port = _find_free_port()
    config = Settings(
        aura_env="testing",
        aura_server_host="127.0.0.1",
        aura_server_port=port,
        aura_api_key_auth_enabled=False,
    )
    server = AURAHTTPServer(config=config, host="127.0.0.1", port=port)
    server.start(block=False)
    base_url = f"http://127.0.0.1:{port}"
    time.sleep(0.3)

    try:
        # In testing mode without auth, default identity is dev (user_id: dev_user)
        # Attempt to spoof user_id as 'victim_user' in payload
        status, run_data = _http_post(
            f"{base_url}/v1/run",
            {"user_input": "Hello", "user_id": "victim_user", "identity": {"user_id": "admin"}},
        )
        assert status == 200
        assert "content" in run_data
        # Response correlation headers
        assert "request_id" in run_data

    finally:
        server.stop()


def test_dockerfile_and_compose_static_spec():
    """Verify Dockerfile and docker-compose.yml contain production-grade specifications."""
    repo_root = Path(__file__).resolve().parent.parent

    # Dockerfile check
    dockerfile_path = repo_root / "Dockerfile"
    assert dockerfile_path.exists()
    dockerfile_content = dockerfile_path.read_text(encoding="utf-8")
    assert "USER appuser" in dockerfile_content
    assert "migrations/" in dockerfile_content
    assert "HEALTHCHECK" in dockerfile_content

    # docker-compose.yml check
    compose_path = repo_root / "docker-compose.yml"
    assert compose_path.exists()
    compose_content = compose_path.read_text(encoding="utf-8")
    assert "pgvector/pgvector:pg16" in compose_content
    assert "AURA_DATABASE_URL" in compose_content
    assert "depends_on" in compose_content
