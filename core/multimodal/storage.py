"""M57 — Multimodal Storage Abstraction & Implementations.

Provides tenant-sandboxed, path-traversal-proof binary object storage abstractions
for local disk and in-memory test environments.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger("aura.multimodal.storage")


class IObjectStorageService(ABC):
    """Abstract contract for tenant-isolated binary artifact storage."""

    @abstractmethod
    def store(self, tenant_id: str, artifact_id: str, data: bytes, format_ext: str = "bin") -> str:
        """Store binary payload for a tenant and return canonical storage URI."""
        pass

    @abstractmethod
    def retrieve(self, tenant_id: str, artifact_id: str) -> bytes | None:
        """Retrieve binary payload for a specific tenant. Returns None if not found or unauthorized."""
        pass

    @abstractmethod
    def delete(self, tenant_id: str, artifact_id: str) -> bool:
        """Permanently delete stored artifact for a specific tenant."""
        pass

    @abstractmethod
    def exists(self, tenant_id: str, artifact_id: str) -> bool:
        """Check if an artifact binary exists for a tenant."""
        pass

    @abstractmethod
    def purge_tenant(self, tenant_id: str) -> int:
        """Purge all stored objects for a tenant (GDPR hard delete). Returns count of deleted objects."""
        pass


def _sanitize_path_component(component: str) -> str:
    """Sanitize path component to prevent path traversal (../, absolute paths, backslashes)."""
    raw = str(component).strip()
    if not raw or ".." in raw or "/" in raw or "\\" in raw or "%" in raw:
        raise ValueError(f"Invalid path component: '{component}'")
    clean = os.path.basename(raw)
    clean = "".join(c for c in clean if c.isalnum() or c in ("-", "_", "."))
    if not clean or clean in (".", ".."):
        raise ValueError(f"Invalid path component: '{component}'")
    return clean


class LocalStorageService(IObjectStorageService):
    """Secure local filesystem object storage with strict tenant path sandboxing."""

    def __init__(self, root_dir: str = "data/multimodal_storage"):
        self.root_dir = Path(root_dir).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _get_tenant_dir(self, tenant_id: str) -> Path:
        safe_tenant = _sanitize_path_component(tenant_id)
        tenant_dir = (self.root_dir / safe_tenant).resolve()
        # Enforce sandbox invariant (Invariant M57-F35)
        if not str(tenant_dir).startswith(str(self.root_dir)):
            raise PermissionError("Path traversal attempt detected in tenant directory.")
        tenant_dir.mkdir(parents=True, exist_ok=True)
        return tenant_dir

    def _get_artifact_path(self, tenant_id: str, artifact_id: str, ext: str = "bin") -> Path:
        tenant_dir = self._get_tenant_dir(tenant_id)
        safe_artifact = _sanitize_path_component(artifact_id)
        safe_ext = _sanitize_path_component(ext).lstrip(".")
        filename = f"{safe_artifact}.{safe_ext}" if safe_ext else safe_artifact
        target = (tenant_dir / filename).resolve()
        if not str(target).startswith(str(tenant_dir)):
            raise PermissionError("Path traversal attempt detected in artifact target path.")
        return target

    def store(self, tenant_id: str, artifact_id: str, data: bytes, format_ext: str = "bin") -> str:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes or bytearray.")
        with self._lock:
            path = self._get_artifact_path(tenant_id, artifact_id, format_ext)
            path.write_bytes(bytes(data))
            return f"file://{path.as_posix()}"

    def retrieve(self, tenant_id: str, artifact_id: str) -> bytes | None:
        with self._lock:
            try:
                tenant_dir = self._get_tenant_dir(tenant_id)
                safe_artifact = _sanitize_path_component(artifact_id)
                for f in tenant_dir.glob(f"{safe_artifact}.*"):
                    if f.is_file():
                        return f.read_bytes()
                target = tenant_dir / safe_artifact
                if target.is_file():
                    return target.read_bytes()
            except Exception as e:
                logger.warning(f"Failed to retrieve artifact {artifact_id} for tenant {tenant_id}: {e}")
                return None
            return None

    def delete(self, tenant_id: str, artifact_id: str) -> bool:
        with self._lock:
            try:
                tenant_dir = self._get_tenant_dir(tenant_id)
                safe_artifact = _sanitize_path_component(artifact_id)
                deleted = False
                for f in tenant_dir.glob(f"{safe_artifact}*"):
                    if f.is_file():
                        f.unlink()
                        deleted = True
                return deleted
            except Exception as e:
                logger.warning(f"Error deleting artifact {artifact_id} for tenant {tenant_id}: {e}")
                return False

    def exists(self, tenant_id: str, artifact_id: str) -> bool:
        with self._lock:
            try:
                tenant_dir = self._get_tenant_dir(tenant_id)
                safe_artifact = _sanitize_path_component(artifact_id)
                for f in tenant_dir.glob(f"{safe_artifact}*"):
                    if f.is_file():
                        return True
            except Exception:
                return False
            return False

    def purge_tenant(self, tenant_id: str) -> int:
        with self._lock:
            try:
                tenant_dir = self._get_tenant_dir(tenant_id)
                if not tenant_dir.exists():
                    return 0
                count = sum(1 for f in tenant_dir.iterdir() if f.is_file())
                shutil.rmtree(tenant_dir, ignore_errors=True)
                return count
            except Exception as e:
                logger.error(f"Error purging storage for tenant {tenant_id}: {e}")
                return 0


class InMemoryStorageService(IObjectStorageService):
    """Thread-safe in-memory object storage for testing and ephemeral environments."""

    def __init__(self):
        self._lock = threading.RLock()
        self._storage: dict[str, dict[str, bytes]] = {}

    def store(self, tenant_id: str, artifact_id: str, data: bytes, format_ext: str = "bin") -> str:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes.")
        with self._lock:
            if tenant_id not in self._storage:
                self._storage[tenant_id] = {}
            self._storage[tenant_id][artifact_id] = bytes(data)
            return f"mem://{tenant_id}/{artifact_id}"

    def retrieve(self, tenant_id: str, artifact_id: str) -> bytes | None:
        with self._lock:
            return self._storage.get(tenant_id, {}).get(artifact_id)

    def delete(self, tenant_id: str, artifact_id: str) -> bool:
        with self._lock:
            if tenant_id in self._storage and artifact_id in self._storage[tenant_id]:
                del self._storage[tenant_id][artifact_id]
                return True
            return False

    def exists(self, tenant_id: str, artifact_id: str) -> bool:
        with self._lock:
            return artifact_id in self._storage.get(tenant_id, {})

    def purge_tenant(self, tenant_id: str) -> int:
        with self._lock:
            if tenant_id in self._storage:
                count = len(self._storage[tenant_id])
                del self._storage[tenant_id]
                return count
            return 0
