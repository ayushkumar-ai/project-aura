"""Durable Artifact Lifecycle, Versioning & Lineage Management Engine (M24).

Coordinates high-level artifact versioning, content retrieval, derivation lineage DAGs,
taint propagation, diffing, and checkpoint manifest exports.
"""

from __future__ import annotations

import difflib
import json
import logging
import threading
from collections import defaultdict, deque
from typing import Any, Sequence
from uuid import uuid4

from core.artifact_store import ArtifactStore, InMemoryArtifactStore
from core.artifact_types import (
    MAX_LINEAGE_DEPTH,
    MAX_LINEAGE_PARENTS,
    Artifact,
    ArtifactType,
    compute_content_hash,
    infer_mime_type,
)
from core.provenance import TaintedValue, is_tainted, unwrap_tainted, wrap_tainted

logger = logging.getLogger("aura.artifact_manager")


class ArtifactLineage:
    """Directed Acyclic Graph (DAG) tracking the derivation history of an artifact."""

    def __init__(self, target_artifact_id: str, nodes: dict[str, Artifact]):
        self.target_artifact_id = target_artifact_id
        self.nodes = dict(nodes)
        self.parent_map: dict[str, list[str]] = defaultdict(list)
        self.child_map: dict[str, list[str]] = defaultdict(list)

        self._build_graph()

    def _build_graph(self) -> None:
        for aid, art in self.nodes.items():
            for pid in art.lineage_parent_ids:
                if pid in self.nodes:
                    self.parent_map[aid].append(pid)
                    self.child_map[pid].append(aid)

    @property
    def is_tainted(self) -> bool:
        """True if any artifact in the lineage graph is marked with taint_status."""
        return any(a.taint_status for a in self.nodes.values())

    @property
    def depth(self) -> int:
        """Maximum derivation depth from roots to the target artifact."""
        if self.target_artifact_id not in self.nodes:
            return 0

        visited: set[str] = set()

        def compute_depth(curr: str, current_depth: int) -> int:
            if curr in visited or current_depth > MAX_LINEAGE_DEPTH:
                return current_depth
            visited.add(curr)
            parents = self.parent_map.get(curr, [])
            if not parents:
                return current_depth
            return max(compute_depth(p, current_depth + 1) for p in parents)

        return compute_depth(self.target_artifact_id, 1)

    def has_cycles(self) -> bool:
        """Detect circular lineage dependencies."""
        visited: set[str] = set()
        rec_stack: set[str] = set()

        def is_cyclic(curr: str) -> bool:
            visited.add(curr)
            rec_stack.add(curr)
            for parent in self.parent_map.get(curr, []):
                if parent not in visited:
                    if is_cyclic(parent):
                        return True
                elif parent in rec_stack:
                    return True
            rec_stack.remove(curr)
            return False

        for node_id in self.nodes:
            if node_id not in visited:
                if is_cyclic(node_id):
                    return True
        return False

    def to_dict(self) -> dict[str, Any]:
        """Serialize lineage graph summary."""
        return {
            "target_artifact_id": self.target_artifact_id,
            "node_count": len(self.nodes),
            "nodes": {aid: a.to_dict() for aid, a in self.nodes.items()},
            "is_tainted": self.is_tainted,
            "depth": self.depth,
            "has_cycles": self.has_cycles(),
        }


class ArtifactManager:
    """High-level lifecycle coordinator for durable versioned deliverables."""

    def __init__(
        self,
        store: ArtifactStore | None = None,
        tracer: Any | None = None,
    ):
        self.store = store if store is not None else InMemoryArtifactStore()
        self.tracer = tracer
        self._lock = threading.RLock()

    def store_artifact(
        self,
        name: str,
        content: Any,
        artifact_type: ArtifactType | str = ArtifactType.DOCUMENT,
        session_id: str | None = None,
        creator_role_id: str | None = None,
        producer_goal_id: str | None = None,
        producer_task_id: str | None = None,
        parent_artifact_ids: Sequence[str] = (),
        metadata: dict[str, Any] | None = None,
        taint_status: bool | None = None,
    ) -> Artifact:
        """Store a new version 1 artifact deliverable."""
        eff_type = ArtifactType(artifact_type) if isinstance(artifact_type, str) else artifact_type

        # Check content taint
        is_content_tainted = isinstance(content, TaintedValue) or (
            hasattr(content, "is_untrusted") and getattr(content, "is_untrusted") is True
        )
        raw_content = unwrap_tainted(content) if isinstance(content, TaintedValue) else content

        # Check parent lineage taint inheritance
        parents = list(parent_artifact_ids or [])
        inherited_taint = False
        for pid in parents:
            parent_art = self.store.get_manifest(pid)
            if parent_art and parent_art.taint_status:
                inherited_taint = True
                break

        effective_taint = (
            bool(taint_status)
            if taint_status is not None
            else (is_content_tainted or inherited_taint)
        )

        # Convert content to bytes / str for hashing and storage
        if isinstance(raw_content, bytes):
            blob_data: bytes | str = raw_content
            size_bytes = len(raw_content)
        elif isinstance(raw_content, str):
            blob_data = raw_content
            size_bytes = len(raw_content.encode("utf-8"))
        elif isinstance(raw_content, (dict, list)):
            blob_data = json.dumps(raw_content, indent=2, sort_keys=True)
            size_bytes = len(blob_data.encode("utf-8"))
        else:
            blob_data = str(raw_content)
            size_bytes = len(blob_data.encode("utf-8"))

        content_hash = compute_content_hash(blob_data)
        artifact_id = f"art_{uuid4().hex[:12]}"
        mime = infer_mime_type(name, eff_type)

        with self._lock:
            # Store in CAS
            storage_uri = self.store.store_blob(content_hash, blob_data)

            artifact = Artifact(
                artifact_id=artifact_id,
                name=name,
                artifact_type=eff_type,
                version=1,
                content_hash=content_hash,
                size_bytes=size_bytes,
                mime_type=mime,
                storage_uri=storage_uri,
                session_id=session_id,
                creator_role_id=creator_role_id,
                producer_goal_id=producer_goal_id,
                producer_task_id=producer_task_id,
                taint_status=effective_taint,
                lineage_parent_ids=tuple(parents),
                metadata=metadata or {},
            )
            self.store.store_manifest(artifact)

        logger.info(
            "Stored artifact %s (%s v1, hash=%s, size=%d bytes, tainted=%s)",
            artifact.artifact_id,
            artifact.name,
            artifact.content_hash[:8],
            artifact.size_bytes,
            artifact.taint_status,
        )
        return artifact

    def create_next_version(
        self,
        artifact_id: str,
        content: Any,
        updater_role_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        parent_artifact_ids: Sequence[str] = (),
    ) -> Artifact:
        """Create a new version of an existing artifact."""
        clean_id = str(artifact_id).strip()
        with self._lock:
            current = self.store.get_manifest(clean_id)
            if current is None:
                raise KeyError(f"Artifact '{clean_id}' not found.")

            next_version = current.version + 1

            is_content_tainted = isinstance(content, TaintedValue) or (
                hasattr(content, "is_untrusted") and getattr(content, "is_untrusted") is True
            )
            raw_content = unwrap_tainted(content) if isinstance(content, TaintedValue) else content

            parents = list(parent_artifact_ids or [])
            if clean_id not in parents:
                parents.append(clean_id)  # Auto-link previous version as parent

            inherited_taint = current.taint_status
            for pid in parents:
                part = self.store.get_manifest(pid)
                if part and part.taint_status:
                    inherited_taint = True
                    break

            effective_taint = is_content_tainted or inherited_taint

            if isinstance(raw_content, bytes):
                blob_data: bytes | str = raw_content
                size_bytes = len(raw_content)
            elif isinstance(raw_content, str):
                blob_data = raw_content
                size_bytes = len(raw_content.encode("utf-8"))
            elif isinstance(raw_content, (dict, list)):
                blob_data = json.dumps(raw_content, indent=2, sort_keys=True)
                size_bytes = len(blob_data.encode("utf-8"))
            else:
                blob_data = str(raw_content)
                size_bytes = len(blob_data.encode("utf-8"))

            content_hash = compute_content_hash(blob_data)
            storage_uri = self.store.store_blob(content_hash, blob_data)

            merged_meta = dict(current.metadata)
            if metadata:
                merged_meta.update(metadata)

            new_art = Artifact(
                artifact_id=current.artifact_id,
                name=current.name,
                artifact_type=current.artifact_type,
                version=next_version,
                content_hash=content_hash,
                size_bytes=size_bytes,
                mime_type=current.mime_type,
                storage_uri=storage_uri,
                session_id=current.session_id,
                creator_role_id=updater_role_id or current.creator_role_id,
                producer_goal_id=current.producer_goal_id,
                producer_task_id=current.producer_task_id,
                taint_status=effective_taint,
                lineage_parent_ids=tuple(parents),
                metadata=merged_meta,
            )
            self.store.store_manifest(new_art)

        logger.info(
            "Created version %d for artifact %s (hash=%s, size=%d bytes)",
            new_art.version,
            new_art.artifact_id,
            new_art.content_hash[:8],
            new_art.size_bytes,
        )
        return new_art

    def get_artifact(self, artifact_id: str, version: int | None = None) -> Artifact | None:
        """Retrieve artifact metadata by ID and optional version."""
        return self.store.get_manifest(artifact_id, version=version)

    def read_artifact_content(self, artifact_id: str, version: int | None = None, decode_text: bool = True) -> Any:
        return self.get_artifact_content(artifact_id=artifact_id, version=version, decode_text=decode_text)

    def get_artifact_content(
        self,
        artifact_id: str,
        version: int | None = None,
        decode_text: bool = True,
    ) -> Any:
        """Retrieve and optionally decode content of an artifact."""
        art = self.store.get_manifest(artifact_id, version=version)
        if art is None:
            return None
        blob = self.store.get_blob(art.content_hash)
        if blob is None:
            return None

        if not decode_text:
            return wrap_tainted(blob, is_untrusted=True, source_type="artifact") if art.taint_status else blob

        # Attempt decoding text/json
        try:
            text = blob.decode("utf-8")
            if art.mime_type == "application/json" or art.artifact_type in (ArtifactType.DATASET, ArtifactType.SCHEMA):
                try:
                    val = json.loads(text)
                    return wrap_tainted(val, is_untrusted=True, source_type="artifact") if art.taint_status else val
                except Exception:
                    pass
            return wrap_tainted(text, is_untrusted=True, source_type="artifact") if art.taint_status else text
        except UnicodeDecodeError:
            return wrap_tainted(blob, is_untrusted=True, source_type="artifact") if art.taint_status else blob

    def list_artifacts(
        self,
        session_id: str | None = None,
        goal_id: str | None = None,
        artifact_type: ArtifactType | str | None = None,
    ) -> list[Artifact]:
        """List artifacts matching query filters."""
        eff_type = ArtifactType(artifact_type) if isinstance(artifact_type, str) else artifact_type
        return self.store.list_manifests(
            session_id=session_id,
            goal_id=goal_id,
            artifact_type=eff_type,
        )

    def get_lineage(self, artifact_id: str) -> ArtifactLineage:
        """Construct the complete derivation lineage DAG for an artifact."""
        clean_id = str(artifact_id).strip()
        nodes: dict[str, Artifact] = {}
        queue: deque[str] = deque([clean_id])
        visited: set[str] = set()

        while queue and len(visited) < 100:
            curr_id = queue.popleft()
            if curr_id in visited:
                continue
            visited.add(curr_id)

            art = self.store.get_manifest(curr_id)
            if art is not None:
                nodes[curr_id] = art
                for pid in art.lineage_parent_ids:
                    if pid not in visited:
                        queue.append(pid)

        return ArtifactLineage(target_artifact_id=clean_id, nodes=nodes)

    def diff_artifacts(self, artifact_id: str, version_a: int, version_b: int) -> dict[str, Any]:
        """Compute textual and metadata diff between two versions of an artifact."""
        art_a = self.store.get_manifest(artifact_id, version=version_a)
        art_b = self.store.get_manifest(artifact_id, version=version_b)

        if art_a is None or art_b is None:
            raise KeyError(f"One or both artifact versions ({version_a}, {version_b}) not found.")

        content_a = self.get_artifact_content(artifact_id, version=version_a, decode_text=True)
        content_b = self.get_artifact_content(artifact_id, version=version_b, decode_text=True)

        str_a = json.dumps(content_a, indent=2) if isinstance(content_a, (dict, list)) else str(content_a or "")
        str_b = json.dumps(content_b, indent=2) if isinstance(content_b, (dict, list)) else str(content_b or "")

        lines_a = str_a.splitlines(keepends=True)
        lines_b = str_b.splitlines(keepends=True)

        diff = list(difflib.unified_diff(lines_a, lines_b, fromfile=f"v{version_a}", tofile=f"v{version_b}"))

        return {
            "artifact_id": artifact_id,
            "version_a": version_a,
            "version_b": version_b,
            "hash_a": art_a.content_hash,
            "hash_b": art_b.content_hash,
            "same_content": art_a.content_hash == art_b.content_hash,
            "diff_lines": diff,
            "size_delta_bytes": art_b.size_bytes - art_a.size_bytes,
        }

    def export_manifests(self) -> dict[str, Any]:
        """Export all artifact manifests for checkpoint serialization."""
        all_arts = self.store.list_manifests()
        return {
            "artifacts": [a.to_dict() for a in all_arts],
        }

    def import_manifests(self, data: dict[str, Any]) -> None:
        """Import artifact manifests from checkpoint data."""
        if not isinstance(data, dict):
            return
        raw_list = data.get("artifacts", [])
        for item in raw_list:
            try:
                art = Artifact.from_dict(item)
                self.store.store_manifest(art)
            except Exception as e:
                logger.warning("Failed to import artifact manifest: %s", e)
