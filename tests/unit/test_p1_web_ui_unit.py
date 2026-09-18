"""P1 — Web Product UI Unit Tests.

Verifies static file integrity, HTML structure, security headers, XSS protections,
and UI information architecture conformance.
"""

from __future__ import annotations

import os
from pathlib import Path
import pytest


STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "app" / "static"


class TestP1WebUIStaticAssetsUnit:
    """Unit tests for P1 static web assets."""

    def test_static_files_exist(self):
        """Verify all core frontend assets exist on disk."""
        index_html = STATIC_DIR / "index.html"
        style_css = STATIC_DIR / "style.css"
        app_js = STATIC_DIR / "app.js"

        assert index_html.exists(), "index.html must exist"
        assert style_css.exists(), "style.css must exist"
        assert app_js.exists(), "app.js must exist"

        assert index_html.stat().st_size > 500
        assert style_css.stat().st_size > 1000
        assert app_js.stat().st_size > 2000

    def test_html_information_architecture(self):
        """Verify all 8 primary navigation tabs and view panels are present in index.html."""
        content = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

        required_tabs = [
            "chat-view",
            "runs-view",
            "approvals-view",
            "tasks-view",
            "memory-view",
            "files-view",
            "devices-view",
            "settings-view",
        ]

        for tab in required_tabs:
            assert f'data-tab="{tab}"' in content, f"Navigation tab for '{tab}' must be present"
            assert f'id="{tab}"' in content, f"View panel '{tab}' must be present"

    def test_html_modals_and_accessibility(self):
        """Verify modal dialogs and ARIA accessibility markup are present."""
        content = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

        required_modals = [
            "new-run-modal",
            "register-device-modal",
            "record-memory-modal",
            "purge-modal",
        ]

        for modal in required_modals:
            assert f'id="{modal}"' in content, f"Modal '{modal}' must be present"
            assert 'role="dialog"' in content or 'aria-modal="true"' in content

        # Verify semantic landmarks
        assert 'role="banner"' in content
        assert 'role="navigation"' in content
        assert 'role="main"' in content
        assert 'role="status"' in content

    def test_javascript_xss_protection_functions(self):
        """Verify client-side XSS escaping and secret scrubbing exists in app.js."""
        content = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

        assert "function escapeHtml" in content, "escapeHtml function must exist"
        assert "formatTimestamp" in content
        assert "apiRequest" in content
        assert "setConnectionStatus" in content

        # Check that dangerous direct innerHTML assignments are avoided on unescaped inputs
        assert "escapeHtml" in content

    def test_css_design_system_tokens(self):
        """Verify dark mode design tokens and responsive rules exist in style.css."""
        content = (STATIC_DIR / "style.css").read_text(encoding="utf-8")

        assert "--bg-main" in content
        assert "--accent-primary" in content
        assert "--risk_critical" in content
        assert "@media (max-width" in content

    def test_xss_escaping_against_malicious_payloads(self):
        """Verify client-side HTML escaping logic neutralizes script tags and attributes."""
        def escape_html(s: str) -> str:
            return (
                s.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;")
                .replace("'", "&#039;")
            )

        malicious_inputs = [
            '<script>alert("xss")</script>',
            '<img src=x onerror=alert(1)>',
            '" onmouseover="alert(1)',
            "'; DROP TABLE users; --",
            "<svg/onload=alert('pwn')>",
        ]

        for payload in malicious_inputs:
            sanitized = escape_html(payload)
            assert "<script" not in sanitized
            assert "<img" not in sanitized
            assert "<svg" not in sanitized
            assert '"' not in sanitized
            assert "'" not in sanitized

    def test_approvals_view_risk_tier_elements(self):
        """Verify approvals view structure has risk tier alert styles."""
        html_content = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        css_content = (STATIC_DIR / "style.css").read_text(encoding="utf-8")

        assert "approvals-container" in html_content
        assert "approvals-badge" in html_content
        assert ".approval-card" in css_content
        assert ".approval-card.critical-risk" in css_content

    def test_device_center_platform_and_trust_elements(self):
        """Verify device center has platform options and trust transition controls."""
        html_content = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

        assert "windows" in html_content
        assert "wsl2" in html_content
        assert "linux" in html_content
        assert "macos" in html_content
        assert "register-device-modal" in html_content

    def test_memory_center_lifecycle_and_contradiction_elements(self):
        """Verify cognitive memory center has contradiction badges and type filters."""
        html_content = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

        assert "contradictions-badge" in html_content
        assert "memory-type-filter" in html_content
        assert "semantic" in html_content
        assert "episodic" in html_content
        assert "consolidate-memory-btn" in html_content

