import json
import logging
import os
import shutil
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.session_types import (
    SessionContext,
    SessionMetadata,
    SessionStatus,
    validate_session_id,
)

logger = logging.getLogger("aura.session_store")


class SessionStore(ABC):
    """Abstract base class for persistent and in-memory multi-session state stores."""

    @abstractmethod
    def save(self, context: SessionContext) -> None:
        """Persist or update a session context."""
        pass

    @abstractmethod
    def get(self, session_id: str) -> SessionContext | None:
        """Retrieve a session context by its identifier."""
        pass

    @abstractmethod
    def delete(self, session_id: str) -> bool:
        """Delete a session context by its identifier."""
        pass

    @abstractmethod
    def list_sessions(
        self,
        user_id: str | None = None,
        active_only: bool = False,
    ) -> list[SessionMetadata]:
        """List metadata for all known sessions, with optional user and status filtering."""
        pass

    @abstractmethod
    def prune_expired_sessions(self, current_time: float | None = None) -> int:
        """Transition or delete expired sessions and return the count of pruned sessions."""
        pass

    @abstractmethod
    def count(self) -> int:
        """Return the total number of stored sessions."""
        pass


class InMemorySessionStore(SessionStore):
    """Thread-safe, volatile in-memory session store."""

    def __init__(self):
        self._lock = threading.RLock()
        self._sessions: dict[str, SessionContext] = {}

    def save(self, context: SessionContext) -> None:
        """Save session context in memory."""
        if not isinstance(context, SessionContext):
            raise TypeError("context must be an instance of SessionContext.")
        with self._lock:
            # Store a roundtripped copy to prevent accidental external in-place mutations
            serialized = context.to_dict()
            self._sessions[context.session_id] = SessionContext.from_dict(serialized)

    def get(self, session_id: str) -> SessionContext | None:
        """Retrieve a copy of the session context."""
        clean_id = validate_session_id(session_id)
        with self._lock:
            ctx = self._sessions.get(clean_id)
            if ctx is None:
                return None
            return SessionContext.from_dict(ctx.to_dict())

    def delete(self, session_id: str) -> bool:
        """Delete a session context."""
        clean_id = validate_session_id(session_id)
        with self._lock:
            if clean_id in self._sessions:
                del self._sessions[clean_id]
                return True
            return False

    def list_sessions(
        self,
        user_id: str | None = None,
        active_only: bool = False,
    ) -> list[SessionMetadata]:
        """List metadata for stored sessions."""
        clean_uid = user_id.strip() if user_id is not None else None
        with self._lock:
            results: list[SessionMetadata] = []
            for ctx in self._sessions.values():
                meta = ctx.metadata
                if clean_uid is not None and meta.user_id != clean_uid:
                    continue
                if active_only and meta.status not in (SessionStatus.ACTIVE, SessionStatus.IDLE):
                    continue
                results.append(SessionMetadata.from_dict(meta.to_dict()))
            return sorted(results, key=lambda m: m.last_accessed_at, reverse=True)

    def prune_expired_sessions(self, current_time: float | None = None) -> int:
        """Mark expired sessions as EXPIRED."""
        now = current_time if current_time is not None else time.time()
        pruned_count = 0
        with self._lock:
            for sid, ctx in list(self._sessions.items()):
                if ctx.status in (SessionStatus.ACTIVE, SessionStatus.IDLE) and ctx.is_expired(now):
                    ctx.metadata.status = SessionStatus.EXPIRED
                    pruned_count += 1
        return pruned_count

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)


class FileSessionStore(SessionStore):
    """Crash-resilient, atomic file-backed session store."""

    def __init__(self, storage_dir: str | Path):
        self.storage_dir = Path(storage_dir).resolve()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _session_file(self, session_id: str) -> Path:
        clean_id = validate_session_id(session_id)
        return self.storage_dir / f"{clean_id}.json"

    def save(self, context: SessionContext) -> None:
        """Persist session context atomically using write, flush, fsync, and replace."""
        if not isinstance(context, SessionContext):
            raise TypeError("context must be an instance of SessionContext.")

        target_file = self._session_file(context.session_id)
        data = context.to_dict()
        serialized = json.dumps(data, indent=2, sort_keys=True)

        with self._lock:
            # Atomic temp file write
            temp_file = target_file.with_suffix(f".tmp.{uuid4().hex}")
            try:
                with open(temp_file, "w", encoding="utf-8") as f:
                    f.write(serialized)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_file, target_file)
            except Exception as e:
                logger.error("Failed to atomically save session '%s': %s", context.session_id, e)
                if temp_file.exists():
                    try:
                        temp_file.unlink()
                    except Exception:
                        pass
                raise

    def get(self, session_id: str) -> SessionContext | None:
        """Read and reconstruct a session context from disk with corruption resilience."""
        target_file = self._session_file(session_id)
        with self._lock:
            if not target_file.exists() or not target_file.is_file():
                return None

            try:
                raw_text = target_file.read_text(encoding="utf-8")
                if not raw_text.strip():
                    logger.warning("Empty session file encountered for '%s'", session_id)
                    return None
                data = json.loads(raw_text)
                return SessionContext.from_dict(data)
            except Exception as e:
                logger.error("Corrupted session file detected for '%s': %s", session_id, e)
                # Quarantine corrupted file to avoid repeated crash
                quarantine = target_file.with_suffix(f".corrupted.{uuid4().hex}")
                try:
                    os.replace(target_file, quarantine)
                    logger.info("Quarantined corrupted session file to '%s'", quarantine)
                except Exception:
                    pass
                return None

    def delete(self, session_id: str) -> bool:
        """Delete a session file from disk."""
        target_file = self._session_file(session_id)
        with self._lock:
            if target_file.exists():
                try:
                    target_file.unlink()
                    return True
                except Exception as e:
                    logger.error("Failed to delete session file '%s': %s", target_file, e)
                    return False
            return False

    def list_sessions(
        self,
        user_id: str | None = None,
        active_only: bool = False,
    ) -> list[SessionMetadata]:
        """List metadata for all valid session files in the storage directory."""
        clean_uid = user_id.strip() if user_id is not None else None
        results: list[SessionMetadata] = []

        with self._lock:
            if not self.storage_dir.exists():
                return []

            for path in self.storage_dir.glob("*.json"):
                if path.name.endswith(".tmp") or ".corrupted" in path.name:
                    continue
                try:
                    raw_text = path.read_text(encoding="utf-8")
                    if not raw_text.strip():
                        continue
                    data = json.loads(raw_text)
                    meta_raw = data.get("metadata", {})
                    meta = SessionMetadata.from_dict(meta_raw)
                    if clean_uid is not None and meta.user_id != clean_uid:
                        continue
                    if active_only and meta.status not in (SessionStatus.ACTIVE, SessionStatus.IDLE):
                        continue
                    results.append(meta)
                except Exception as e:
                    logger.warning("Skipping unparseable session file '%s': %s", path, e)
                    continue

            return sorted(results, key=lambda m: m.last_accessed_at, reverse=True)

    def prune_expired_sessions(self, current_time: float | None = None) -> int:
        """Identify and transition expired session files to EXPIRED status."""
        now = current_time if current_time is not None else time.time()
        pruned_count = 0

        with self._lock:
            if not self.storage_dir.exists():
                return 0

            for path in self.storage_dir.glob("*.json"):
                if path.name.endswith(".tmp") or ".corrupted" in path.name:
                    continue
                try:
                    raw_text = path.read_text(encoding="utf-8")
                    if not raw_text.strip():
                        continue
                    data = json.loads(raw_text)
                    meta_raw = data.get("metadata", {})
                    meta = SessionMetadata.from_dict(meta_raw)
                    if meta.status in (SessionStatus.ACTIVE, SessionStatus.IDLE) and meta.is_expired(now):
                        ctx = SessionContext.from_dict(data)
                        ctx.metadata.status = SessionStatus.EXPIRED
                        self.save(ctx)
                        pruned_count += 1
                except Exception as e:
                    logger.warning("Error inspecting session during prune '%s': %s", path, e)
                    continue

        return pruned_count

    def count(self) -> int:
        with self._lock:
            if not self.storage_dir.exists():
                return 0
            return len([p for p in self.storage_dir.glob("*.json") if not p.name.endswith(".tmp") and ".corrupted" not in p.name])
