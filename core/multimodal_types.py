"""M37 — Multimodal Foundation Types.

Defines modality-neutral representations, content blocks, formats, and analysis results
for text, image, and audio inputs.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ModalityType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"


class ImageFormat(str, Enum):
    PNG = "png"
    JPEG = "jpeg"
    WEBP = "webp"
    SVG = "svg"
    GIF = "gif"
    UNKNOWN = "unknown"


class AudioFormat(str, Enum):
    WAV = "wav"
    MP3 = "mp3"
    OGG = "ogg"
    FLAC = "flac"
    UNKNOWN = "unknown"


@dataclass
class ModalityContentBlock:
    block_id: str
    modality: ModalityType
    text_content: str = ""
    raw_bytes: bytes | None = None
    mime_type: str = "text/plain"
    filename: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "modality": self.modality.value,
            "text_content": self.text_content,
            "has_raw_bytes": self.raw_bytes is not None,
            "byte_size": len(self.raw_bytes) if self.raw_bytes else 0,
            "mime_type": self.mime_type,
            "filename": self.filename,
            "metadata": self.metadata,
        }


@dataclass
class MultimodalRequest:
    request_id: str
    prompt: str = ""
    blocks: list[ModalityContentBlock] = field(default_factory=list)
    user_id: str = "user"
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "prompt": self.prompt,
            "blocks": [b.to_dict() for b in self.blocks],
            "user_id": self.user_id,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


@dataclass
class MultimodalAnalysisResult:
    request_id: str
    summary: str
    detected_modalities: list[str] = field(default_factory=list)
    extracted_text: str = ""
    audio_transcription: str = ""
    image_metadata: list[dict[str, Any]] = field(default_factory=list)
    audio_metadata: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 1.0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
