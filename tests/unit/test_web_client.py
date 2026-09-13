"""Unit and integration tests for Project AURA Web Client interface."""

import json
import os
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
import pytest

from app.config import Settings
from app.server import AURAHTTPServer


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http_get(url: str, headers: dict[str, str] | None = None) -> tuple[int, bytes, dict[str, str]]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = resp.read()
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, data, resp_headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), {k.lower(): v for k, v in e.headers.items()}


def _http_post(url: str, json_data: dict, headers: dict[str, str] | None = None) -> tuple[int, dict, dict[str, str]]:
    body = json.dumps(json_data).encode("utf-8")
    req_headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    }
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, data, resp_headers
    except urllib.error.HTTPError as e:
        data = json.loads(e.read().decode("utf-8")) if e.fp else {}
        return e.code, data, {k.lower(): v for k, v in e.headers.items()}


@pytest.fixture(scope="module")
def running_server():
    port = _find_free_port()
    config = Settings(
        aura_env="testing",
        aura_server_host="127.0.0.1",
        aura_server_port=port,
        aura_api_key_auth_enabled=False,
    )
    server = AURAHTTPServer(config=config, host="127.0.0.1", port=port)
    server.start(block=False)
    time.sleep(0.3)
    base_url = f"http://127.0.0.1:{port}"
    yield base_url
    server.stop()


def test_web_client_index_loads(running_server):
    """Verify GET / serves index.html with HTML Content-Type and security headers."""
    status, body, headers = _http_get(f"{running_server}/")
    assert status == 200
    assert "text/html" in headers.get("content-type", "")
    assert "nosniff" in headers.get("x-content-type-options", "")
    assert "DENY" in headers.get("x-frame-options", "")
    text = body.decode("utf-8")
    assert "Welcome to Project AURA" in text
    assert "AURA" in text
    assert "messages-container" in text


def test_web_client_ui_alias_loads(running_server):
    """Verify GET /ui serves index.html alias."""
    status, body, headers = _http_get(f"{running_server}/ui")
    assert status == 200
    assert "text/html" in headers.get("content-type", "")
    assert "Welcome to Project AURA" in body.decode("utf-8")


def test_web_client_static_css_loads(running_server):
    """Verify GET /static/style.css serves valid CSS stylesheet."""
    status, body, headers = _http_get(f"{running_server}/static/style.css")
    assert status == 200
    assert "text/css" in headers.get("content-type", "")
    css_text = body.decode("utf-8")
    assert "--accent-primary" in css_text
    assert ".chat-workspace" in css_text


def test_web_client_static_js_loads(running_server):
    """Verify GET /static/app.js serves client application JavaScript."""
    status, body, headers = _http_get(f"{running_server}/static/app.js")
    assert status == 200
    assert "application/javascript" in headers.get("content-type", "")
    js_text = body.decode("utf-8")
    assert "escapeHtml" in js_text
    assert "sendMessage" in js_text
    assert "checkServerHealth" in js_text


def test_web_client_static_traversal_blocked(running_server):
    """Security: Verify directory traversal attacks are blocked with 403 Forbidden."""
    status, _, _ = _http_get(f"{running_server}/static/../../app/server.py")
    assert status in (403, 404)


def test_web_client_run_api_execution(running_server):
    """Verify client POST /v1/run sends user_input and receives assistant response with request_id."""
    status, data, _ = _http_post(
        f"{running_server}/v1/run",
        {"user_input": "Hello AURA Web Client", "metadata": {"client": "test"}},
    )
    assert status == 200
    assert "content" in data
    assert "request_id" in data
    assert len(data["request_id"]) > 0


def test_web_client_source_contains_no_secrets():
    """Security: Verify no API keys, credentials, or .env secrets are in client static files."""
    static_dir = Path("app/static")
    assert static_dir.exists()

    forbidden_strings = ["sk-", "AIzaSy", "ghp_", "api_key = ", "secret_key = "]

    for filename in ("index.html", "style.css", "app.js"):
        file_path = static_dir / filename
        assert file_path.exists()
        content = file_path.read_text(encoding="utf-8")
        for forbidden in forbidden_strings:
            assert forbidden not in content, f"Potential secret pattern '{forbidden}' found in {filename}"
