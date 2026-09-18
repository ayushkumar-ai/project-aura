"""M57 — Multimodal Storage & Lifecycle State Machine Unit Tests."""

import pytest
from core.multimodal.storage import InMemoryStorageService, LocalStorageService
from core.multimodal.types import ArtifactLifecycleState, MediaFormat, MultimodalArtifact, MultimodalMediaType
from core.repositories.in_memory_multimodal import InMemoryMultimodalRepository


class TestMultimodalLifecycleAndStorageUnit:
    def test_in_memory_storage_crud(self):
        storage = InMemoryStorageService()
        uri = storage.store("tenant_a", "art_1", b"binary_image_data", format_ext="png")
        assert uri.startswith("mem://tenant_a/art_1")
        assert storage.exists("tenant_a", "art_1")
        assert storage.retrieve("tenant_a", "art_1") == b"binary_image_data"
        # Cross-tenant retrieval returns None (Invariant M57-F02)
        assert storage.retrieve("tenant_b", "art_1") is None

        deleted = storage.delete("tenant_a", "art_1")
        assert deleted is True
        assert not storage.exists("tenant_a", "art_1")

    def test_local_storage_path_traversal_prevention(self, tmp_path):
        storage = LocalStorageService(root_dir=str(tmp_path / "storage"))
        # Storing valid data
        uri = storage.store("tenant_1", "art_normal", b"safe_bytes", format_ext="png")
        assert uri.startswith("file://")
        assert storage.retrieve("tenant_1", "art_normal") == b"safe_bytes"

        # Attempt path traversal in tenant_id or artifact_id (TEST-M57-SEC-08)
        with pytest.raises(ValueError, match="Invalid path component"):
            storage.store("../../../etc", "art_bad", b"bad")
        with pytest.raises(ValueError, match="Invalid path component"):
            storage.store("tenant_1", "../bad_file", b"bad")

    def test_artifact_lifecycle_transitions(self):
        repo = InMemoryMultimodalRepository()
        art = MultimodalArtifact(
            tenant_id="tenant_1",
            media_type=MultimodalMediaType.IMAGE,
            format=MediaFormat.PNG,
            size_bytes=512,
            lifecycle_state=ArtifactLifecycleState.UPLOADED,
        )
        saved = repo.save_artifact(art)
        assert saved.lifecycle_state == ArtifactLifecycleState.UPLOADED

        # Transition to ACCEPTED
        up1 = repo.update_artifact_state(saved.artifact_id, tenant_id="tenant_1", lifecycle_state=ArtifactLifecycleState.ACCEPTED)
        assert up1.lifecycle_state == ArtifactLifecycleState.ACCEPTED

        # Transition to PROCESSING
        up2 = repo.update_artifact_state(saved.artifact_id, tenant_id="tenant_1", lifecycle_state=ArtifactLifecycleState.PROCESSING)
        assert up2.lifecycle_state == ArtifactLifecycleState.PROCESSING

        # Transition to PROCESSED
        up3 = repo.update_artifact_state(saved.artifact_id, tenant_id="tenant_1", lifecycle_state=ArtifactLifecycleState.PROCESSED)
        assert up3.lifecycle_state == ArtifactLifecycleState.PROCESSED

    def test_tenant_storage_purge(self):
        storage = InMemoryStorageService()
        storage.store("tenant_purge", "art_1", b"data1")
        storage.store("tenant_purge", "art_2", b"data2")
        storage.store("tenant_keep", "art_3", b"data3")

        purged_count = storage.purge_tenant("tenant_purge")
        assert purged_count == 2
        assert not storage.exists("tenant_purge", "art_1")
        assert storage.exists("tenant_keep", "art_3")
