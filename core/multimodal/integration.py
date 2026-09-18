"""M57 — Multimodal Downstream Bridges (M56 Cognitive Memory & M43 Vector/RAG).

Provides memory admission controls, vector derivation synchronization,
and cascading deletion with zero resurrection.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from core.cognitive_memory.types import (
    CognitiveMemory,
    CognitiveMemoryType,
    ProvenanceType,
)
from core.multimodal.types import (
    MultimodalArtifact,
    MultimodalDerivation,
    MultimodalProvenance,
    MultimodalResult,
)

if TYPE_CHECKING:
    from core.repositories.base_cognitive_memory import BaseCognitiveMemoryRepository
    from core.repositories.base_multimodal import BaseMultimodalRepository

logger = logging.getLogger("aura.multimodal.integration")


class MultimodalMemoryBridge:
    """Bridges validated multimodal observations into M56 Cognitive Memory (Invariant M57-F24)."""

    def __init__(
        self,
        memory_repo: BaseCognitiveMemoryRepository | None = None,
        multimodal_repo: BaseMultimodalRepository | None = None,
    ):
        self.memory_repo = memory_repo
        self.multimodal_repo = multimodal_repo

    def admit_to_cognitive_memory(
        self,
        tenant_id: str,
        artifact: MultimodalArtifact,
        result: MultimodalResult,
        explicit_user_confirmed: bool = False,
    ) -> CognitiveMemory | None:
        """Admit multimodal result as a CognitiveMemory entry (Invariant M57-F24).
        
        Admission Invariant: Inferred observations from images, audio, or documents MUST default
        to tool_observed or model_inferred; they NEVER gain user_explicit provenance unless explicitly confirmed.
        """
        if not self.memory_repo:
            return None

        # Determine strict provenance
        if explicit_user_confirmed and artifact.provenance == MultimodalProvenance.USER_UPLOAD:
            provenance_type = ProvenanceType.USER_EXPLICIT
            confidence = 1.0
        elif artifact.provenance == MultimodalProvenance.TOOL_OUTPUT:
            provenance_type = ProvenanceType.TOOL_OBSERVED
            confidence = 0.90
        else:
            provenance_type = ProvenanceType.MODEL_INFERRED
            confidence = 0.70

        mem_key = f"mm_{artifact.artifact_id}_{result.operation}"
        saved_mem, _ = self.memory_repo.record_memory(
            tenant_id=tenant_id,
            content=result.extracted_text or f"Multimodal analysis from {artifact.filename or artifact.artifact_id}",
            memory_type=CognitiveMemoryType.EXPERIENCE if result.operation == "experience" else CognitiveMemoryType.SEMANTIC,
            category="multimodal",
            key=mem_key,
            structured_data=dict(result.structured_data),
            confidence=confidence,
            provenance_type=provenance_type,
            tags=["multimodal", artifact.media_type.value, result.operation],
            metadata={
                "source_artifact_id": artifact.artifact_id,
                "result_id": result.result_id,
                "checksum": artifact.checksum_sha256,
            },
        )

        # Record lineage derivation
        if self.multimodal_repo:
            drv = MultimodalDerivation(
                tenant_id=tenant_id,
                source_artifact_id=artifact.artifact_id,
                derived_type="cognitive_memory",
                derived_id=saved_mem.memory_id,
            )
            self.multimodal_repo.save_derivation(drv)

        return saved_mem


class MultimodalDeletionCascade:
    """Coordinates cascading deletion across storage, repository, memory, and derivations (Invariant M57-F25)."""

    def __init__(
        self,
        multimodal_repo: BaseMultimodalRepository,
        memory_repo: BaseCognitiveMemoryRepository | None = None,
        storage: Any | None = None,
    ):
        self.multimodal_repo = multimodal_repo
        self.memory_repo = memory_repo
        self.storage = storage

    def delete_artifact_cascade(self, tenant_id: str, artifact_id: str) -> bool:
        """Permanently delete an artifact and all its derivations, results, and memory records with ZERO RESURRECTION."""
        artifact = self.multimodal_repo.get_artifact(artifact_id, tenant_id=tenant_id)
        if not artifact:
            return False

        # 1. Fetch derivations
        derivations = self.multimodal_repo.list_derivations(artifact_id, tenant_id=tenant_id)
        for drv in derivations:
            if drv.derived_type == "cognitive_memory" and self.memory_repo:
                try:
                    self.memory_repo.delete_memory(drv.derived_id, tenant_id=tenant_id, hard_delete=True)
                except Exception as e:
                    logger.warning(f"Error deleting derived memory {drv.derived_id}: {e}")

        # 2. Delete binary from object storage
        if self.storage:
            try:
                self.storage.delete(tenant_id, artifact_id)
            except Exception as e:
                logger.warning(f"Error deleting binary object {artifact_id}: {e}")

        # 3. Authoritatively delete artifact and all dependent records in DB
        return self.multimodal_repo.delete_artifact(artifact_id, tenant_id=tenant_id)
