"""M57 — Multimodal Processing & Rich Interaction Domain Models and Types.

Defines supported media types, formats, lifecycle state machine, security classifications,
provenance categories, artifact metadata, processing jobs, structured results,
and configurable resource limits.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from core.cognitive_memory.types import scrub_sensitive_content


class MultimodalMediaType(str, Enum):
    """Core supported multimodal media types."""
    IMAGE = "image"
    AUDIO = "audio"
    DOCUMENT = "document"
    STRUCTURED_DATA = "structured_data"
    BINARY_ARTIFACT = "binary_artifact"


class MediaFormat(str, Enum):
    """Supported multimodal MIME and file formats."""
    # Images
    PNG = "image/png"
    JPEG = "image/jpeg"
    WEBP = "image/webp"
    GIF = "image/gif"
    # Audio
    WAV = "audio/wav"
    MP3 = "audio/mpeg"
    OGG = "audio/ogg"
    # Documents
    PDF = "application/pdf"
    TXT = "text/plain"
    MARKDOWN = "text/markdown"
    CSV = "text/csv"
    JSON = "application/json"
    HTML = "text/html"
    OCTET_STREAM = "application/octet-stream"


class ArtifactLifecycleState(str, Enum):
    """Multimodal artifact lifecycle state machine."""
    UPLOADED = "uploaded"
    VALIDATING = "validating"
    ACCEPTED = "accepted"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"
    QUARANTINED = "quarantined"
    EXPIRED = "expired"
    DELETED = "deleted"


class SecurityClassification(str, Enum):
    """Data sensitivity and access boundary classification."""
    UNRESTRICTED = "unrestricted"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"
    QUARANTINED = "quarantined"


class MultimodalProvenance(str, Enum):
    """Origin and authority provenance for multimodal artifacts."""
    USER_UPLOAD = "user_upload"
    SYSTEM_GENERATED = "system_generated"
    TOOL_OUTPUT = "tool_output"
    EXTERNAL_FETCH = "external_fetch"
    DERIVED = "derived"


class JobStatus(str, Enum):
    """Processing job execution state."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class MultimodalLimitsConfig:
    """Configurable resource and security envelopes for multimodal processing."""
    max_upload_bytes: int = 25 * 1024 * 1024  # 25 MB
    max_image_dimension: int = 4096            # 4096 x 4096 px
    max_audio_duration_seconds: float = 300.0  # 5 minutes
    max_document_pages: int = 100
    max_extracted_text_chars: int = 100_000
    max_processing_timeout_seconds: float = 60.0
    max_retries: int = 3
    quarantine_malicious: bool = True


@dataclass
class MultimodalArtifact:
    """Authoritative domain record for an ingested multimodal artifact."""
    artifact_id: str = field(default_factory=lambda: f"art_{uuid4().hex[:16]}")
    tenant_id: str = "default"
    media_type: MultimodalMediaType = MultimodalMediaType.IMAGE
    format: MediaFormat | str = MediaFormat.PNG
    size_bytes: int = 0
    checksum_sha256: str = ""
    storage_uri: str = ""
    lifecycle_state: ArtifactLifecycleState = ArtifactLifecycleState.UPLOADED
    provenance: MultimodalProvenance = MultimodalProvenance.USER_UPLOAD
    security_classification: SecurityClassification = SecurityClassification.UNRESTRICTED
    filename: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float | None = None

    def __post_init__(self):
        if isinstance(self.media_type, str):
            self.media_type = MultimodalMediaType(self.media_type)
        if isinstance(self.format, str):
            try:
                self.format = MediaFormat(self.format)
            except ValueError:
                pass
        if isinstance(self.lifecycle_state, str):
            self.lifecycle_state = ArtifactLifecycleState(self.lifecycle_state)
        if isinstance(self.provenance, str):
            self.provenance = MultimodalProvenance(self.provenance)
        if isinstance(self.security_classification, str):
            self.security_classification = SecurityClassification(self.security_classification)
        self.size_bytes = max(0, int(self.size_bytes))
        # Sanitize metadata
        if isinstance(self.metadata, dict):
            cleaned = {}
            for k, v in self.metadata.items():
                if isinstance(v, str):
                    cleaned[k] = scrub_sensitive_content(v)
                elif not callable(v):
                    cleaned[k] = v
            self.metadata = cleaned

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "tenant_id": self.tenant_id,
            "media_type": self.media_type.value if isinstance(self.media_type, Enum) else str(self.media_type),
            "format": self.format.value if isinstance(self.format, Enum) else str(self.format),
            "size_bytes": self.size_bytes,
            "checksum_sha256": self.checksum_sha256,
            "storage_uri": self.storage_uri,
            "lifecycle_state": self.lifecycle_state.value if isinstance(self.lifecycle_state, Enum) else str(self.lifecycle_state),
            "provenance": self.provenance.value if isinstance(self.provenance, Enum) else str(self.provenance),
            "security_classification": self.security_classification.value if isinstance(self.security_classification, Enum) else str(self.security_classification),
            "filename": self.filename,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MultimodalArtifact:
        return cls(
            artifact_id=data.get("artifact_id", f"art_{uuid4().hex[:16]}"),
            tenant_id=data.get("tenant_id", "default"),
            media_type=data.get("media_type", MultimodalMediaType.IMAGE),
            format=data.get("format", MediaFormat.PNG),
            size_bytes=data.get("size_bytes", 0),
            checksum_sha256=data.get("checksum_sha256", ""),
            storage_uri=data.get("storage_uri", ""),
            lifecycle_state=data.get("lifecycle_state", ArtifactLifecycleState.UPLOADED),
            provenance=data.get("provenance", MultimodalProvenance.USER_UPLOAD),
            security_classification=data.get("security_classification", SecurityClassification.UNRESTRICTED),
            filename=data.get("filename", ""),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            expires_at=data.get("expires_at"),
        )


@dataclass
class MultimodalProcessingJob:
    """Tracks asynchronous or synchronous processing tasks for artifacts."""
    job_id: str = field(default_factory=lambda: f"job_{uuid4().hex[:16]}")
    tenant_id: str = "default"
    artifact_id: str = ""
    operation: str = "understand"
    capability_id: str = "image_understanding"
    status: JobStatus = JobStatus.PENDING
    error_detail: str | None = None
    attempts: int = 0
    max_attempts: int = 3
    idempotency_key: str | None = None
    started_at: float | None = None
    completed_at: float | None = None
    created_at: float = field(default_factory=time.time)

    def __post_init__(self):
        if isinstance(self.status, str):
            self.status = JobStatus(self.status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "tenant_id": self.tenant_id,
            "artifact_id": self.artifact_id,
            "operation": self.operation,
            "capability_id": self.capability_id,
            "status": self.status.value if isinstance(self.status, Enum) else str(self.status),
            "error_detail": self.error_detail,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "idempotency_key": self.idempotency_key,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MultimodalProcessingJob:
        return cls(
            job_id=data.get("job_id", f"job_{uuid4().hex[:16]}"),
            tenant_id=data.get("tenant_id", "default"),
            artifact_id=data.get("artifact_id", ""),
            operation=data.get("operation", "understand"),
            capability_id=data.get("capability_id", "image_understanding"),
            status=data.get("status", JobStatus.PENDING),
            error_detail=data.get("error_detail"),
            attempts=data.get("attempts", 0),
            max_attempts=data.get("max_attempts", 3),
            idempotency_key=data.get("idempotency_key"),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            created_at=data.get("created_at", time.time()),
        )


@dataclass
class MultimodalResult:
    """Normalized structured output from multimodal model or tool processing."""
    result_id: str = field(default_factory=lambda: f"res_{uuid4().hex[:16]}")
    tenant_id: str = "default"
    artifact_id: str = ""
    job_id: str | None = None
    operation: str = "understand"
    extracted_text: str = ""
    structured_data: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    bounding_boxes: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def __post_init__(self):
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        if self.extracted_text:
            self.extracted_text = scrub_sensitive_content(self.extracted_text)

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "tenant_id": self.tenant_id,
            "artifact_id": self.artifact_id,
            "job_id": self.job_id,
            "operation": self.operation,
            "extracted_text": self.extracted_text,
            "structured_data": dict(self.structured_data),
            "confidence": self.confidence,
            "bounding_boxes": list(self.bounding_boxes),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MultimodalResult:
        return cls(
            result_id=data.get("result_id", f"res_{uuid4().hex[:16]}"),
            tenant_id=data.get("tenant_id", "default"),
            artifact_id=data.get("artifact_id", ""),
            job_id=data.get("job_id"),
            operation=data.get("operation", "understand"),
            extracted_text=data.get("extracted_text", ""),
            structured_data=data.get("structured_data", {}),
            confidence=data.get("confidence", 1.0),
            bounding_boxes=data.get("bounding_boxes", []),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", time.time()),
        )


@dataclass
class MultimodalDerivation:
    """Lineage link tracking derived artifacts or representations from a parent artifact."""
    derivation_id: str = field(default_factory=lambda: f"drv_{uuid4().hex[:16]}")
    tenant_id: str = "default"
    source_artifact_id: str = ""
    derived_type: str = "vector_embedding"
    derived_id: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "derivation_id": self.derivation_id,
            "tenant_id": self.tenant_id,
            "source_artifact_id": self.source_artifact_id,
            "derived_type": self.derived_type,
            "derived_id": self.derived_id,
            "created_at": self.created_at,
        }


@dataclass
class MultimodalCapabilityUsage:
    """Usage and cost accounting telemetry for multimodal model invocations."""
    usage_id: str = field(default_factory=lambda: f"usg_{uuid4().hex[:16]}")
    tenant_id: str = "default"
    capability_id: str = "image_understanding"
    provider: str = "gateway"
    model: str = "default"
    input_size_bytes: int = 0
    output_tokens: int = 0
    duration_ms: float = 0.0
    status: str = "success"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "usage_id": self.usage_id,
            "tenant_id": self.tenant_id,
            "capability_id": self.capability_id,
            "provider": self.provider,
            "model": self.model,
            "input_size_bytes": self.input_size_bytes,
            "output_tokens": self.output_tokens,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "created_at": self.created_at,
        }
