"""Durable Artifact Storage & Content-Addressable Storage (CAS) Engine (M24).

Implements abstract storage protocol, thread-safe InMemoryArtifactStore, and disk-backed
FileWorkspaceArtifactStore using Content-Addressable Storage with SHA-256 deduplication,
corruption detection, and atomic persistence.
"""

from __future__ import annotations

import abc
import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from core.artifact_types import (
    MAX_ARTIFACT_SIZE_BYTES,
    Artifact,
    ArtifactType,
    compute_content_hash,
)

logger = logging.getLogger("aura.artifact_store")


class ArtifactStore(abc.ABC):
    """Abstract interface for storing and retrieving artifact blobs and manifests."""

    @abc.abstractmethod
    def store_blob(self, content_hash: str, content: bytes | str) -> str:
        """Store raw content blob addressed by its SHA-256 hash. Returns storage URI."""
        raise NotImplementedError

    @abc.abstractmethod
    def get_blob(self, content_hash: str) -> bytes | None:
        """Retrieve raw content bytes by SHA-256 hash."""
        raise NotImplementedError

    @abc.abstractmethod
    def has_blob(self, content_hash: str) -> bool:
        """Check if content hash exists in storage."""
        raise NotImplementedError

    @abc.abstractmethod
    def store_manifest(self, artifact: Artifact) -> None:
        """Store an artifact version manifest."""
        raise NotImplementedError

    @abc.abstractmethod
    def get_manifest(self, artifact_id: str, version: int | None = None) -> Artifact | None:
        """Get an artifact manifest by ID and optional version (defaults to latest)."""
        raise NotImplementedError

    @abc.abstractmethod
    def list_manifests(
        self,
        session_id: str | None = None,
        goal_id: str | None = None,
        artifact_type: ArtifactType | None = None,
    ) -> list[Artifact]:
        """List latest manifests matching optional filter criteria."""
        raise NotImplementedError

    @abc.abstractmethod
    def delete_artifact(self, artifact_id: str) -> bool:
        """Delete an artifact and all its version manifests."""
        raise NotImplementedError


class InMemoryArtifactStore(ArtifactStore):
    """Thread-safe in-memory dictionary-backed artifact store."""

    def __init__(self):
        self._blobs: dict[str, bytes] = {}
        self._manifests: dict[str, dict[int, Artifact]] = defaultdict(dict)
        self._lock = threading.RLock()

    def store_blob(self, content_hash: str, content: bytes | str) -> str:
        clean_hash = str(content_hash).strip().lower()
        raw_bytes = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        with self._lock:
            self._blobs[clean_hash] = raw_bytes
        return f"memory://blobs/{clean_hash}"

    def get_blob(self, content_hash: str) -> bytes | None:
        clean_hash = str(content_hash).strip().lower()
        with self._lock:
            return self._blobs.get(clean_hash)

    def has_blob(self, content_hash: str) -> bool:
        clean_hash = str(content_hash).strip().lower()
        with self._lock:
            return clean_hash in self._blobs

    def store_manifest(self, artifact: Artifact) -> None:
        if not isinstance(artifact, Artifact):
            raise TypeError("artifact must be an Artifact instance.")
        with self._lock:
            self._manifests[artifact.artifact_id][artifact.version] = artifact

    def get_manifest(self, artifact_id: str, version: int | None = None) -> Artifact | None:
        aid = str(artifact_id).strip()
        with self._lock:
            versions = self._manifests.get(aid)
            if not versions:
                return None
            if version is not None:
                return versions.get(int(version))
            # Return highest version
            max_ver = max(versions.keys())
            return versions[max_ver]

    def list_manifests(
        self,
        session_id: str | None = None,
        goal_id: str | None = None,
        artifact_type: ArtifactType | None = None,
    ) -> list[Artifact]:
        with self._lock:
            results: list[Artifact] = []
            for versions in self._manifests.values():
                if not versions:
                    continue
                latest = versions[max(versions.keys())]
                if session_id is not None and latest.session_id != str(session_id).strip():
                    continue
                if goal_id is not None and latest.producer_goal_id != str(goal_id).strip():
                    continue
                if artifact_type is not None and latest.artifact_type != artifact_type:
                    continue
                results.append(latest)
            return sorted(results, key=lambda a: a.created_at)

    def delete_artifact(self, artifact_id: str) -> bool:
        aid = str(artifact_id).strip()
        with self._lock:
            if aid in self._manifests:
                del self._manifests[aid]
                return True
            return False


class FileWorkspaceArtifactStore(ArtifactStore):
    """Content-Addressable Storage (CAS) with atomic disk persistence and SHA-256 verification."""

    def __init__(self, base_dir: str | Path = ".aura_artifacts"):
        self.base_dir = Path(base_dir).resolve()
        self.blobs_dir = self.base_dir / "blobs"
        self.manifests_dir = self.base_dir / "manifests"
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self.manifests_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _blob_path(self, content_hash: str) -> Path:
        """Derive sharded CAS path for a blob hash."""
        clean = str(content_hash).strip().lower()
        if len(clean) != 64 or not all(c in "0123456789abcdef" for c in clean):
            raise ValueError("Invalid SHA-256 hash format.")
        shard1 = clean[:2]
        shard2 = clean[2:4]
        target_dir = self.blobs_dir / shard1 / shard2
        target_path = (target_dir / clean).resolve()
        # Security check: verify no path traversal
        if not str(target_path).startswith(str(self.base_dir)):
            raise ValueError("Path traversal violation detected.")
        return target_path

    def _manifest_dir(self, artifact_id: str) -> Path:
        clean_id = str(artifact_id).strip()
        # Sanitize artifact_id to alphanumeric and dashes/underscores
        safe_id = "".join(c for c in clean_id if c.isalnum() or c in ("-", "_"))
        if not safe_id:
            safe_id = hashlib.sha256(clean_id.encode("utf-8")).hexdigest()[:16]
        target_dir = (self.manifests_dir / safe_id).resolve()
        if not str(target_dir).startswith(str(self.base_dir)):
            raise ValueError("Path traversal violation detected.")
        return target_dir

    def store_blob(self, content_hash: str, content: bytes | str) -> str:
        clean_hash = str(content_hash).strip().lower()
        raw_bytes = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        if len(raw_bytes) > MAX_ARTIFACT_SIZE_BYTES:
            raise ValueError(f"Content exceeds maximum artifact size ({MAX_ARTIFACT_SIZE_BYTES} bytes).")

        target_path = self._blob_path(clean_hash)
        with self._lock:
            if not target_path.exists():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                temp_file = tempfile.NamedTemporaryFile(
                    "wb",
                    dir=str(target_path.parent),
                    delete=False,
                )
                try:
                    temp_file.write(raw_bytes)
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
                    temp_file.close()
                    shutil.move(temp_file.name, str(target_path))
                except Exception:
                    try:
                        temp_file.close()
                    except Exception:
                        pass
                    if os.path.exists(temp_file.name):
                        try:
                            os.remove(temp_file.name)
                        except Exception:
                            pass
                    raise
        return f"file://{target_path.as_posix()}"

    def get_blob(self, content_hash: str) -> bytes | None:
        clean_hash = str(content_hash).strip().lower()
        try:
            target_path = self._blob_path(clean_hash)
        except ValueError:
            return None

        with self._lock:
            if not target_path.exists() or not target_path.is_file():
                return None
            try:
                with open(target_path, "rb") as f:
                    data = f.read()
                # Verify SHA-256 integrity (corruption detection)
                computed = hashlib.sha256(data).hexdigest()
                if computed != clean_hash:
                    logger.error("Corruption detected in blob %s: expected %s, got %s", target_path, clean_hash, computed)
                    return None
                return data
            except Exception as e:
                logger.error("Failed to read blob %s: %s", target_path, e)
                return None

    def has_blob(self, content_hash: str) -> bool:
        clean_hash = str(content_hash).strip().lower()
        try:
            target_path = self._blob_path(clean_hash)
            return target_path.exists() and target_path.is_file()
        except ValueError:
            return False

    def store_manifest(self, artifact: Artifact) -> None:
        if not isinstance(artifact, Artifact):
            raise TypeError("artifact must be an Artifact instance.")

        art_dir = self._manifest_dir(artifact.artifact_id)
        with self._lock:
            art_dir.mkdir(parents=True, exist_ok=True)
            ver_file = art_dir / f"v{artifact.version}.json"
            latest_file = art_dir / "latest.json"

            data = artifact.to_dict()
            # Atomic write version manifest
            temp_v = tempfile.NamedTemporaryFile("w", dir=str(art_dir), delete=False, encoding="utf-8")
            json.dump(data, temp_v, indent=2)
            temp_v.flush()
            temp_v.close()
            shutil.move(temp_v.name, str(ver_file))

            # Atomic write latest manifest pointer
            temp_l = tempfile.NamedTemporaryFile("w", dir=str(art_dir), delete=False, encoding="utf-8")
            json.dump(data, temp_l, indent=2)
            temp_l.flush()
            temp_l.close()
            shutil.move(temp_l.name, str(latest_file))

    def get_manifest(self, artifact_id: str, version: int | None = None) -> Artifact | None:
        try:
            art_dir = self._manifest_dir(artifact_id)
        except ValueError:
            return None

        with self._lock:
            if not art_dir.exists():
                return None
            if version is not None:
                target_file = art_dir / f"v{int(version)}.json"
            else:
                target_file = art_dir / "latest.json"

            if not target_file.exists():
                return None
            try:
                with open(target_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return Artifact.from_dict(data)
            except Exception as e:
                logger.error("Failed to load artifact manifest from %s: %s", target_file, e)
                return None

    def list_manifests(
        self,
        session_id: str | None = None,
        goal_id: str | None = None,
        artifact_type: ArtifactType | None = None,
    ) -> list[Artifact]:
        results: list[Artifact] = []
        with self._lock:
            if not self.manifests_dir.exists():
                return []
            for art_dir in self.manifests_dir.iterdir():
                if not art_dir.is_dir():
                    continue
                latest_file = art_dir / "latest.json"
                if not latest_file.exists():
                    continue
                try:
                    with open(latest_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    art = Artifact.from_dict(data)
                    if session_id is not None and art.session_id != str(session_id).strip():
                        continue
                    if goal_id is not None and art.producer_goal_id != str(goal_id).strip():
                        continue
                    if artifact_type is not None and art.artifact_type != artifact_type:
                        continue
                    results.append(art)
                except Exception as e:
                    logger.warning("Error reading manifest %s: %s", latest_file, e)
        return sorted(results, key=lambda a: a.created_at)

    def delete_artifact(self, artifact_id: str) -> bool:
        try:
            art_dir = self._manifest_dir(artifact_id)
        except ValueError:
            return False

        with self._lock:
            if art_dir.exists():
                try:
                    shutil.rmtree(str(art_dir))
                    return True
                except Exception as e:
                    logger.error("Failed to delete artifact dir %s: %s", art_dir, e)
                    return False
            return False
