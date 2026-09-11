"""Unit tests for M24 Artifact Manager, Lineage & Versioning Subsystem."""

import pytest
from core.artifact_manager import ArtifactManager, ArtifactLineage
from core.artifact_store import InMemoryArtifactStore
from core.artifact_types import ArtifactType
from core.provenance import wrap_tainted


def test_artifact_manager_store_and_versioning():
    manager = ArtifactManager(store=InMemoryArtifactStore())

    # Create v1
    art_v1 = manager.store_artifact(
        name="report.md",
        content="# Section 1\nInitial overview.",
        artifact_type=ArtifactType.REPORT,
        creator_role_id="analyst",
        metadata={"category": "market"},
    )

    assert art_v1.version == 1
    assert art_v1.name == "report.md"

    # Create v2
    art_v2 = manager.create_next_version(
        artifact_id=art_v1.artifact_id,
        content="# Section 1\nInitial overview.\n# Section 2\nAdvanced insights.",
        updater_role_id="senior_analyst",
        metadata={"category": "market_final"},
    )

    assert art_v2.version == 2
    assert art_v2.artifact_id == art_v1.artifact_id
    assert art_v1.artifact_id in art_v2.lineage_parent_ids

    # Verify contents
    c1 = manager.get_artifact_content(art_v1.artifact_id, version=1)
    c2 = manager.get_artifact_content(art_v1.artifact_id, version=2)
    assert "Section 2" not in c1
    assert "Section 2" in c2

    # Diff
    diff = manager.diff_artifacts(art_v1.artifact_id, 1, 2)
    assert not diff["same_content"]
    assert len(diff["diff_lines"]) > 0


def test_artifact_lineage_and_taint_propagation():
    manager = ArtifactManager(store=InMemoryArtifactStore())

    # Parent 1: untrusted web scrape (tainted)
    untrusted_payload = wrap_tainted("Untrusted web snippet", is_untrusted=True, source_type="web_search")
    art_raw = manager.store_artifact(
        name="raw_scrape.txt",
        content=untrusted_payload,
        artifact_type=ArtifactType.DOCUMENT,
        creator_role_id="web_crawler",
    )
    assert art_raw.taint_status is True

    # Child 1: derived analysis citing raw_scrape
    art_analysis = manager.store_artifact(
        name="analysis.md",
        content="# Synthesized Analysis",
        artifact_type=ArtifactType.REPORT,
        creator_role_id="researcher",
        parent_artifact_ids=[art_raw.artifact_id],
    )
    # Taint MUST be inherited from tainted parent
    assert art_analysis.taint_status is True

    # Lineage DAG inspection
    lineage = manager.get_lineage(art_analysis.artifact_id)
    assert lineage.is_tainted is True
    assert lineage.depth == 2
    assert not lineage.has_cycles()
    assert art_raw.artifact_id in lineage.nodes


def test_artifact_json_content_decoding():
    manager = ArtifactManager(store=InMemoryArtifactStore())
    dataset = {"users": [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]}

    art = manager.store_artifact(
        name="users.json",
        content=dataset,
        artifact_type=ArtifactType.DATASET,
    )

    retrieved = manager.get_artifact_content(art.artifact_id)
    assert isinstance(retrieved, dict)
    assert len(retrieved["users"]) == 2
