import logging
import threading
import time
from typing import Any
from uuid import uuid4

from app.config import settings
from core.history import ConversationHistory
from core.session_store import InMemorySessionStore, SessionStore
from core.session_types import (
    SessionContext,
    SessionMetadata,
    SessionStatus,
    sanitize_session_metadata,
    validate_session_id,
)

logger = logging.getLogger("aura.session_manager")


class SessionManager:
    """Thread-safe multi-session coordinator with per-session isolation, locking, TTL, and lifecycle controls."""

    def __init__(
        self,
        store: SessionStore | None = None,
        default_ttl_seconds: float | None = None,
        max_active_sessions: int | None = None,
        max_history_turns: int | None = None,
    ):
        self.store: SessionStore = store if store is not None else InMemorySessionStore()
        self.default_ttl_seconds: float = (
            float(default_ttl_seconds)
            if default_ttl_seconds is not None
            else getattr(settings, "aura_session_ttl_seconds", 3600.0)
        )
        self.max_active_sessions: int = (
            int(max_active_sessions)
            if max_active_sessions is not None
            else getattr(settings, "aura_max_active_sessions", 100)
        )
        self.max_history_turns: int = (
            int(max_history_turns)
            if max_history_turns is not None
            else getattr(settings, "aura_max_session_history_turns", 100)
        )

        self._global_lock = threading.RLock()
        self._session_locks: dict[str, threading.RLock] = {}

    def _get_session_lock(self, session_id: str) -> threading.RLock:
        """Retrieve or create an RLock for a specific session."""
        with self._global_lock:
            if session_id not in self._session_locks:
                self._session_locks[session_id] = threading.RLock()
            return self._session_locks[session_id]

    def create_session(
        self,
        session_id: str | None = None,
        user_id: str = "default_user",
        metadata: dict[str, Any] | None = None,
        ttl_seconds: float | None = None,
        resource_limits: dict[str, float] | None = None,
    ) -> SessionContext:
        """Create and initialize a new isolated session context."""
        eff_id = validate_session_id(session_id if session_id is not None else str(uuid4()))
        eff_ttl = float(ttl_seconds) if ttl_seconds is not None else self.default_ttl_seconds

        with self._global_lock:
            # Check capacity
            active_sessions = self.list_sessions(active_only=True)
            if len(active_sessions) >= self.max_active_sessions:
                # Attempt to prune expired sessions first
                self.prune_expired()
                active_sessions = self.list_sessions(active_only=True)
                if len(active_sessions) >= self.max_active_sessions:
                    raise RuntimeError(
                        f"Active session capacity reached ({self.max_active_sessions}). Cannot create session '{eff_id}'."
                    )

            # Check for collision
            existing = self.store.get(eff_id)
            if existing is not None and existing.status in (SessionStatus.ACTIVE, SessionStatus.IDLE, SessionStatus.PAUSED):
                raise ValueError(f"Session with ID '{eff_id}' already exists and is active.")

            meta = SessionMetadata(
                session_id=eff_id,
                user_id=user_id,
                status=SessionStatus.ACTIVE,
                metadata=metadata or {},
                ttl_seconds=eff_ttl,
            )
            ctx = SessionContext(
                metadata=meta,
                history=ConversationHistory(),
                active_goal_ids=[],
                active_task_ids=[],
                resource_limits=resource_limits or {},
            )
            self.store.save(ctx)
            return ctx

    def get_session(self, session_id: str, touch: bool = True) -> SessionContext | None:
        """Retrieve a session by its ID, checking expiration and touching last accessed time."""
        clean_id = validate_session_id(session_id)
        lock = self._get_session_lock(clean_id)

        with lock:
            ctx = self.store.get(clean_id)
            if ctx is None:
                return None

            now = time.time()
            if ctx.is_expired(now):
                if ctx.status in (SessionStatus.ACTIVE, SessionStatus.IDLE):
                    ctx.metadata.status = SessionStatus.EXPIRED
                    self.store.save(ctx)
                return ctx

            if touch and ctx.status in (SessionStatus.ACTIVE, SessionStatus.IDLE):
                ctx.touch(now)
                self.store.save(ctx)

            return ctx

    def get_or_create_session(
        self,
        session_id: str,
        user_id: str = "default_user",
        metadata: dict[str, Any] | None = None,
    ) -> SessionContext:
        """Retrieve an existing active session or create a new one."""
        existing = self.get_session(session_id, touch=True)
        if existing is not None and existing.status in (SessionStatus.ACTIVE, SessionStatus.IDLE, SessionStatus.PAUSED):
            return existing
        return self.create_session(session_id=session_id, user_id=user_id, metadata=metadata)

    def update_session(self, context: SessionContext) -> None:
        """Update an existing session in the underlying store."""
        if not isinstance(context, SessionContext):
            raise TypeError("context must be an instance of SessionContext.")
        lock = self._get_session_lock(context.session_id)
        with lock:
            context.touch()
            self.store.save(context)

    def close_session(
        self,
        session_id: str,
        reason: str = "normal",
        status: SessionStatus | str | None = None,
    ) -> bool:
        """Mark a session as COMPLETED or TERMINATED."""
        clean_id = validate_session_id(session_id)
        lock = self._get_session_lock(clean_id)

        with lock:
            ctx = self.store.get(clean_id)
            if ctx is None:
                return False
            if status is not None:
                eff_status = SessionStatus(status) if isinstance(status, str) else status
            elif reason.lower() in ("terminate", "terminated", "abort", "aborted", "killed", "error", "failed"):
                eff_status = SessionStatus.TERMINATED
            else:
                eff_status = SessionStatus.COMPLETED

            ctx.metadata.status = eff_status
            ctx.metadata.metadata["close_reason"] = reason
            ctx.touch()
            self.store.save(ctx)
            return True

    def pause_session(self, session_id: str) -> bool:
        """Transition an active session to PAUSED state."""
        clean_id = validate_session_id(session_id)
        lock = self._get_session_lock(clean_id)

        with lock:
            ctx = self.store.get(clean_id)
            if ctx is None or ctx.status not in (SessionStatus.ACTIVE, SessionStatus.IDLE):
                return False
            ctx.metadata.status = SessionStatus.PAUSED
            ctx.touch()
            self.store.save(ctx)
            return True

    def resume_session(self, session_id: str) -> bool:
        """Resume a PAUSED session back to ACTIVE state."""
        clean_id = validate_session_id(session_id)
        lock = self._get_session_lock(clean_id)

        with lock:
            ctx = self.store.get(clean_id)
            if ctx is None or ctx.status != SessionStatus.PAUSED:
                return False
            ctx.metadata.status = SessionStatus.ACTIVE
            ctx.touch()
            self.store.save(ctx)
            return True

    def expire_session(self, session_id: str) -> bool:
        """Explicitly transition a session to EXPIRED state."""
        clean_id = validate_session_id(session_id)
        lock = self._get_session_lock(clean_id)

        with lock:
            ctx = self.store.get(clean_id)
            if ctx is None:
                return False
            ctx.metadata.status = SessionStatus.EXPIRED
            ctx.touch()
            self.store.save(ctx)
            return True

    def bind_goal(self, session_id: str, goal_id: str) -> bool:
        """Bind an active goal ID to this session context."""
        clean_id = validate_session_id(session_id)
        clean_gid = str(goal_id).strip()
        if not clean_gid:
            raise ValueError("goal_id cannot be empty.")

        lock = self._get_session_lock(clean_id)
        with lock:
            ctx = self.store.get(clean_id)
            if ctx is None or ctx.status not in (SessionStatus.ACTIVE, SessionStatus.IDLE, SessionStatus.PAUSED):
                return False
            if clean_gid not in ctx.active_goal_ids:
                ctx.active_goal_ids.append(clean_gid)
                ctx.touch()
                self.store.save(ctx)
            return True

    def unbind_goal(self, session_id: str, goal_id: str) -> bool:
        """Unbind a goal ID from this session context."""
        clean_id = validate_session_id(session_id)
        clean_gid = str(goal_id).strip()
        lock = self._get_session_lock(clean_id)
        with lock:
            ctx = self.store.get(clean_id)
            if ctx is None:
                return False
            if clean_gid in ctx.active_goal_ids:
                ctx.active_goal_ids.remove(clean_gid)
                ctx.touch()
                self.store.save(ctx)
                return True
            return False

    def bind_task(self, session_id: str, task_id: str) -> bool:
        """Bind an active task ID to this session context."""
        clean_id = validate_session_id(session_id)
        clean_tid = str(task_id).strip()
        if not clean_tid:
            raise ValueError("task_id cannot be empty.")

        lock = self._get_session_lock(clean_id)
        with lock:
            ctx = self.store.get(clean_id)
            if ctx is None or ctx.status not in (SessionStatus.ACTIVE, SessionStatus.IDLE, SessionStatus.PAUSED):
                return False
            if clean_tid not in ctx.active_task_ids:
                ctx.active_task_ids.append(clean_tid)
                ctx.touch()
                self.store.save(ctx)
            return True

    def unbind_task(self, session_id: str, task_id: str) -> bool:
        """Unbind a task ID from this session context."""
        clean_id = validate_session_id(session_id)
        clean_tid = str(task_id).strip()
        lock = self._get_session_lock(clean_id)
        with lock:
            ctx = self.store.get(clean_id)
            if ctx is None:
                return False
            if clean_tid in ctx.active_task_ids:
                ctx.active_task_ids.remove(clean_tid)
                ctx.touch()
                self.store.save(ctx)
                return True
            return False

    def add_turn(
        self,
        session_id: str,
        user_input: str,
        assistant_output: str,
        tool_name: str | None = None,
        tool_result: str | None = None,
    ) -> None:
        """Add a completed conversational turn to the session's isolated history with bounding."""
        clean_id = validate_session_id(session_id)
        lock = self._get_session_lock(clean_id)

        with lock:
            ctx = self.get_session(clean_id, touch=True)
            if ctx is None:
                ctx = self.create_session(session_id=clean_id)

            ctx.history.add_turn(
                user_input=user_input,
                assistant_output=assistant_output,
                tool_name=tool_name,
                tool_result=tool_result,
            )
            # Enforce history limit
            if len(ctx.history.turns) > self.max_history_turns:
                ctx.history.turns = ctx.history.turns[-self.max_history_turns:]

            ctx.touch()
            self.store.save(ctx)

    def get_history(self, session_id: str) -> ConversationHistory | None:
        """Retrieve conversation history for a session."""
        ctx = self.get_session(session_id, touch=False)
        if ctx is None:
            return None
        return ctx.history

    def list_sessions(
        self,
        user_id: str | None = None,
        active_only: bool = False,
    ) -> list[SessionMetadata]:
        """List metadata for sessions."""
        return self.store.list_sessions(user_id=user_id, active_only=active_only)

    def prune_expired(self, current_time: float | None = None) -> int:
        """Prune expired sessions in the underlying store."""
        return self.store.prune_expired_sessions(current_time=current_time)
