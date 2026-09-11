"""Durable Versioned Artifact Contracts & Schemas (M24).

Defines immutable, typed data contracts for versioned artifacts, Content-Addressable
Storage (CAS) metadata, MIME inference, cryptographic SHA-256 digests, and lineage.
"""

from __future__ import annotations

import copy
import hashlib
import json
import mimetypes
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence
from uuid import uuid4

from core.provenance import TaintedValue, is_tainted, unwrap_tainted, wrap_tainted

# Bounds to prevent unbounded memory or storage attacks
MAX_ARTIFACT_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
MAX_ARTIFACT_METADATA_ENTRIES = 64
MAX_LINEAGE_DEPTH = 20
MAX_LINEAGE_PARENTS = 32

FORBIDDEN_ARTIFACT_METADATA_KEYS = frozenset({
    "approved",
    "approval_status",
    "is_approved",
    "auto_approve",
    "permission",
    "authorized",
    "is_admin",
    "is_authorized",
    "bypass_policy",
    "sudo",
})


def _sanitize_artifact_value(val: Any) -> Any:
    """Sanitize metadata values preserving TaintedValue envelopes."""
    if isinstance(val, TaintedValue):
        return {
            "__tainted__": True,
            "raw_value": _sanitize_artifact_value(val.raw_value),
            "is_untrusted": val.is_untrusted,
            "source_type": val.source_type,
            "originating_step_id": val.originating_step_id,
            "source_urls": list(val.source_urls),
        }
    elif isinstance(val, (str, int, float, bool)) or val is None:
        if isinstance(val, str) and len(val) > 8192:
            return val[:8192] + "...[truncated]"
        return val
    elif isinstance(val, (list, tuple, set, frozenset)):
        return [_sanitize_artifact_value(x) for x in list(val)[:50]]
    elif isinstance(val, dict):
        cleaned: dict[str, Any] = {}
        for k, v in list(val.items())[:MAX_ARTIFACT_METADATA_ENTRIES]:
            k_str = str(k).strip()
            if k_str.lower() in FORBIDDEN_ARTIFACT_METADATA_KEYS or callable(v):
                continue
            cleaned[k_str] = _sanitize_artifact_value(v)
        return cleaned
    elif callable(val):
        return "<callable>"
    else:
        return repr(val)[:8192]


def _sanitize_artifact_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Sanitize artifact metadata dictionary."""
    if not isinstance(meta, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for k, v in list(meta.items())[:MAX_ARTIFACT_METADATA_ENTRIES]:
        k_str = str(k).strip()
        if not k_str or k_str.lower() in FORBIDDEN_ARTIFACT_METADATA_KEYS or callable(v):
            continue
        cleaned[k_str] = _sanitize_artifact_value(v)
    return cleaned


def compute_content_hash(content: str | bytes | dict | list | Any) -> str:
    """Compute a deterministic SHA-256 hash of arbitrary content."""
    if isinstance(content, TaintedValue):
        content = content.raw_value

    if isinstance(content, bytes):
        raw_bytes = content
    elif isinstance(content, str):
        raw_bytes = content.encode("utf-8")
    elif isinstance(content, (dict, list)):
        try:
            raw_bytes = json.dumps(content, sort_keys=True, ensure_ascii=True).encode("utf-8")
        except Exception:
            raw_bytes = str(content).encode("utf-8")
    else:
        raw_bytes = str(content).encode("utf-8")

    return hashlib.sha256(raw_bytes).hexdigest()


class ArtifactType(str, Enum):
    """Semantic classification of durable artifact assets."""

    DOCUMENT = "document"
    CODE = "code"
    DATASET = "dataset"
    REPORT = "report"
    SCHEMA = "schema"
    EVALUATION_EXPORT = "evaluation_export"
    VERIFICATION_PROOF = "verification_proof"
    BINARY = "binary"
    CUSTOM = "custom"


def infer_mime_type(name: str, artifact_type: ArtifactType) -> str:
    """Infer standard MIME type from filename or ArtifactType."""
    clean_name = str(name).strip()
    guessed, _ = mimetypes.guess_type(clean_name)
    if guessed:
        return guessed

    mapping = {
        ArtifactType.DOCUMENT: "text/markdown",
        ArtifactType.CODE: "text/x-python",
        ArtifactType.DATASET: "application/json",
        ArtifactType.REPORT: "text/markdown",
        ArtifactType.SCHEMA: "application/json",
        ArtifactType.EVALUATION_EXPORT: "application/json",
        ArtifactType.VERIFICATION_PROOF: "text/plain",
        ArtifactType.BINARY: "application/octet-stream",
        ArtifactType.CUSTOM: "application/json",
    }
    return mapping.get(artifact_type, "text/plain")


@dataclass(frozen=True)
class Artifact:
    """Immutable, versioned, tamper-evident deliverable produced or consumed in AURA."""

    artifact_id: str
    name: str
    artifact_type: ArtifactType
    version: int
    content_hash: str
    size_bytes: int
    mime_type: str
    storage_uri: str
    session_id: str | None = None
    creator_role_id: str | None = None
    producer_goal_id: str | None = None
    producer_task_id: str | None = None
    taint_status: bool = False
    lineage_parent_ids: tuple[str, ...] = field(default_factory=tuple)
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        aid = str(self.artifact_id).strip()
        if not aid:
            raise ValueError("artifact_id must be a non-empty string.")
        object.__setattr__(self, "artifact_id", aid)

        nm = str(self.name).strip()
        if not nm:
            raise ValueError("name must be a non-empty string.")
        object.__setattr__(self, "name", nm)

        if isinstance(self.artifact_type, str):
            object.__setattr__(self, "artifact_type", ArtifactType(self.artifact_type))
        elif not isinstance(self.artifact_type, ArtifactType):
            raise TypeError("artifact_type must be an ArtifactType instance.")

        v = int(self.version)
        if v < 1:
            raise ValueError("version must be a positive integer (>= 1).")
        object.__setattr__(self, "version", v)

        ch = str(self.content_hash).strip().lower()
        if len(ch) != 64:
            raise ValueError("content_hash must be a 64-character SHA-256 hex string.")
        object.__setattr__(self, "content_hash", ch)

        sz = int(self.size_bytes)
        if sz < 0 or sz > MAX_ARTIFACT_SIZE_BYTES:
            raise ValueError(f"size_bytes ({sz}) must be between 0 and {MAX_ARTIFACT_SIZE_BYTES}.")
        object.__setattr__(self, "size_bytes", sz)

        object.__setattr__(self, "mime_type", str(self.mime_type or "text/plain").strip())
        object.__setattr__(self, "storage_uri", str(self.storage_uri).strip())

        if self.session_id is not None:
            object.__setattr__(self, "session_id", str(self.session_id).strip() or None)
        if self.creator_role_id is not None:
            object.__setattr__(self, "creator_role_id", str(self.creator_role_id).strip() or None)
        if self.producer_goal_id is not None:
            object.__setattr__(self, "producer_goal_id", str(self.producer_goal_id).strip() or None)
        if self.producer_task_id is not None:
            object.__setattr__(self, "producer_task_id", str(self.producer_task_id).strip() or None)

        object.__setattr__(self, "taint_status", bool(self.taint_status))

        parents = []
        if isinstance(self.lineage_parent_ids, (list, tuple, set)):
            for p in self.lineage_parent_ids:
                p_str = str(p).strip()
                if p_str and p_str not in parents:
                    parents.append(p_str)
        object.__setattr__(self, "lineage_parent_ids", tuple(parents[:MAX_LINEAGE_PARENTS]))
        object.__setattr__(self, "created_at", float(self.created_at))
        object.__setattr__(self, "metadata", _sanitize_artifact_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        """Serialize artifact metadata to dictionary."""
        return {
            "artifact_id": self.artifact_id,
            "name": self.name,
            "artifact_type": self.artifact_type.value,
            "version": self.version,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
            "mime_type": self.mime_type,
            "storage_uri": self.storage_uri,
            "session_id": self.session_id,
            "creator_role_id": self.creator_role_id,
            "producer_goal_id": self.producer_goal_id,
            "producer_task_id": self.producer_task_id,
            "taint_status": self.taint_status,
            "lineage_parent_ids": list(self.lineage_parent_ids),
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Artifact:
        """Restore artifact metadata from dictionary."""
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        return cls(
            artifact_id=str(data.get("artifact_id", "")),
            name=str(data.get("name", "")),
            artifact_type=ArtifactType(data.get("artifact_type", ArtifactType.DOCUMENT.value)),
            version=int(data.get("version", 1)),
            content_hash=str(data.get("content_hash", "")),
            size_bytes=int(data.get("size_bytes", 0)),
            mime_type=str(data.get("mime_type", "text/plain")),
            storage_uri=str(data.get("storage_uri", "")),
            session_id=data.get("session_id"),
            creator_role_id=data.get("creator_role_id"),
            producer_goal_id=data.get("producer_goal_id"),
            producer_task_id=data.get("producer_task_id"),
            taint_status=bool(data.get("taint_status", False)),
            lineage_parent_ids=tuple(data.get("lineage_parent_ids", ())),
            created_at=float(data.get("created_at", time.time())),
            metadata=dict(data.get("metadata", {})),
        )
