"""M57 — Multimodal Capabilities & Processor Pipeline Unit Tests."""

import pytest
from core.model_gateway import ModelGateway, ProviderCatalog, ProviderRegistration
from core.multimodal.capabilities import MultimodalCapabilityRegistry
from core.multimodal.processor import MultimodalProcessor
from core.multimodal.storage import InMemoryStorageService
from core.multimodal.types import (
    ArtifactLifecycleState,
    JobStatus,
    MediaFormat,
    MultimodalMediaType,
    MultimodalProvenance,
)
from core.repositories.in_memory_multimodal import InMemoryMultimodalRepository
from providers.fake_model import FakeModelProvider


class TestMultimodalCapabilitiesAndProcessorUnit:
    @pytest.fixture
    def registry(self):
        return MultimodalCapabilityRegistry()

    @pytest.fixture
    def processor(self, registry):
        repo = InMemoryMultimodalRepository()
        storage = InMemoryStorageService()
        fake_prov = FakeModelProvider()
        catalog = ProviderCatalog([ProviderRegistration(provider_id="fake_multimodal", provider=fake_prov)])
        gateway = ModelGateway(catalog=catalog)
        return MultimodalProcessor(
            repository=repo,
            storage=storage,
            model_gateway=gateway,
            capability_registry=registry,
        )

    def test_capability_registry_resolution(self, registry):
        cap = registry.resolve(MultimodalMediaType.IMAGE, MediaFormat.PNG, operation="understand")
        assert cap.capability_id == "image_understanding"

        audio_cap = registry.resolve(MultimodalMediaType.AUDIO, MediaFormat.WAV, operation="transcribe")
        assert audio_cap.capability_id == "audio_transcription"

        doc_cap = registry.resolve(MultimodalMediaType.DOCUMENT, MediaFormat.PDF, operation="extract_document")
        assert doc_cap.capability_id == "document_extraction"

    def test_unsupported_capability_fails_closed(self, registry):
        # Invariant M57-F11
        with pytest.raises(ValueError, match="No enabled capability found"):
            registry.resolve(MultimodalMediaType.AUDIO, MediaFormat.WAV, operation="generate_video")

    def test_ingest_and_process_image_pipeline(self, processor):
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        artifact = processor.ingest_artifact(
            tenant_id="tenant_10",
            data=png_bytes,
            filename="test_image.png",
            declared_format=MediaFormat.PNG,
        )
        assert artifact.lifecycle_state == ArtifactLifecycleState.ACCEPTED
        assert artifact.checksum_sha256 != ""

        result, job = processor.process_artifact(
            tenant_id="tenant_10",
            artifact_id=artifact.artifact_id,
            operation="understand",
            user_prompt="Explain this diagram",
        )
        assert result.artifact_id == artifact.artifact_id
        assert job.status == JobStatus.COMPLETED
        assert result.confidence == 0.95
        assert "Fake response" in result.extracted_text

    def test_idempotent_processing_returns_cached_result(self, processor):
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        artifact = processor.ingest_artifact(
            tenant_id="tenant_20",
            data=png_bytes,
            filename="idempotent.png",
            declared_format=MediaFormat.PNG,
        )

        res1, job1 = processor.process_artifact(
            tenant_id="tenant_20",
            artifact_id=artifact.artifact_id,
            operation="understand",
            idempotency_key="idemp_key_999",
        )

        res2, job2 = processor.process_artifact(
            tenant_id="tenant_20",
            artifact_id=artifact.artifact_id,
            operation="understand",
            idempotency_key="idemp_key_999",
        )

        assert res1.result_id == res2.result_id
        assert job1.job_id == job2.job_id
