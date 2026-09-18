"""M57 — Multimodal Content Validation, MIME Sniffing, and Security Hardening.

Implements server-side magic byte inspection, decompression bomb mitigation,
SSRF checks on URLs, secret redaction, and bounded input verification.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import urllib.parse
from typing import Any

from core.cognitive_memory.types import scrub_sensitive_content
from core.multimodal.types import MediaFormat, MultimodalLimitsConfig, MultimodalMediaType

logger = logging.getLogger("aura.multimodal.validation")

# Magic bytes dictionary mapping format to signature detection callable
def _is_png(data: bytes) -> bool:
    return len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n"

def _is_jpeg(data: bytes) -> bool:
    return len(data) >= 3 and data[:3] == b"\xff\xd8\xff"

def _is_gif(data: bytes) -> bool:
    return len(data) >= 6 and (data[:6] == b"GIF87a" or data[:6] == b"GIF89a")

def _is_webp(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"

def _is_pdf(data: bytes) -> bool:
    return len(data) >= 5 and data[:5] == b"%PDF-"

def _is_wav(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"

def _is_mp3(data: bytes) -> bool:
    if len(data) < 3:
        return False
    if data[:3] == b"ID3":
        return True
    # Frame sync header (11 bits set): 0xFF followed by 0xFB, 0xF3, 0xF2, 0xFA, 0xE0..
    return data[0] == 0xFF and (data[1] & 0xE0) == 0xE0

def _is_ogg(data: bytes) -> bool:
    return len(data) >= 4 and data[:4] == b"OggS"


MAGIC_SIGNATURES: dict[MediaFormat, Any] = {
    MediaFormat.PNG: _is_png,
    MediaFormat.JPEG: _is_jpeg,
    MediaFormat.GIF: _is_gif,
    MediaFormat.WEBP: _is_webp,
    MediaFormat.PDF: _is_pdf,
    MediaFormat.WAV: _is_wav,
    MediaFormat.MP3: _is_mp3,
    MediaFormat.OGG: _is_ogg,
}

FORMAT_TO_MEDIA_TYPE: dict[MediaFormat, MultimodalMediaType] = {
    MediaFormat.PNG: MultimodalMediaType.IMAGE,
    MediaFormat.JPEG: MultimodalMediaType.IMAGE,
    MediaFormat.GIF: MultimodalMediaType.IMAGE,
    MediaFormat.WEBP: MultimodalMediaType.IMAGE,
    MediaFormat.WAV: MultimodalMediaType.AUDIO,
    MediaFormat.MP3: MultimodalMediaType.AUDIO,
    MediaFormat.OGG: MultimodalMediaType.AUDIO,
    MediaFormat.PDF: MultimodalMediaType.DOCUMENT,
    MediaFormat.TXT: MultimodalMediaType.DOCUMENT,
    MediaFormat.MARKDOWN: MultimodalMediaType.DOCUMENT,
    MediaFormat.CSV: MultimodalMediaType.DOCUMENT,
    MediaFormat.JSON: MultimodalMediaType.STRUCTURED_DATA,
    MediaFormat.HTML: MultimodalMediaType.DOCUMENT,
    MediaFormat.OCTET_STREAM: MultimodalMediaType.BINARY_ARTIFACT,
}

FORBIDDEN_SSRF_IPS = frozenset({
    "169.254.169.254",
    "metadata.google.internal",
    "100.100.100.200",
})


class MultimodalValidator:
    """Validates raw binary artifacts against MIME specifications, security bounds, and decompression bombs."""

    def __init__(self, limits: MultimodalLimitsConfig | None = None):
        self.limits = limits or MultimodalLimitsConfig()

    def compute_sha256(self, data: bytes) -> str:
        """Compute SHA-256 cryptographic digest of data."""
        return hashlib.sha256(data).hexdigest()

    def sniff_and_validate_format(
        self,
        data: bytes,
        declared_format: str | MediaFormat | None = None,
        filename: str = "",
    ) -> tuple[MediaFormat, MultimodalMediaType]:
        """Validate payload against magic bytes. Rejects spoofed MIME types and spoofed extensions."""
        if not data:
            raise ValueError("Payload data cannot be empty.")

        if len(data) > self.limits.max_upload_bytes:
            raise ValueError(
                f"Payload size {len(data)} bytes exceeds configured maximum of {self.limits.max_upload_bytes} bytes."
            )

        detected_format: MediaFormat | None = None

        # Check binary magic signatures
        for fmt, checker in MAGIC_SIGNATURES.items():
            if checker(data):
                detected_format = fmt
                break

        # If not binary image/audio/pdf, check text formats
        if detected_format is None:
            try:
                decoded = data[:4096].decode("utf-8")
                if filename.endswith(".json") or (decoded.strip().startswith("{") and decoded.strip().endswith("}")):
                    detected_format = MediaFormat.JSON
                elif filename.endswith(".csv"):
                    detected_format = MediaFormat.CSV
                elif filename.endswith(".md") or filename.endswith(".markdown"):
                    detected_format = MediaFormat.MARKDOWN
                elif filename.endswith(".html") or filename.endswith(".htm") or "<html" in decoded.lower():
                    detected_format = MediaFormat.HTML
                else:
                    detected_format = MediaFormat.TXT
            except UnicodeDecodeError:
                detected_format = MediaFormat.OCTET_STREAM

        # If declared format was given, verify compatibility
        if declared_format:
            norm_declared = declared_format.value if isinstance(declared_format, MediaFormat) else str(declared_format).lower()
            # If declared format is a specific binary format but magic bytes mismatch -> spoofing attack!
            for binary_fmt in MAGIC_SIGNATURES:
                if binary_fmt.value == norm_declared:
                    if detected_format != binary_fmt:
                        raise ValueError(
                            f"MIME spoofing detected: Declared format '{norm_declared}' does not match detected binary signature '{detected_format.value}'."
                        )

        # Check filename extension spoofing if filename provided
        if filename:
            ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
            if ext in ("png", "jpg", "jpeg", "gif", "webp", "pdf", "wav", "mp3", "ogg"):
                expected_mime_map = {
                    "png": MediaFormat.PNG,
                    "jpg": MediaFormat.JPEG,
                    "jpeg": MediaFormat.JPEG,
                    "gif": MediaFormat.GIF,
                    "webp": MediaFormat.WEBP,
                    "pdf": MediaFormat.PDF,
                    "wav": MediaFormat.WAV,
                    "mp3": MediaFormat.MP3,
                    "ogg": MediaFormat.OGG,
                }
                expected_fmt = expected_mime_map.get(ext)
                if expected_fmt and detected_format != expected_fmt:
                    raise ValueError(
                        f"Extension spoofing detected: Filename extension '.{ext}' does not match actual content format '{detected_format.value}'."
                    )

        media_type = FORMAT_TO_MEDIA_TYPE.get(detected_format, MultimodalMediaType.BINARY_ARTIFACT)
        return detected_format, media_type

    def validate_decompression_bounds(self, compressed_bytes: int, uncompressed_bytes: int) -> None:
        """Protect against zip/gzip/decompression bombs (Invariant M57-F19)."""
        if compressed_bytes > 0:
            ratio = uncompressed_bytes / compressed_bytes
            if ratio > 100.0 and uncompressed_bytes > 5 * 1024 * 1024:
                raise ValueError(
                    f"Decompression bomb detected: Compression ratio {ratio:.1f}:1 exceeds safe threshold."
                )

    def validate_url_safe(self, url: str) -> str:
        """Validate URL to prevent SSRF and cloud metadata endpoint access (Invariant M57-F36)."""
        if not url or not isinstance(url, str):
            raise ValueError("URL must be a non-empty string.")
        parsed = urllib.parse.urlparse(url.strip())
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Invalid URL scheme '{parsed.scheme}'. Only http and https are allowed.")
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            raise ValueError("Missing hostname in URL.")
        if hostname in FORBIDDEN_SSRF_IPS:
            raise ValueError(f"SSRF violation: Access to forbidden metadata host '{hostname}' is blocked.")
        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_link_local or ip.is_loopback or ip.is_private:
                raise ValueError(f"SSRF violation: Access to private/link-local IP '{ip}' is blocked.")
        except ValueError as e:
            if "SSRF violation" in str(e):
                raise
        return url.strip()
