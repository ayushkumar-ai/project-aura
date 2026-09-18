"""M57 — Multimodal Types & Domain Models Unit Tests."""

import pytest
from core.multimodal.types import (
    ArtifactLifecycleState,
    JobStatus,
    MediaFormat,
    MultimodalArtifact,
    MultimodalCapabilityUsage,
    MultimodalDerivation,
    MultimodalLimitsConfig,
    MultimodalMediaType,
    MultimodalProcessingJob,
    MultimodalProvenance,
    MultimodalResult,
    SecurityClassification,
)


class TestMultimodalTypesUnit:
    def test_multimodal_artifact_creation_and_defaults(self):
        art = MultimodalArtifact(
            tenant_id="tenant_123",
            media_type=MultimodalMediaType.IMAGE,
            format=MediaFormat.PNG,
            size_bytes=1024,
            checksum_sha256="abc123sha",
            storage_uri="mem://tenant_123/art_1",
            filename="diagram.png",
        )
        assert art.artifact_id.startswith("art_")
        assert art.tenant_id == "tenant_123"
        assert art.media_type == MultimodalMediaType.IMAGE
        assert art.format == MediaFormat.PNG
        assert art.lifecycle_state == ArtifactLifecycleState.UPLOADED
        assert art.security_classification == SecurityClassification.UNRESTRICTED

    def test_artifact_serialization_roundtrip(self):
        art = MultimodalArtifact(
            tenant_id="tenant_456",
            media_type=MultimodalMediaType.DOCUMENT,
            format=MediaFormat.PDF,
            size_bytes=2048,
            checksum_sha256="def456sha",
            storage_uri="mem://tenant_456/art_2",
            filename="report.pdf",
            metadata={"author": "Alice", "secret": "Bearer sk-1234567890abcdef1234567890"},
        )
        d = art.to_dict()
        assert d["tenant_id"] == "tenant_456"
        assert d["media_type"] == "document"
        assert d["format"] == "application/pdf"
        # Secret scrubbing in metadata
        assert "[REDACTED_SECRET]" in str(d["metadata"])

        restored = MultimodalArtifact.from_dict(d)
        assert restored.artifact_id == art.artifact_id
        assert restored.media_type == MultimodalMediaType.DOCUMENT
        assert restored.format == MediaFormat.PDF

    def test_processing_job_model(self):
        job = MultimodalProcessingJob(
            tenant_id="tenant_1",
            artifact_id="art_1",
            operation="ocr",
            capability_id="ocr_text_extraction",
            status=JobStatus.PENDING,
            idempotency_key="idemp_123",
        )
        assert job.job_id.startswith("job_")
        assert job.status == JobStatus.PENDING
        d = job.to_dict()
        assert d["operation"] == "ocr"
        assert d["idempotency_key"] == "idemp_123"

        restored = MultimodalProcessingJob.from_dict(d)
        assert restored.job_id == job.job_id
        assert restored.status == JobStatus.PENDING

    def test_multimodal_result_confidence_bounding(self):
        res = MultimodalResult(
            tenant_id="tenant_1",
            artifact_id="art_1",
            extracted_text="Detected text with api_key: 1234567890123456",
            confidence=1.5,  # Exceeds bounds
        )
        assert res.confidence == 1.0
        assert "[REDACTED_SECRET]" in res.extracted_text

        res_low = MultimodalResult(
            tenant_id="tenant_1",
            artifact_id="art_1",
            confidence=-0.5,
        )
        assert res_low.confidence == 0.0

    def test_multimodal_derivation_model(self):
        drv = MultimodalDerivation(
            tenant_id="tenant_1",
            source_artifact_id="art_100",
            derived_type="vector_embedding",
            derived_id="vec_200",
        )
        d = drv.to_dict()
        assert d["source_artifact_id"] == "art_100"
        assert d["derived_type"] == "vector_embedding"
        assert d["derived_id"] == "vec_200"

    def test_capability_usage_model(self):
        usg = MultimodalCapabilityUsage(
            tenant_id="tenant_1",
            capability_id="image_understanding",
            provider="gemini",
            model="gemini-2.5-flash",
            input_size_bytes=4096,
            output_tokens=150,
            duration_ms=250.5,
        )
        d = usg.to_dict()
        assert d["capability_id"] == "image_understanding"
        assert d["provider"] == "gemini"
        assert d["duration_ms"] == 250.5
