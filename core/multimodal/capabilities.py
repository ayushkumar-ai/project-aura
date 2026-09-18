"""M57 — Multimodal Capability Registry & Resolution.

Manages multimodal processing capabilities (image understanding, audio transcription,
document extraction, OCR, structured data analysis) with deterministic resolution.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from core.multimodal.types import MediaFormat, MultimodalMediaType

logger = logging.getLogger("aura.multimodal.capabilities")


@dataclass
class MultimodalCapability:
    """Descriptor for a supported multimodal capability."""
    capability_id: str
    title: str
    description: str
    supported_media_types: list[MultimodalMediaType]
    supported_formats: list[MediaFormat | str]
    operations: list[str]
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def supports(self, media_type: MultimodalMediaType, format_val: MediaFormat | str, operation: str) -> bool:
        """Check if this capability supports the requested media type, format, and operation."""
        if not self.enabled:
            return False
        if media_type not in self.supported_media_types:
            return False
        if operation not in self.operations:
            return False
        # If wildcard or specific format matches
        if not self.supported_formats:
            return True
        norm_fmt = format_val.value if isinstance(format_val, MediaFormat) else str(format_val).lower()
        for f in self.supported_formats:
            fmt_str = f.value if isinstance(f, MediaFormat) else str(f).lower()
            if fmt_str == norm_fmt:
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "title": self.title,
            "description": self.description,
            "supported_media_types": [m.value for m in self.supported_media_types],
            "supported_formats": [
                (f.value if isinstance(f, MediaFormat) else str(f)) for f in self.supported_formats
            ],
            "operations": list(self.operations),
            "enabled": self.enabled,
            "metadata": dict(self.metadata),
        }


class MultimodalCapabilityRegistry:
    """Thread-safe registry for multimodal processing capabilities."""

    def __init__(self, register_defaults: bool = True):
        self._lock = threading.RLock()
        self._capabilities: dict[str, MultimodalCapability] = {}
        if register_defaults:
            self._register_default_capabilities()

    def _register_default_capabilities(self) -> None:
        # 1. Image Understanding
        self.register(
            MultimodalCapability(
                capability_id="image_understanding",
                title="Visual Reasoning & Image Understanding",
                description="Analyzes images, visual layouts, scenes, charts, and objects.",
                supported_media_types=[MultimodalMediaType.IMAGE],
                supported_formats=[MediaFormat.PNG, MediaFormat.JPEG, MediaFormat.WEBP, MediaFormat.GIF],
                operations=["understand", "describe", "analyze", "detect_objects"],
            )
        )
        # 2. OCR / Text Extraction
        self.register(
            MultimodalCapability(
                capability_id="ocr_text_extraction",
                title="Optical Character Recognition (OCR)",
                description="Extracts printed or handwritten text and bounding boxes from images.",
                supported_media_types=[MultimodalMediaType.IMAGE, MultimodalMediaType.DOCUMENT],
                supported_formats=[MediaFormat.PNG, MediaFormat.JPEG, MediaFormat.PDF],
                operations=["ocr", "extract_text"],
            )
        )
        # 3. Audio Transcription
        self.register(
            MultimodalCapability(
                capability_id="audio_transcription",
                title="Speech-to-Text & Audio Transcription",
                description="Transcribes spoken audio into structured text with timestamps.",
                supported_media_types=[MultimodalMediaType.AUDIO],
                supported_formats=[MediaFormat.WAV, MediaFormat.MP3, MediaFormat.OGG],
                operations=["transcribe", "speech_to_text"],
            )
        )
        # 4. Document Extraction
        self.register(
            MultimodalCapability(
                capability_id="document_extraction",
                title="Document Parsing & Information Extraction",
                description="Extracts structured text, tables, and sections from documents.",
                supported_media_types=[MultimodalMediaType.DOCUMENT],
                supported_formats=[MediaFormat.PDF, MediaFormat.TXT, MediaFormat.MARKDOWN, MediaFormat.CSV, MediaFormat.HTML],
                operations=["extract_document", "summarize", "parse_tables"],
            )
        )
        # 5. Structured Data Analysis
        self.register(
            MultimodalCapability(
                capability_id="structured_analysis",
                title="Structured Data & JSON Reasoning",
                description="Analyzes and validates structured tabular data, schemas, and JSON.",
                supported_media_types=[MultimodalMediaType.STRUCTURED_DATA],
                supported_formats=[MediaFormat.JSON, MediaFormat.CSV],
                operations=["analyze_data", "validate_schema"],
            )
        )

    def register(self, capability: MultimodalCapability) -> None:
        with self._lock:
            self._capabilities[capability.capability_id] = capability

    def get(self, capability_id: str) -> MultimodalCapability | None:
        with self._lock:
            return self._capabilities.get(capability_id)

    def list_capabilities(self) -> list[MultimodalCapability]:
        with self._lock:
            return sorted(self._capabilities.values(), key=lambda c: c.capability_id)

    def resolve(
        self,
        media_type: MultimodalMediaType,
        format_val: MediaFormat | str,
        operation: str = "understand",
    ) -> MultimodalCapability:
        """Deterministically resolve matching capability. Fails closed if not found (Invariant M57-F11)."""
        with self._lock:
            for cap in self._capabilities.values():
                if cap.supports(media_type, format_val, operation):
                    return cap
            raise ValueError(
                f"No enabled capability found supporting media_type='{media_type.value if isinstance(media_type, MultimodalMediaType) else media_type}', "
                f"format='{format_val.value if isinstance(format_val, MediaFormat) else format_val}', operation='{operation}'."
            )
