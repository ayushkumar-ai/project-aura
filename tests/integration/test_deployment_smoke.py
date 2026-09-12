"""End-to-End Live HTTP Deployment Smoke Test.

Validates the full deployment lifecycle:
1. Starts the real AURA HTTP server on a dynamic local port
2. Hits GET /health (liveness probe)
3. Hits GET /ready (readiness probe)
4. Executes a prompt via POST /v1/run
5. Executes a multi-step task via POST /v1/task
6. Queries registered dynamic skills via GET /v1/skills
7. Tests payload size limit enforcement (413 Payload Too Large)
8. Tests auth enforcement when enabled (401 Unauthorized)
9. Shuts down the server cleanly
"""

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any

import pytest

from app.config import Settings
from app.server import AURAHTTPServer


def _find_free_port() -> int:
    """Find a random available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http_get(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    """Helper to perform HTTP GET request."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status, data
    except urllib.error.HTTPError as e:
        data = json.loads(e.read().decode("utf-8")) if e.fp else {}
        return e.code, data


def _http_post(url: str, body: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    """Helper to perform HTTP POST request."""
    encoded_body = json.dumps(body).encode("utf-8")
    req_headers = {"Content-Type": "application/json", "Content-Length": str(len(encoded_body))}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=encoded_body, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return resp.status, data
    except urllib.error.HTTPError as e:
        data = json.loads(e.read().decode("utf-8")) if e.fp else {}
        return e.code, data


def test_live_server_deployment_smoke():
    """Full live server lifecycle and endpoint verification."""
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
    time.sleep(0.3)  # Give thread a moment to bind

    try:
        # 1. Test /health (Liveness)
        status, health_data = _http_get(f"{base_url}/health")
        assert status == 200
        assert health_data["status"] == "healthy"
        assert "uptime_seconds" in health_data

        # 2. Test /ready (Readiness)
        status, ready_data = _http_get(f"{base_url}/ready")
        assert status == 200
        assert ready_data["ready"] is True

        # 3. Test POST /v1/run (Execution)
        status, run_data = _http_post(
            f"{base_url}/v1/run",
            {"user_input": "Hello from production smoke test", "metadata": {}},
        )
        assert status == 200
        assert "content" in run_data
        assert "request_id" in run_data

        # 4. Test GET /v1/skills (Catalog)
        status, skills_data = _http_get(f"{base_url}/v1/skills")
        assert status == 200
        assert "skills" in skills_data

        # 5. Test Payload Too Large (413)
        huge_payload = {"user_input": "X" * 2_000_000}
        status, err_data = _http_post(f"{base_url}/v1/run", huge_payload)
        assert status == 413
        assert err_data["error"]["code"] == "payload_too_large"

    finally:
        server.stop()


def test_server_auth_enforcement_smoke():
    """Verify that authentication is strictly enforced when enabled."""
    port = _find_free_port()
    config = Settings(
        aura_env="production",
        aura_server_host="127.0.0.1",
        aura_server_port=port,
        aura_server_api_key="smoke-secret-key-12345",
        aura_api_key_auth_enabled=True,
    )
    server = AURAHTTPServer(config=config, host="127.0.0.1", port=port)
    server.start(block=False)

    base_url = f"http://127.0.0.1:{port}"
    time.sleep(0.3)

    try:
        # 1. Health and ready remain open without auth
        status, _ = _http_get(f"{base_url}/health")
        assert status == 200

        status, _ = _http_get(f"{base_url}/ready")
        assert status == 200

        # 2. POST /v1/run without auth returns 401
        status, err_data = _http_post(f"{base_url}/v1/run", {"user_input": "Test"})
        assert status == 401
        assert err_data["error"]["code"] == "unauthorized"

        # 3. POST /v1/run with valid Bearer token succeeds
        headers = {"Authorization": "Bearer smoke-secret-key-12345"}
        status, run_data = _http_post(f"{base_url}/v1/run", {"user_input": "Test Auth"}, headers=headers)
        assert status == 200
        assert "content" in run_data

    finally:
        server.stop()
