"""M57 — Multimodal Prompt-Injection Defense, Trust Hierarchy & Data Envelopes.

Enforces strict authority boundaries:
SYSTEM_DEVELOPER_POLICY (4) > AUTHORIZED_USER_INSTRUCTION (3) > TOOL_SYSTEM_OBSERVATION (2) > EXTERNAL_MULTIMODAL_DATA (1)
All multimodal-extracted text is wrapped in data envelopes and barred from direct tool execution.
"""

from __future__ import annotations

import html
import logging
import re
from enum import IntEnum
from typing import Any

from core.multimodal.types import MultimodalProvenance

logger = logging.getLogger("aura.multimodal.trust")


class AuthorityRank(IntEnum):
    """Explicit authority hierarchy for Project AURA execution."""
    EXTERNAL_MULTIMODAL_DATA = 1
    TOOL_SYSTEM_OBSERVATION = 2
    AUTHORIZED_USER_INSTRUCTION = 3
    SYSTEM_DEVELOPER_POLICY = 4


# Common prompt-injection heuristics targeting multimodal text/OCR
_PROMPT_INJECTION_PATTERNS = [
    re.compile(r"(?i)\bignore\s+(?:all\s+)?previous\s+instructions\b"),
    re.compile(r"(?i)\bsystem\s+override\b"),
    re.compile(r"(?i)\byou\s+are\s+now\s+in\s+developer\s+mode\b"),
    re.compile(r"(?i)\bdisregard\s+(?:all\s+)?(?:prior|previous)\s+(?:rules|instructions)\b"),
    re.compile(r"(?i)\bprint\s+(?:the\s+)?(?:secret|api_key|password|token)\b"),
    re.compile(r"(?i)\bdelete\s+(?:all\s+)?(?:files|database|records|tables)\b"),
    re.compile(r"(?i)\bexecute\s+(?:the\s+following\s+)?(?:command|tool|script)\b"),
]


def wrap_untrusted_multimodal_data(
    content: str,
    artifact_id: str,
    media_type: str,
    provenance: MultimodalProvenance | str = MultimodalProvenance.USER_UPLOAD,
    trust_level: str = "data_only",
) -> str:
    """Enclose extracted multimodal content in an explicit XML data envelope (Invariant M57-F16)."""
    escaped_content = html.escape(content or "")
    prov_str = provenance.value if isinstance(provenance, MultimodalProvenance) else str(provenance)
    return (
        f'<UNTRUSTED_MULTIMODAL_DATA artifact_id="{artifact_id}" '
        f'media_type="{media_type}" provenance="{prov_str}" trust_level="{trust_level}">\n'
        f'{escaped_content}\n'
        f'</UNTRUSTED_MULTIMODAL_DATA>'
    )


def sanitize_and_check_injection(text: str) -> tuple[str, bool]:
    """Inspect text for embedded prompt injection patterns.
    
    Returns (cleaned_text, injection_detected).
    """
    if not text:
        return "", False
    detected = False
    for pat in _PROMPT_INJECTION_PATTERNS:
        if pat.search(text):
            detected = True
            logger.warning(f"Prompt injection pattern detected in multimodal content: {pat.pattern}")
            break
    return text, detected


def validate_tool_dispatch_authority(
    provenance: MultimodalProvenance | str,
    user_authorized: bool = False,
) -> bool:
    """Verify if multimodal observation is allowed to directly trigger tool execution.
    
    Invariant M57-F20: Multimodal content is strictly DATA and CANNOT directly invoke tools.
    """
    # Multimodal observation alone NEVER authorizes tool execution
    if not user_authorized:
        return False
    return True
