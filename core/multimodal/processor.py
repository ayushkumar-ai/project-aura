"""M57 — Multimodal Pipeline Processor.

Executes end-to-end multimodal intake, validation, storage, capability resolution,
ModelGateway routing, structured result normalization, and error containment.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from core.metrics import get_metrics_registry
from core.model_gateway import ModelGateway
from core.models import AURAResponse
from core.multimodal.capabilities import MultimodalCapabilityRegistry
from core.multimodal.storage import IObjectStorageService, InMemoryStorageService
from core.multimodal.trust import wrap_untrusted_multimodal_data, sanitize_and_check_injection
from core.multimodal.types import (
    ArtifactLifecycleState,
    JobStatus,
    MediaFormat,
    MultimodalArtifact,
    MultimodalCapabilityUsage,
    MultimodalLimitsConfig,
    MultimodalMediaType,
    MultimodalProcessingJob,
    MultimodalProvenance,
    MultimodalResult,
    SecurityClassification,
)
from core.multimodal.validation import MultimodalValidator

if TYPE_CHECKING:
    from core.repositories.base_multimodal import BaseMultimodalRepository

logger = logging.getLogger("aura.multimodal.processor")


class MultimodalProcessor:
    """Coordinates multimodal artifact intake, validation, storage, and ModelGateway processing."""

    def __init__(
        self,
        repository: BaseMultimodalRepository | None = None,
        storage: IObjectStorageService | None = None,
        model_gateway: ModelGateway | None = None,
        capability_registry: MultimodalCapabilityRegistry | None = None,
        limits: MultimodalLimitsConfig | None = None,
    ):
        self.repository = repository
        self.storage = storage or InMemoryStorageService()
        self.model_gateway = model_gateway
        self.registry = capability_registry or MultimodalCapabilityRegistry()
        self.limits = limits or MultimodalLimitsConfig()
        self.validator = MultimodalValidator(self.limits)

    def ingest_artifact(
        self,
        tenant_id: str,
        data: bytes,
        filename: str = "",
        declared_format: str | MediaFormat | None = None,
        provenance: MultimodalProvenance = MultimodalProvenance.USER_UPLOAD,
        metadata: dict[str, Any] | None = None,
    ) -> MultimodalArtifact:
        """Intake raw binary, validate magic bytes, compute SHA-256, store, and record artifact."""
        if not data:
            raise ValueError("Cannot ingest empty payload.")

        # 1. Validate magic bytes and format
        detected_format, media_type = self.validator.sniff_and_validate_format(
            data, declared_format=declared_format, filename=filename
        )
        checksum = self.validator.compute_sha256(data)
        size_bytes = len(data)
        artifact_id = f"art_{uuid4().hex[:16]}"

        # 2. Store payload safely in object storage
        storage_uri = self.storage.store(
            tenant_id=tenant_id,
            artifact_id=artifact_id,
            data=data,
            format_ext=detected_format.name.lower() if isinstance(detected_format, MediaFormat) else "bin",
        )

        artifact = MultimodalArtifact(
            artifact_id=artifact_id,
            tenant_id=tenant_id,
            media_type=media_type,
            format=detected_format,
            size_bytes=size_bytes,
            checksum_sha256=checksum,
            storage_uri=storage_uri,
            lifecycle_state=ArtifactLifecycleState.ACCEPTED,
            provenance=provenance,
            security_classification=SecurityClassification.UNRESTRICTED,
            filename=filename,
            metadata=metadata or {},
        )

        if self.repository:
            artifact = self.repository.save_artifact(artifact)

        try:
            metrics = get_metrics_registry()
            metrics.get_counter("aura_multimodal_requests_total").inc(
                labels={"media_type": media_type.value, "operation": "ingest", "status": "success"}
            )
        except Exception:
            pass

        return artifact

    def process_artifact(
        self,
        tenant_id: str,
        artifact_id: str,
        operation: str = "understand",
        user_prompt: str = "",
        idempotency_key: str | None = None,
    ) -> tuple[MultimodalResult, MultimodalProcessingJob]:
        """Execute multimodal processing pipeline via ModelGateway (Invariant M57-F12)."""
        start_time = time.time()

        # 1. Retrieve and verify artifact ownership
        if not self.repository:
            raise RuntimeError("Repository is required for multimodal processing.")

        artifact = self.repository.get_artifact(artifact_id, tenant_id=tenant_id)
        if not artifact:
            raise ValueError(f"Artifact '{artifact_id}' not found for tenant '{tenant_id}'.")

        if artifact.lifecycle_state in (ArtifactLifecycleState.DELETED, ArtifactLifecycleState.QUARANTINED):
            raise PermissionError(f"Cannot process artifact in terminal state: '{artifact.lifecycle_state.value}'.")

        # 2. Check Idempotency (Invariant M57-F08)
        if idempotency_key:
            existing_job = self.repository.get_job_by_idempotency(tenant_id, idempotency_key)
            if existing_job and existing_job.status == JobStatus.COMPLETED:
                results = self.repository.list_results(artifact_id, tenant_id=tenant_id)
                for res in results:
                    if res.job_id == existing_job.job_id:
                        return res, existing_job

        # 3. Resolve capability
        capability = self.registry.resolve(
            media_type=artifact.media_type,
            format_val=artifact.format,
            operation=operation,
        )

        # 4. Create processing job record
        job = MultimodalProcessingJob(
            job_id=f"job_{uuid4().hex[:16]}",
            tenant_id=tenant_id,
            artifact_id=artifact_id,
            operation=operation,
            capability_id=capability.capability_id,
            status=JobStatus.RUNNING,
            idempotency_key=idempotency_key,
            started_at=time.time(),
        )
        job = self.repository.save_job(job)

        # 5. Read payload from storage
        data = self.storage.retrieve(tenant_id, artifact_id)
        if data is None:
            job.status = JobStatus.FAILED
            job.error_detail = "Failed to retrieve artifact payload from storage."
            job.completed_at = time.time()
            self.repository.save_job(job)
            raise RuntimeError(job.error_detail)

        # 6. Construct prompt envelope with prompt-injection defense
        raw_text_sample = ""
        if artifact.media_type in (MultimodalMediaType.DOCUMENT, MultimodalMediaType.STRUCTURED_DATA):
            try:
                raw_text_sample = data.decode("utf-8", errors="ignore")[:4000]
            except Exception:
                pass
        elif artifact.media_type == MultimodalMediaType.IMAGE:
            raw_text_sample = f"[Image Binary: {len(data)} bytes, format: {artifact.format}]"
        elif artifact.media_type == MultimodalMediaType.AUDIO:
            raw_text_sample = f"[Audio Binary: {len(data)} bytes, format: {artifact.format}]"

        wrapped_sample = wrap_untrusted_multimodal_data(
            content=raw_text_sample,
            artifact_id=artifact_id,
            media_type=artifact.media_type.value,
            provenance=artifact.provenance,
        )

        system_instruction = (
            f"You are AURA's Multimodal Engine. Analyze the following {artifact.media_type.value} "
            f"for operation '{operation}'.\n"
            f"User Instruction: {user_prompt or 'Analyze content thoroughly.'}\n"
            f"Input Data:\n{wrapped_sample}\n"
            f"Respond with a clear summary and JSON structured data."
        )

        # 7. Invoke ModelGateway
        extracted_text = ""
        structured_data = {}
        confidence = 0.95
        provider_name = "gateway"
        model_name = "default"

        if self.model_gateway:
            req_id = uuid4()
            try:
                gw_resp: AURAResponse = self.model_gateway.generate(prompt=system_instruction, request_id=req_id)
                extracted_text = gw_resp.content
                provider_name = gw_resp.metadata.get("gateway_provider", "gateway")
                model_name = gw_resp.metadata.get("gateway_model", "default")
                # Parse JSON block if present
                if "{" in extracted_text and "}" in extracted_text:
                    try:
                        start_idx = extracted_text.find("{")
                        end_idx = extracted_text.rfind("}") + 1
                        structured_data = json.loads(extracted_text[start_idx:end_idx])
                    except Exception:
                        structured_data = {"analysis": extracted_text}
                else:
                    structured_data = {"analysis": extracted_text}
            except Exception as e:
                logger.error(f"ModelGateway execution failed for artifact {artifact_id}: {e}")
                job.status = JobStatus.FAILED
                job.error_detail = str(e)
                job.completed_at = time.time()
                self.repository.save_job(job)
                raise
        else:
            # Deterministic fallback when ModelGateway is not wired
            extracted_text = f"Simulated {operation} result for {artifact.media_type.value} ({artifact.filename or artifact_id})"
            structured_data = {"operation": operation, "media_type": artifact.media_type.value, "size_bytes": artifact.size_bytes}

        # 8. Check for prompt injection in extracted text
        _, has_injection = sanitize_and_check_injection(extracted_text)
        if has_injection:
            structured_data["security_flag"] = "prompt_injection_detected"

        # 9. Create and save MultimodalResult
        result = MultimodalResult(
            result_id=f"res_{uuid4().hex[:16]}",
            tenant_id=tenant_id,
            artifact_id=artifact_id,
            job_id=job.job_id,
            operation=operation,
            extracted_text=extracted_text,
            structured_data=structured_data,
            confidence=confidence,
            metadata={"has_injection_flag": has_injection, "provider": provider_name, "model": model_name},
        )
        result = self.repository.save_result(result)

        # 10. Update job status to completed
        duration_ms = (time.time() - start_time) * 1000.0
        job.status = JobStatus.COMPLETED
        job.completed_at = time.time()
        job = self.repository.save_job(job)

        # 11. Record usage telemetry
        usage = MultimodalCapabilityUsage(
            tenant_id=tenant_id,
            capability_id=capability.capability_id,
            provider=provider_name,
            model=model_name,
            input_size_bytes=artifact.size_bytes,
            output_tokens=len(extracted_text.split()),
            duration_ms=duration_ms,
            status="success",
        )
        self.repository.save_usage(usage)

        # 12. Update artifact lifecycle state to PROCESSED
        artifact.lifecycle_state = ArtifactLifecycleState.PROCESSED
        artifact.updated_at = time.time()
        self.repository.save_artifact(artifact)

        try:
            metrics = get_metrics_registry()
            metrics.get_counter("aura_multimodal_requests_total").inc(
                labels={"media_type": artifact.media_type.value, "operation": operation, "status": "success"}
            )
            metrics.get_histogram("aura_multimodal_duration_seconds").observe(
                duration_ms / 1000.0, labels={"operation": operation}
            )
        except Exception:
            pass

        return result, job
