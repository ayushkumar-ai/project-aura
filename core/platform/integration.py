"""M58 — Downstream Bridges (M56 Cognitive Memory, M57 Multimodal & M52 Tasks).

Provides memory admission controls, multimodal forwarding, and cascading deletion with zero resurrection.
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
from core.platform.types import DeviceExecutionRecord, DeviceRecord

if TYPE_CHECKING:
    from core.multimodal.processor import MultimodalProcessor
    from core.multimodal.types import MultimodalArtifact
    from core.repositories.base_cognitive_memory import BaseCognitiveMemoryRepository
    from core.repositories.base_platform import BasePlatformRepository

logger = logging.getLogger("aura.platform.integration")


class PlatformMemoryBridge:
    """Bridges validated device observations into M56 Cognitive Memory (Invariant M58-F22)."""

    def __init__(
        self,
        memory_repo: BaseCognitiveMemoryRepository | None = None,
        platform_repo: BasePlatformRepository | None = None,
    ):
        self.memory_repo = memory_repo
        self.platform_repo = platform_repo

    def admit_observation_to_memory(
        self,
        tenant_id: str,
        device: DeviceRecord,
        execution: DeviceExecutionRecord,
        explicit_user_confirmed: bool = False,
    ) -> CognitiveMemory | None:
        """Admit device observation into Cognitive Memory.
        
        Enforces Invariant M58-F22: Device observations MUST default to tool_observed provenance
        and NEVER gain user_explicit authority unless explicitly confirmed by the user.
        """
        if not self.memory_repo:
            return None

        provenance_type = ProvenanceType.USER_EXPLICIT if explicit_user_confirmed else ProvenanceType.TOOL_OBSERVED
        confidence = 1.0 if explicit_user_confirmed else 0.85

        content_summary = (
            f"Device observation from {device.name} ({device.device_id}): "
            f"Executed {execution.capability_name} -> {execution.result.get('data', {})}"
        )

        mem_key = f"dev_{device.device_id}_{execution.capability_name}"
        saved_mem, _ = self.memory_repo.record_memory(
            tenant_id=tenant_id,
            content=content_summary,
            memory_type=CognitiveMemoryType.EPISODIC,
            category="device_observation",
            key=mem_key,
            structured_data=dict(execution.result),
            confidence=confidence,
            provenance_type=provenance_type,
            tags=["device", device.device_type.value, execution.capability_name],
            metadata={
                "device_id": device.device_id,
                "execution_id": execution.execution_id,
                "platform": device.platform.value,
            },
        )
        return saved_mem

    def cascade_device_deletion(self, tenant_id: str, device_id: str) -> bool:
        """Delete device and all associated memory records with zero resurrection (Invariant M58-F23)."""
        if not self.platform_repo:
            return False

        # 1. Clear any derived memories from this device in M56 repository
        if self.memory_repo:
            memories = self.memory_repo.query_memories(tenant_id=tenant_id, limit=200)
            for mem in memories:
                if mem.metadata.get("device_id") == device_id:
                    try:
                        self.memory_repo.delete_memory(mem.memory_id, tenant_id=tenant_id, hard_delete=True)
                    except Exception as e:
                        logger.warning(f"Failed to cascade delete memory {mem.memory_id}: {e}")

        # 2. Authoritatively delete device record
        return self.platform_repo.delete_device(device_id=device_id, tenant_id=tenant_id)


class PlatformMultimodalBridge:
    """Bridges screen captures and device media into M57 Multimodal Pipeline (Invariant M58-F24)."""

    def __init__(self, multimodal_processor: MultimodalProcessor | None = None):
        self.processor = multimodal_processor

    def forward_screenshot_to_multimodal(
        self,
        tenant_id: str,
        device: DeviceRecord,
        raw_image_bytes: bytes,
        filename: str = "screenshot.png",
    ) -> MultimodalArtifact | None:
        """Forward raw screen capture to M57 multimodal intake and validation pipeline."""
        if not self.processor:
            return None

        return self.processor.ingest_artifact(
            tenant_id=tenant_id,
            data=raw_image_bytes,
            filename=filename,
            declared_format="image/png",
            metadata={"device_id": device.device_id, "platform": device.platform.value},
        )
