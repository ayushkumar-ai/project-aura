"""Security and Sensitive Information Scrubbing Utilities.

Provides deterministic sanitization and redacting of:
- API keys (OpenAI keys, bearer tokens, generic secrets)
- Authorization headers
- Passwords and credential fields in dictionaries and strings
"""

from __future__ import annotations

import re
from typing import Any

# Sensitive key names in dictionaries to mask
SENSITIVE_KEY_PATTERNS = {
    "api_key",
    "apikey",
    "secret",
    "token",
    "password",
    "authorization",
    "auth_token",
    "bearer",
    "credentials",
    "private_key",
    "access_token",
}

# Regex pattern matching common API key patterns (e.g. sk-..., bearer tokens, 32+ hex/base64 strings)
API_KEY_REGEX = re.compile(
    r"(sk-[a-zA-Z0-9_\-]{20,})|(Bearer\s+[a-zA-Z0-9_\-\.]{16,})|(key-[a-zA-Z0-9_\-]{16,})",
    re.IGNORECASE,
)

REDACTED_STR = "[REDACTED]"


def scrub_string(text: str) -> str:
    """Scrub sensitive patterns such as API keys and tokens from a text string."""
    if not isinstance(text, str) or not text:
        return text

    # Redact known API key formats
    scrubbed = API_KEY_REGEX.sub(lambda m: f"{m.group(0)[:6]}...{REDACTED_STR}", text)
    return scrubbed


def scrub_dict(data: dict[str, Any], max_depth: int = 5) -> dict[str, Any]:
    """Recursively redact sensitive key-values from a dictionary."""
    if not isinstance(data, dict) or max_depth <= 0:
        return data

    scrubbed: dict[str, Any] = {}
    for k, v in data.items():
        k_lower = str(k).lower()
        if any(pat in k_lower for pat in SENSITIVE_KEY_PATTERNS):
            scrubbed[k] = REDACTED_STR
        elif isinstance(v, dict):
            scrubbed[k] = scrub_dict(v, max_depth=max_depth - 1)
        elif isinstance(v, list):
            scrubbed[k] = [
                scrub_dict(item, max_depth=max_depth - 1) if isinstance(item, dict)
                else (scrub_string(item) if isinstance(item, str) else item)
                for item in v
            ]
        elif isinstance(v, tuple):
            scrubbed[k] = tuple(
                scrub_dict(item, max_depth=max_depth - 1) if isinstance(item, dict)
                else (scrub_string(item) if isinstance(item, str) else item)
                for item in v
            )
        elif isinstance(v, str):
            scrubbed[k] = scrub_string(v)
        else:
            scrubbed[k] = v

    return scrubbed


def sanitize_error_message(err: Exception | str, is_production: bool = False) -> str:
    """Sanitize error messages to prevent secret or traceback leaks in production."""
    err_str = str(err)
    scrubbed = scrub_string(err_str)
    if is_production:
        # Avoid detailed internal tracebacks in production API responses
        if "Traceback (most recent call last)" in scrubbed:
            return "Internal server error occurred."
    return scrubbed
