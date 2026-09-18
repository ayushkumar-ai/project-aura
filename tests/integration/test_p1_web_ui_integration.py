"""P1 — Web Product UI Integration Tests.

Tests HTTP server static asset delivery, security headers (nosniff, DENY, CORS),
path traversal protections, and end-to-end endpoint accessibility for all 8 product views.
"""

from __future__ import annotations

import json
import threading
import time
from http import HTTPStatus
import urllib.request
import urllib.error
import pytest

from app.config import Settings
from app.server import AURAHTTPServer
from core.repositories.factory import create_in_memory_repositories
from core.identity import UserRole


@pytest.fixture(scope="module")
def p1_test_server():
    """Starts a live in-memory AURA HTTP server for P1 integration tests."""
    cfg = Settings(
        aura_env="development",
        aura_server_host="127.0.0.1",
        aura_server_port=8945,
        aura_api_key_auth_enabled=False,
    )
    repo_container = create_in_memory_repositories()
    server = AURAHTTPServer(
        config=cfg,
        host="127.0.0.1",
        port=8945,
        repository_container=repo_container,
    )
    server.start(block=False)
    time.sleep(0.5)

    base_url = "http://127.0.0.1:8945"
    yield base_url, server

    server.stop()


class TestP1WebUIIntegration:
    """Integration test suite for P1 Web Product UI."""

    def test_serve_index_html_root_and_ui(self, p1_test_server):
        """Verify GET / and GET /ui serve index.html with security headers."""
        base_url, _ = p1_test_server

        for path in ("/", "/ui", "/index.html"):
            req = urllib.request.Request(f"{base_url}{path}")
            with urllib.request.urlopen(req) as resp:
                assert resp.status == 200
                assert "text/html" in resp.headers.get("Content-Type", "")
                assert resp.headers.get("X-Content-Type-Options") == "nosniff"
                assert resp.headers.get("X-Frame-Options") == "DENY"
                body = resp.read().decode("utf-8")
                assert "<title>AURA — Autonomous Personal Intelligence</title>" in body
                assert "data-tab=\"chat-view\"" in body
                assert "data-tab=\"runs-view\"" in body
                assert "data-tab=\"approvals-view\"" in body
                assert "data-tab=\"tasks-view\"" in body
                assert "data-tab=\"memory-view\"" in body
                assert "data-tab=\"files-view\"" in body
                assert "data-tab=\"devices-view\"" in body
                assert "data-tab=\"settings-view\"" in body

    def test_serve_css_and_javascript_assets(self, p1_test_server):
        """Verify GET /static/style.css and GET /static/app.js serve correctly."""
        base_url, _ = p1_test_server

        # Style CSS
        req_css = urllib.request.Request(f"{base_url}/static/style.css")
        with urllib.request.urlopen(req_css) as resp:
            assert resp.status == 200
            assert "text/css" in resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8")
            assert "--bg-main" in body

        # App JS
        req_js = urllib.request.Request(f"{base_url}/static/app.js")
        with urllib.request.urlopen(req_js) as resp:
            assert resp.status == 200
            assert "application/javascript" in resp.headers.get("Content-Type", "")
            body = resp.read().decode("utf-8")
            assert "PROJECT AURA — MODERN WEB PRODUCT CLIENT" in body

    def test_static_path_traversal_blocked(self, p1_test_server):
        """Verify path traversal attacks on static assets fail safely."""
        base_url, _ = p1_test_server

        traversal_paths = [
            "/static/../server.py",
            "/static/../../pyproject.toml",
            "/static/%2e%2e/server.py",
        ]

        for p in traversal_paths:
            req = urllib.request.Request(f"{base_url}{p}")
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                urllib.request.urlopen(req)
            assert exc_info.value.code in (HTTPStatus.NOT_FOUND, HTTPStatus.FORBIDDEN, HTTPStatus.BAD_REQUEST)

    def test_all_web_ui_api_endpoints_operational(self, p1_test_server):
        """Verify all REST APIs backing the 8 UI views respond cleanly."""
        base_url, _ = p1_test_server

        endpoints_to_test = [
            ("/health", "GET", None, 200),
            ("/ready", "GET", None, 200),
            ("/v1/agent/runs", "GET", None, 200),
            ("/v1/agent/mesh/roles", "GET", None, 200),
            ("/v1/approvals", "GET", None, 200),
            ("/v1/tasks", "GET", None, 200),
            ("/v1/automations", "GET", None, 200),
            ("/v1/cognitive-memory/query", "GET", None, 200),
            ("/v1/cognitive-memory/profile", "GET", None, 200),
            ("/v1/multimodal/artifacts", "GET", None, 200),
            ("/v1/devices", "GET", None, 200),
            ("/v1/preferences", "GET", None, 200),
        ]

        for path, method, payload, expected_status in endpoints_to_test:
            data_bytes = json.dumps(payload).encode("utf-8") if payload else None
            req = urllib.request.Request(
                f"{base_url}{path}",
                data=data_bytes,
                headers={"Content-Type": "application/json"} if payload else {},
                method=method,
            )
            with urllib.request.urlopen(req) as resp:
                assert resp.status == expected_status, f"Endpoint {method} {path} failed with status {resp.status}"
                body = json.loads(resp.read().decode("utf-8"))
                assert isinstance(body, dict), f"Endpoint {method} {path} did not return JSON object"
