"""M58 — Platform Security, Path Normalization & Command Sandboxing.

Enforces fail-closed path traversal prevention, safe parameter validation,
command allowlisting, SSRF protection on network endpoints, and secret scrubbing.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any

from core.cognitive_memory.types import scrub_sensitive_content

logger = logging.getLogger("aura.platform.security")

# Allowed read-only and sandboxed capabilities
ALLOWLISTED_DESKTOP_CAPABILITIES = frozenset({
    "get_system_info",
    "get_clock",
    "get_battery_status",
    "get_network_status",
    "read_sandboxed_file",
    "write_sandboxed_file",
    "delete_sandboxed_file",
    "list_sandboxed_directory",
    "take_screenshot",
    "send_notification",
    "clipboard_read",
    "clipboard_write",
})

FORBIDDEN_RAW_SHELL_PATTERNS = [
    re.compile(r"(?i)\b(?:powershell|pwsh|cmd\.exe|bash|sh|zsh|eval|exec)\b"),
    re.compile(r"(?i)\b(?:rm\s+-rf|del\s+/f|format\s+[a-z]:|mkfs)\b"),
    re.compile(r"(?i)[;&|`$]"),  # Shell chaining metacharacters
]

FORBIDDEN_METADATA_HOSTS = frozenset({
    "169.254.169.254",
    "metadata.google.internal",
    "100.100.100.200",
})


class PlatformSecurityManager:
    """Validates and enforces security boundaries on platform interactions."""

    def __init__(self, sandbox_root: str = "data/platform_sandbox"):
        self.sandbox_root = Path(sandbox_root).resolve()
        self.sandbox_root.mkdir(parents=True, exist_ok=True)

    def sanitize_path(self, tenant_id: str, relative_path: str) -> Path:
        """Resolve and validate a relative path inside a tenant's sandboxed directory.
        
        Enforces Invariant M58-F13 (Path Traversal Protection).
        """
        if not relative_path or not isinstance(relative_path, str):
            raise ValueError("File path must be a non-empty string.")

        raw = relative_path.strip()

        # Reject obvious path traversal patterns
        if ".." in raw or raw.startswith("/") or raw.startswith("\\") or ":" in raw:
            raise PermissionError(f"Path traversal detected: '{relative_path}'")

        # Sanitize tenant directory
        clean_tenant = "".join(c for c in tenant_id if c.isalnum() or c in ("-", "_"))
        if not clean_tenant:
            clean_tenant = "default"

        tenant_sandbox = (self.sandbox_root / clean_tenant).resolve()
        tenant_sandbox.mkdir(parents=True, exist_ok=True)

        target = (tenant_sandbox / raw).resolve()

        # Enforce sandbox containment invariant
        if not str(target).startswith(str(tenant_sandbox)):
            raise PermissionError(f"Target path '{target}' escapes tenant sandbox '{tenant_sandbox}'.")

        return target

    def validate_capability_name(self, capability_name: str) -> str:
        """Verify capability name is well-formed and recognized."""
        clean = str(capability_name).strip().lower()
        if not clean or not re.match(r"^[a-z0-9_]{1,64}$", clean):
            raise ValueError(f"Invalid capability name: '{capability_name}'")
        return clean

    def validate_command_safety(self, command_name: str, args: list[str] | None = None) -> None:
        """Enforce Invariant M58-F11 & M58-F12 (Command Boundary and Shell Safety)."""
        clean_cmd = self.validate_capability_name(command_name)
        if clean_cmd not in ALLOWLISTED_DESKTOP_CAPABILITIES:
            raise PermissionError(f"Capability '{command_name}' is not in allowlisted platform capabilities.")

        # Check arguments for shell injection metacharacters
        if args:
            for arg in args:
                if not isinstance(arg, str):
                    continue
                for pat in FORBIDDEN_RAW_SHELL_PATTERNS:
                    if pat.search(arg):
                        raise PermissionError(f"Potential command injection detected in argument: '{arg}'")

    def validate_endpoint_url(self, url: str) -> str:
        """Validate network URLs to prevent SSRF (Invariant M58-F14)."""
        if not url:
            raise ValueError("URL cannot be empty.")
        parsed = urllib.parse.urlparse(url.strip())
        if parsed.scheme not in ("http", "https", "ws", "wss"):
            raise ValueError(f"Invalid transport scheme '{parsed.scheme}'.")
        hostname = (parsed.hostname or "").lower()
        if hostname in FORBIDDEN_METADATA_HOSTS:
            raise PermissionError(f"SSRF violation: Access to forbidden metadata host '{hostname}' is blocked.")
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_loopback or ip.is_private or ip.is_link_local:
                raise PermissionError(f"SSRF violation: Access to private/link-local address '{ip}' is blocked.")
        except ValueError:
            pass
        return url.strip()

    def scrub_output(self, data: Any) -> Any:
        """Recursively scrub secrets from output dictionaries or strings (Invariant M58-F29)."""
        if isinstance(data, str):
            return scrub_sensitive_content(data)
        elif isinstance(data, dict):
            return {k: self.scrub_output(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self.scrub_output(item) for item in data]
        return data
