"""Immutable contracts and schemas for Autonomous Runtime Supervision and Session Checkpoints (M18).

Provides typed models for:
- DaemonStatus: Lifecycle state transitions
- SupervisorConfig: Bounded configuration parameters
- SupervisorTelemetry: Runtime diagnostics and health statistics
- CheckpointMetadata: Metadata schema for session checkpoints
- Sanitization utilities to prevent privilege escalation via checkpoints
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from core.provenance import TaintedValue, is_tainted, unwrap_tainted, wrap_tainted
from core.agent_plan import _canonical_value, _restore_value

FORBIDDEN_PRIVILEGE_KEYS: frozenset[str] = frozenset({
    "is_authorized",
    "bypass_policy",
    "skip_approval",
    "approved",
    "auto_approve",
    "permission",
    "authorized",
    "role_override",
    "system_override",
    "is_approved",
    "approval_status",
    "bypass_auth",
    "root_access",
    "grant_all",
})


def sanitize_checkpoint_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Recursively strip forbidden authorization and privilege-escalation keys from metadata for JSON serialization."""
    if not isinstance(meta, dict):
        return {}

    cleaned: dict[str, Any] = {}
    for k, v in meta.items():
        k_str = str(k).strip()
        if k_str.lower() in FORBIDDEN_PRIVILEGE_KEYS:
            continue
        if callable(v):
            continue
        if isinstance(v, dict):
            cleaned[k_str] = sanitize_checkpoint_metadata(v)
        elif isinstance(v, (list, tuple)):
            cleaned[k_str] = [
                sanitize_checkpoint_metadata(item) if isinstance(item, dict) else (str(item) if not callable(item) else "")
                for item in v
            ]
        elif isinstance(v, TaintedValue):
            cleaned[k_str] = _canonical_value(v)
        elif isinstance(v, (str, int, float, bool)) or v is None:
            cleaned[k_str] = v
        else:
            cleaned[k_str] = repr(v)
    return cleaned


def sanitize_restored_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Recursively strip forbidden authorization keys while preserving in-memory Python objects (like TaintedValue)."""
    if not isinstance(meta, dict):
        return {}

    cleaned: dict[str, Any] = {}
    for k, v in meta.items():
        k_str = str(k).strip()
        if k_str.lower() in FORBIDDEN_PRIVILEGE_KEYS:
            continue
        if callable(v):
            continue
        if isinstance(v, dict):
            cleaned[k_str] = sanitize_restored_metadata(v)
        elif isinstance(v, (list, tuple)):
            cleaned[k_str] = [
                sanitize_restored_metadata(item) if isinstance(item, dict) else (str(item) if not callable(item) else "")
                for item in v
            ]
        elif isinstance(v, TaintedValue):
            cleaned[k_str] = v
        elif isinstance(v, (str, int, float, bool)) or v is None:
            cleaned[k_str] = v
        else:
            cleaned[k_str] = repr(v)
    return cleaned


class DaemonStatus(str, Enum):
    """Lifecycle status of the autonomous background supervisor daemon."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass(frozen=True)
class SupervisorConfig:
    """Bounded configuration parameters for AutonomousSupervisor."""

    heartbeat_interval_seconds: float = 1.0
    scheduler_interval_seconds: float = 2.0
    event_interval_seconds: float = 1.0
    lock_prune_interval_seconds: float = 10.0
    clarification_interval_seconds: float = 10.0
    memory_interval_seconds: float = 300.0
    checkpoint_interval_seconds: float = 30.0
    checkpoint_dir: str = ".aura_checkpoints"
    checkpoint_retention_count: int = 5
    shutdown_timeout_seconds: float = 5.0
    max_batch_goals_per_step: int = 4
    auto_recover_on_startup: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.heartbeat_interval_seconds, (int, float)) or self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be a positive number.")
        if not isinstance(self.scheduler_interval_seconds, (int, float)) or self.scheduler_interval_seconds <= 0:
            raise ValueError("scheduler_interval_seconds must be a positive number.")
        if not isinstance(self.event_interval_seconds, (int, float)) or self.event_interval_seconds <= 0:
            raise ValueError("event_interval_seconds must be a positive number.")
        if not isinstance(self.lock_prune_interval_seconds, (int, float)) or self.lock_prune_interval_seconds <= 0:
            raise ValueError("lock_prune_interval_seconds must be a positive number.")
        if not isinstance(self.clarification_interval_seconds, (int, float)) or self.clarification_interval_seconds <= 0:
            raise ValueError("clarification_interval_seconds must be a positive number.")
        if not isinstance(self.memory_interval_seconds, (int, float)) or self.memory_interval_seconds <= 0:
            raise ValueError("memory_interval_seconds must be a positive number.")
        if not isinstance(self.checkpoint_interval_seconds, (int, float)) or self.checkpoint_interval_seconds <= 0:
            raise ValueError("checkpoint_interval_seconds must be a positive number.")
        if not isinstance(self.checkpoint_dir, str) or not self.checkpoint_dir.strip():
            raise ValueError("checkpoint_dir must be a non-empty string.")
        if not isinstance(self.checkpoint_retention_count, int) or self.checkpoint_retention_count <= 0:
            raise ValueError("checkpoint_retention_count must be a positive integer.")
        if not isinstance(self.shutdown_timeout_seconds, (int, float)) or self.shutdown_timeout_seconds <= 0:
            raise ValueError("shutdown_timeout_seconds must be a positive number.")
        if not isinstance(self.max_batch_goals_per_step, int) or self.max_batch_goals_per_step <= 0:
            raise ValueError("max_batch_goals_per_step must be a positive integer.")
        if not isinstance(self.auto_recover_on_startup, bool):
            raise TypeError("auto_recover_on_startup must be a boolean.")


@dataclass(frozen=True)
class SupervisorTelemetry:
    """Diagnostics and aggregated telemetry for AutonomousSupervisor."""

    status: DaemonStatus = DaemonStatus.STOPPED
    uptime_seconds: float = 0.0
    total_heartbeats: int = 0
    total_events_dispatched: int = 0
    total_goals_stepped: int = 0
    total_checkpoints_saved: int = 0
    total_errors: int = 0
    active_workers: int = 0
    scheduler_queue_depth: int = 0
    active_locks_count: int = 0
    pending_clarifications_count: int = 0
    budget_utilization: dict[str, Any] = field(default_factory=dict)
    last_heartbeat_timestamp: float | None = None
    last_checkpoint_timestamp: float | None = None
    last_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CheckpointMetadata:
    """Summary metadata record for a saved runtime session checkpoint."""

    checkpoint_id: str
    created_at: float
    version: str = "1.0"
    goal_count: int = 0
    task_count: int = 0
    lock_count: int = 0
    clarification_count: int = 0
    event_queue_size: int = 0
    is_clean_shutdown: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint_id, str) or not self.checkpoint_id.strip():
            raise ValueError("checkpoint_id must be a non-empty string.")
        if not isinstance(self.created_at, (int, float)) or self.created_at <= 0:
            raise ValueError("created_at must be a positive timestamp.")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be a non-empty string.")
