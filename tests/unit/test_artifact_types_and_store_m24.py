"""Unit tests for M24 Artifact Types and Artifact Storage Subsystem."""

import tempfile
from pathlib import Path
import pytest
from core.artifact_types import (
    Artifact,
    ArtifactType,
    compute_content_hash,
    infer_mime_type,
)
from core.artifact_store import (
    FileWorkspaceArtifactStore,
    InMemoryArtifactStore,
)


def test_artifact_contracts_and_hashing():
    content = "# Project Proposal\nDetailed roadmap."
    h = compute_content_hash(content)
    assert len(h) == 64

    art = Artifact(
        artifact_id="art-101",
        name="proposal.md",
        artifact_type=ArtifactType.DOCUMENT,
        version=1,
        content_hash=h,
        size_bytes=len(content),
        mime_type=infer_mime_type("proposal.md", ArtifactType.DOCUMENT),
        storage_uri="memory://blobs/" + h,
        creator_role_id="lead_researcher",
        metadata={"is_admin": "true", "tag": "v1_draft"},
    )

    assert art.artifact_id == "art-101"
    assert art.version == 1
    assert art.mime_type == "text/markdown"
    assert "is_admin" not in art.metadata  # forbidden key stripped
    assert art.metadata.get("tag") == "v1_draft"

    d = art.to_dict()
    restored = Artifact.from_dict(d)
    assert restored.artifact_id == art.artifact_id
    assert restored.content_hash == art.content_hash


def test_in_memory_artifact_store():
    store = InMemoryArtifactStore()
    content = b"Binary artifact payload"
    h = compute_content_hash(content)

    uri = store.store_blob(h, content)
    assert store.has_blob(h)
    assert store.get_blob(h) == content

    art = Artifact(
        artifact_id="art-202",
        name="data.bin",
        artifact_type=ArtifactType.BINARY,
        version=1,
        content_hash=h,
        size_bytes=len(content),
        mime_type="application/octet-stream",
        storage_uri=uri,
    )
    store.store_manifest(art)

    retrieved = store.get_manifest("art-202")
    assert retrieved is not None
    assert retrieved.content_hash == h
    assert len(store.list_manifests()) == 1


def test_file_workspace_cas_store():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = FileWorkspaceArtifactStore(base_dir=tmpdir)
        content = "print('Hello Autonomous World')"
        h = compute_content_hash(content)

        uri = store.store_blob(h, content)
        assert store.has_blob(h)
        assert store.get_blob(h) == content.encode("utf-8")

        art = Artifact(
            artifact_id="art-code-1",
            name="main.py",
            artifact_type=ArtifactType.CODE,
            version=1,
            content_hash=h,
            size_bytes=len(content),
            mime_type="text/x-python",
            storage_uri=uri,
            producer_goal_id="g-1",
        )
        store.store_manifest(art)

        # Version 2 update
        content_v2 = "print('Hello Autonomous World v2')"
        h2 = compute_content_hash(content_v2)
        uri2 = store.store_blob(h2, content_v2)
        art_v2 = Artifact(
            artifact_id="art-code-1",
            name="main.py",
            artifact_type=ArtifactType.CODE,
            version=2,
            content_hash=h2,
            size_bytes=len(content_v2),
            mime_type="text/x-python",
            storage_uri=uri2,
            producer_goal_id="g-1",
        )
        store.store_manifest(art_v2)

        # Retrieve latest vs specific version
        latest = store.get_manifest("art-code-1")
        assert latest is not None
        assert latest.version == 2

        v1 = store.get_manifest("art-code-1", version=1)
        assert v1 is not None
        assert v1.version == 1
        assert v1.content_hash == h


def test_file_workspace_corruption_detection():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = FileWorkspaceArtifactStore(base_dir=tmpdir)
        content = "critical safe data"
        h = compute_content_hash(content)
        store.store_blob(h, content)

        # Intentionally corrupt the file on disk
        target_path = store._blob_path(h)
        with open(target_path, "wb") as f:
            f.write(b"tampered corrupted data")

        # Reading must detect hash mismatch and return None
        data = store.get_blob(h)
        assert data is None
