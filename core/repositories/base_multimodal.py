"""M57 — Abstract Multimodal Repository Interface.

Defines persistence contracts for multimodal artifacts, processing jobs,
structured results, derivations, and usage analytics.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.multimodal.types import (
    ArtifactLifecycleState,
    MultimodalArtifact,
    MultimodalCapabilityUsage,
    MultimodalDerivation,
    MultimodalProcessingJob,
    MultimodalResult,
)


class BaseMultimodalRepository(ABC):
    """Abstract repository for multimodal artifacts and execution records."""

    @abstractmethod
    def save_artifact(self, artifact: MultimodalArtifact) -> MultimodalArtifact:
        """Create or update a multimodal artifact record."""
        pass

    @abstractmethod
    def get_artifact(self, artifact_id: str, tenant_id: str) -> MultimodalArtifact | None:
        """Retrieve an artifact by ID scoped to tenant."""
        pass

    @abstractmethod
    def list_artifacts(
        self,
        tenant_id: str,
        lifecycle_state: ArtifactLifecycleState | str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[MultimodalArtifact]:
        """List artifacts for a tenant with optional lifecycle filtering."""
        pass

    @abstractmethod
    def update_artifact_state(
        self,
        artifact_id: str,
        tenant_id: str,
        lifecycle_state: ArtifactLifecycleState | str,
        reason: str = "",
    ) -> MultimodalArtifact | None:
        """Update the lifecycle state of an artifact."""
        pass

    @abstractmethod
    def delete_artifact(self, artifact_id: str, tenant_id: str) -> bool:
        """Permanently delete an artifact and its dependent records for a tenant."""
        pass

    @abstractmethod
    def save_job(self, job: MultimodalProcessingJob) -> MultimodalProcessingJob:
        """Create or update a multimodal processing job."""
        pass

    @abstractmethod
    def get_job(self, job_id: str, tenant_id: str) -> MultimodalProcessingJob | None:
        """Retrieve a processing job by ID."""
        pass

    @abstractmethod
    def get_job_by_idempotency(self, tenant_id: str, idempotency_key: str) -> MultimodalProcessingJob | None:
        """Look up existing job by idempotency key for tenant."""
        pass

    @abstractmethod
    def save_result(self, result: MultimodalResult) -> MultimodalResult:
        """Persist a structured multimodal processing result."""
        pass

    @abstractmethod
    def get_result(self, result_id: str, tenant_id: str) -> MultimodalResult | None:
        """Retrieve a result by ID."""
        pass

    @abstractmethod
    def list_results(self, artifact_id: str, tenant_id: str) -> list[MultimodalResult]:
        """List results associated with an artifact."""
        pass

    @abstractmethod
    def save_derivation(self, derivation: MultimodalDerivation) -> MultimodalDerivation:
        """Record lineage derivation from parent artifact."""
        pass

    @abstractmethod
    def list_derivations(self, source_artifact_id: str, tenant_id: str) -> list[MultimodalDerivation]:
        """List all derived records for a source artifact."""
        pass

    @abstractmethod
    def save_usage(self, usage: MultimodalCapabilityUsage) -> MultimodalCapabilityUsage:
        """Record capability usage telemetry."""
        pass

    @abstractmethod
    def purge_tenant_data(self, tenant_id: str) -> int:
        """Hard purge all multimodal records for a tenant (GDPR Right-to-be-Forgotten)."""
        pass
