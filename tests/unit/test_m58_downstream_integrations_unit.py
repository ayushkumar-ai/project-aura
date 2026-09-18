"""M58 — Downstream Integrations (M56 Memory, M57 Multimodal) Unit Tests."""

import pytest
from core.cognitive_memory.types import ProvenanceType
from core.multimodal.processor import MultimodalProcessor
from core.multimodal.storage import InMemoryStorageService
from core.platform.gateway import PlatformIntegrationGateway
from core.platform.integration import (
    PlatformMemoryBridge,
    PlatformMultimodalBridge,
)
from core.repositories.in_memory_cognitive_memory import InMemoryCognitiveMemoryRepository
from core.repositories.in_memory_multimodal import InMemoryMultimodalRepository
from core.repositories.in_memory_platform import InMemoryPlatformRepository


class TestDownstreamIntegrationsUnit:
    def test_memory_admission_provenance_and_cascade_deletion(self):
        # Invariant M58-F22 & M58-F23 (TEST-M58-SEC-13)
        cog_repo = InMemoryCognitiveMemoryRepository()
        plat_repo = InMemoryPlatformRepository()
        gateway = PlatformIntegrationGateway(repository=plat_repo)
        bridge = PlatformMemoryBridge(memory_repo=cog_repo, platform_repo=plat_repo)

        dev = gateway.register_device(tenant_id="tenant_mem", name="Telemetry Station", auto_authorize=True)
        exec_record = gateway.execute_action(
            tenant_id="tenant_mem",
            device_id=dev.device_id,
            capability_name="get_clock",
        )

        # 1. Admit observation without explicit user confirmation -> defaults to TOOL_OBSERVED
        mem = bridge.admit_observation_to_memory(
            tenant_id="tenant_mem",
            device=dev,
            execution=exec_record,
            explicit_user_confirmed=False,
        )
        assert mem.provenance_type == ProvenanceType.TOOL_OBSERVED
        assert mem.confidence == 0.85
        assert cog_repo.get_memory(mem.memory_id, tenant_id="tenant_mem") is not None

        # 2. Cascade delete device -> permanently deletes derived cognitive memory with zero resurrection
        deleted = bridge.cascade_device_deletion(tenant_id="tenant_mem", device_id=dev.device_id)
        assert deleted is True

        assert plat_repo.get_device(dev.device_id, tenant_id="tenant_mem") is None
        assert cog_repo.get_memory(mem.memory_id, tenant_id="tenant_mem") is None

    def test_multimodal_screenshot_forwarding(self):
        # Invariant M58-F24
        mm_repo = InMemoryMultimodalRepository()
        mm_storage = InMemoryStorageService()
        processor = MultimodalProcessor(repository=mm_repo, storage=mm_storage)
        mm_bridge = PlatformMultimodalBridge(multimodal_processor=processor)

        plat_repo = InMemoryPlatformRepository()
        gateway = PlatformIntegrationGateway(repository=plat_repo)
        dev = gateway.register_device(tenant_id="tenant_mm", name="Graphics Rig", auto_authorize=True)

        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        art = mm_bridge.forward_screenshot_to_multimodal(
            tenant_id="tenant_mm",
            device=dev,
            raw_image_bytes=png_bytes,
            filename="screen_capture_1.png",
        )
        assert art is not None
        assert art.media_type.value == "image"
        assert art.metadata.get("device_id") == dev.device_id
