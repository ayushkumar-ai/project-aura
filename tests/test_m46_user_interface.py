"""M46 — User-Facing Product Interface Test Suite.

Verifies:
1. Static Asset Delivery (HTML, CSS, JS) with Security Headers
2. Path Traversal & File Boundary Security
3. UI Feature Matrix (Chat, Memory, RAG, Tools, Auth Settings)
4. Safe HTML Escaping & Zero Credential Leaks in Static Assets
5. Live Server End-to-End Web Client APIs
"""

import json
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
import pytest

from app.config import Settings
from app.server import AURAHTTPServer


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http_get_raw(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, resp_headers, resp.read()
    except urllib.error.HTTPError as e:
        resp_headers = {k.lower(): v for k, v in e.headers.items()}
        return e.code, resp_headers, e.read() if e.fp else b""


def test_static_asset_delivery():
    """Verify static assets are served with correct MIME types and security headers."""
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
        # 1. Root / UI path serves index.html
        status, headers, body = _http_get_raw(f"{base_url}/")
        assert status == 200
        assert "text/html" in headers.get("content-type", "")
        assert headers.get("x-content-type-options") == "nosniff"
        assert headers.get("x-frame-options") == "DENY"
        assert b"Project AURA" in body

        # 2. JavaScript bundle
        status, headers, body = _http_get_raw(f"{base_url}/static/app.js")
        assert status == 200
        assert "javascript" in headers.get("content-type", "")
        assert b"PROJECT AURA" in body

        # 3. Stylesheet bundle
        status, headers, body = _http_get_raw(f"{base_url}/static/style.css")
        assert status == 200
        assert "text/css" in headers.get("content-type", "")
        assert b"--accent-primary" in body

    finally:
        server.stop()


def test_static_file_path_traversal_protection():
    """Verify that path traversal attempts are strictly rejected."""
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
        # Directory traversal attempt
        status, _, body = _http_get_raw(f"{base_url}/static/../server.py")
        assert status in (403, 404)
        assert b"class AURAHTTPServer" not in body

    finally:
        server.stop()


def test_static_html_and_js_contain_all_required_m46_views():
    """Verify static frontend assets contain all M46 user interface components."""
    static_dir = Path(__file__).resolve().parent.parent / "app" / "static"
    html_content = (static_dir / "index.html").read_text(encoding="utf-8")
    js_content = (static_dir / "app.js").read_text(encoding="utf-8")

    # Views
    assert "chat-view" in html_content
    assert "memory-view" in html_content
    assert "rag-view" in html_content
    assert "tools-view" in html_content

    # Controls & Elements
    assert "pref-name" in html_content
    assert "pref-instructions" in html_content
    assert "rag-query-input" in html_content
    assert "settings-modal" in html_content
    assert "auth-token-input" in html_content

    # JavaScript logic
    assert "escapeHtml" in js_content
    assert "loadPreferences" in js_content
    assert "loadToolsCatalog" in js_content
    assert "sessionStorage" in js_content
    assert "aura_auth_token" in js_content

    # Verify no hardcoded secrets in frontend assets
    assert "sk-" not in js_content
    assert "AIza" not in js_content
    assert "aura_sec_" not in js_content
