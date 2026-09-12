"""M37 — Multimodal Foundation Engine for Project AURA.

Provides modality validation, format detection, mock adapters, and unified processing
for text, image, and audio inputs.
"""

from __future__ import annotations

import abc
import logging
import struct
import threading
import time
from typing import Any
from uuid import uuid4

from core.multimodal_types import (
    AudioFormat,
    ImageFormat,
    ModalityContentBlock,
    ModalityType,
    MultimodalAnalysisResult,
    MultimodalRequest,
)

logger = logging.getLogger("aura.multimodal")


class BaseMultimodalAdapter(abc.ABC):
    """Abstract interface for external or local multimodal model backends."""

    @abc.abstractmethod
    def analyze_image(self, image_bytes: bytes, mime_type: str) -> dict[str, Any]:
        ...

    @abc.abstractmethod
    def transcribe_audio(self, audio_bytes: bytes, mime_type: str) -> str:
        ...


class MockMultimodalAdapter(BaseMultimodalAdapter):
    """Deterministic reference adapter for image analysis and speech transcription."""

    def analyze_image(self, image_bytes: bytes, mime_type: str) -> dict[str, Any]:
        fmt = self.detect_image_format(image_bytes)
        desc = f"Simulated analysis of {fmt.value.upper()} image ({len(image_bytes)} bytes)."
        extracted_text = "Sample OCR Text: Project AURA Architecture Overview"
        return {
            "format": fmt.value,
            "byte_size": len(image_bytes),
            "description": desc,
            "detected_objects": ["diagram", "text_block", "chart"],
            "ocr_text": extracted_text,
        }

    def transcribe_audio(self, audio_bytes: bytes, mime_type: str) -> str:
        fmt = self.detect_audio_format(audio_bytes)
        return f"Simulated transcription of {fmt.value.upper()} audio: 'Hello AURA, start daily briefing.'"

    @staticmethod
    def detect_image_format(raw_bytes: bytes) -> ImageFormat:
        if not raw_bytes or len(raw_bytes) < 4:
            return ImageFormat.UNKNOWN
        if raw_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
            return ImageFormat.PNG
        if raw_bytes.startswith(b"\xff\xd8\xff"):
            return ImageFormat.JPEG
        if raw_bytes.startswith(b"GIF87a") or raw_bytes.startswith(b"GIF89a"):
            return ImageFormat.GIF
        if len(raw_bytes) >= 12 and raw_bytes.startswith(b"RIFF") and raw_bytes[8:12] == b"WEBP":
            return ImageFormat.WEBP
        if b"<svg" in raw_bytes[:100].lower():
            return ImageFormat.SVG
        return ImageFormat.UNKNOWN

    @staticmethod
    def detect_audio_format(raw_bytes: bytes) -> AudioFormat:
        if not raw_bytes or len(raw_bytes) < 4:
            return AudioFormat.UNKNOWN
        if len(raw_bytes) >= 12 and raw_bytes.startswith(b"RIFF") and raw_bytes[8:12] == b"WAVE":
            return AudioFormat.WAV
        if raw_bytes.startswith(b"ID3") or raw_bytes.startswith(b"\xff\xfb"):
            return AudioFormat.MP3
        if raw_bytes.startswith(b"OggS"):
            return AudioFormat.OGG
        if raw_bytes.startswith(b"fLaC"):
            return AudioFormat.FLAC
        return AudioFormat.UNKNOWN


class MultimodalProcessor:
    """Unified processor for multimodal requests across text, image, and audio."""

    def __init__(self, adapter: BaseMultimodalAdapter | None = None):
        self.adapter = adapter or MockMultimodalAdapter()
        self._lock = threading.RLock()

    def ingest_text(self, text: str, filename: str = "") -> ModalityContentBlock:
        return ModalityContentBlock(
            block_id=f"mod_txt_{uuid4().hex[:8]}",
            modality=ModalityType.TEXT,
            text_content=text,
            mime_type="text/plain",
            filename=filename,
        )

    def ingest_image(
        self,
        raw_bytes: bytes,
        filename: str = "",
        mime_type: str = "image/png",
    ) -> ModalityContentBlock:
        return ModalityContentBlock(
            block_id=f"mod_img_{uuid4().hex[:8]}",
            modality=ModalityType.IMAGE,
            raw_bytes=raw_bytes,
            mime_type=mime_type,
            filename=filename,
        )

    def ingest_audio(
        self,
        raw_bytes: bytes,
        filename: str = "",
        mime_type: str = "audio/wav",
    ) -> ModalityContentBlock:
        return ModalityContentBlock(
            block_id=f"mod_aud_{uuid4().hex[:8]}",
            modality=ModalityType.AUDIO,
            raw_bytes=raw_bytes,
            mime_type=mime_type,
            filename=filename,
        )

    def process_request(self, request: MultimodalRequest) -> MultimodalAnalysisResult:
        """Process all modality blocks and build an integrated multimodal result."""
        with self._lock:
            detected_modalities: set[str] = set()
            extracted_text_parts: list[str] = []
            audio_transcripts: list[str] = []
            image_meta_list: list[dict[str, Any]] = []
            audio_meta_list: list[dict[str, Any]] = []

            if request.prompt:
                detected_modalities.add("text")
                extracted_text_parts.append(request.prompt)

            for block in request.blocks:
                detected_modalities.add(block.modality.value)

                if block.modality == ModalityType.TEXT:
                    if block.text_content:
                        extracted_text_parts.append(block.text_content)

                elif block.modality == ModalityType.IMAGE:
                    if block.raw_bytes:
                        meta = self.adapter.analyze_image(block.raw_bytes, block.mime_type)
                        meta["block_id"] = block.block_id
                        meta["filename"] = block.filename
                        image_meta_list.append(meta)
                        if meta.get("ocr_text"):
                            extracted_text_parts.append(f"[Image OCR: {meta['ocr_text']}]")

                elif block.modality == ModalityType.AUDIO:
                    if block.raw_bytes:
                        transcript = self.adapter.transcribe_audio(block.raw_bytes, block.mime_type)
                        audio_transcripts.append(transcript)
                        audio_meta_list.append({
                            "block_id": block.block_id,
                            "filename": block.filename,
                            "byte_size": len(block.raw_bytes),
                            "transcript": transcript,
                        })

            all_extracted_text = "\n".join(extracted_text_parts).strip()
            all_transcripts = "\n".join(audio_transcripts).strip()

            summary = (
                f"Multimodal input processed with {len(request.blocks)} blocks across "
                f"{', '.join(sorted(detected_modalities))}."
            )

            return MultimodalAnalysisResult(
                request_id=request.request_id,
                summary=summary,
                detected_modalities=sorted(detected_modalities),
                extracted_text=all_extracted_text,
                audio_transcription=all_transcripts,
                image_metadata=image_meta_list,
                audio_metadata=audio_meta_list,
                confidence=0.98,
                created_at=time.time(),
            )
