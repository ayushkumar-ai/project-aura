"""Session State Checkpoint, Persistence & Crash-Recovery Engine (M18).

Provides atomic, thread-safe, and taint-preserving snapshot and recovery for:
- MultiGoalScheduler (queued and running goal tasks)
- ResourceBudgetManager (active goals, per-goal usage, sliding window history)
- SharedResourceLockManager (active resource locks, TTLs, and URI indices)
- ClarificationGateway (pending clarification requests and user responses)
- ProactiveEventDispatcher (subscriptions and queued proactive events)

Security Invariant:
- Sanitizes all metadata and recursively strips forbidden authorization/privilege-escalation keys.
- Preserves TaintedValue provenance envelopes without untrusted elevation.
- Supports automatic fallback to previous valid checkpoints if latest checkpoint is corrupted.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from core.agent_plan import _canonical_value, _restore_value
from core.clarification_gateway import ClarificationGateway
from core.daemon_types import (
    CheckpointMetadata,
    FORBIDDEN_PRIVILEGE_KEYS,
    sanitize_checkpoint_metadata,
    sanitize_restored_metadata,
)
from core.event_dispatcher import ProactiveEventDispatcher
from core.goal_scheduler import MultiGoalScheduler
from core.provenance import TaintedValue, is_tainted, unwrap_tainted, wrap_tainted
from core.resource_budget import ResourceBudgetManager
from core.resource_locks import SharedResourceLockManager
from core.scheduling_types import (
    ClarificationRequest,
    ClarificationResponse,
    ClarificationStatus,
    ClarificationType,
    EventSubscription,
    GoalScheduleStatus,
    LockType,
    ProactiveEvent,
    ResourceLock,
    ScheduledGoalTask,
)

logger = logging.getLogger("aura.runtime_checkpoint")


class RuntimeCheckpointManager:
    """Manages atomic session snapshot creation, validation, retention, and crash recovery."""

    def __init__(
        self,
        checkpoint_dir: str | Path = ".aura_checkpoints",
        retention_count: int = 5,
        scheduler: MultiGoalScheduler | None = None,
        budget_manager: ResourceBudgetManager | None = None,
        lock_manager: SharedResourceLockManager | None = None,
        clarification_gateway: ClarificationGateway | None = None,
        event_dispatcher: ProactiveEventDispatcher | None = None,
        delegation_tree: Any | None = None,
        message_bus: Any | None = None,
        role_registry: Any | None = None,
        artifact_manager: Any | None = None,
        adaptive_optimizer: Any | None = None,
        tracer: Any | None = None,
        campaign_engine: Any | None = None,
        dynamic_skill_registry: Any | None = None,
        self_healing_orchestrator: Any | None = None,
        epistemic_graph: Any | None = None,
        durable_state_store: Any | None = None,
        learning_engine: Any | None = None,
        cross_device_sync: Any | None = None,
    ) -> None:
        self.checkpoint_dir = Path(checkpoint_dir)
        self.retention_count = max(1, int(retention_count))
        self.scheduler = scheduler
        self.budget_manager = budget_manager
        self.lock_manager = lock_manager
        self.clarification_gateway = clarification_gateway
        self.event_dispatcher = event_dispatcher
        self.delegation_tree = delegation_tree
        self.message_bus = message_bus
        self.role_registry = role_registry
        self.artifact_manager = artifact_manager
        self.adaptive_optimizer = adaptive_optimizer
        self.tracer = tracer
        self.campaign_engine = campaign_engine
        self.dynamic_skill_registry = dynamic_skill_registry
        self.self_healing_orchestrator = self_healing_orchestrator
        self.epistemic_graph = epistemic_graph
        self.durable_state_store = durable_state_store
        self.learning_engine = learning_engine
        self.cross_device_sync = cross_device_sync
        self._lock = threading.RLock()

    @property
    def checkpoint_dir(self) -> Path:
        return self._checkpoint_dir

    @checkpoint_dir.setter
    def checkpoint_dir(self, value: str | Path) -> None:
        self._checkpoint_dir = Path(value)
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def _pointer_path(self) -> Path:
        return self.checkpoint_dir / "latest_checkpoint.json"

    def _write_json_atomic(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = tempfile.NamedTemporaryFile(
            "w",
            dir=str(path.parent),
            delete=False,
            encoding="utf-8",
        )
        try:
            json.dump(data, temp_file, indent=2)
            temp_file.flush()
            os.fsync(temp_file.fileno())
            temp_file.close()
            shutil.move(temp_file.name, str(path))
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

    # ------------------------------------------------------------------
    # Checkpoint Snapshot
    # ------------------------------------------------------------------
    def save_checkpoint(
        self,
        checkpoint_id: str | None = None,
        is_clean_shutdown: bool = False,
        current_time: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CheckpointMetadata:
        """Create and atomically persist a full runtime session checkpoint."""
        now = current_time if current_time is not None else time.time()
        cid = str(checkpoint_id).strip() if checkpoint_id else f"ckpt_{int(now)}_{uuid4().hex[:8]}"

        with self._lock:
            # 1. Capture Scheduler State
            scheduler_data: list[dict[str, Any]] = []
            if self.scheduler is not None:
                with self.scheduler._lock:
                    for task in self.scheduler._tasks.values():
                        scheduler_data.append({
                            "schedule_id": task.schedule_id,
                            "goal_id": task.goal_id,
                            "priority": task.priority.value,
                            "base_weight": task.base_weight,
                            "effective_priority": task.effective_priority,
                            "enqueued_at": task.enqueued_at,
                            "started_at": task.started_at,
                            "completed_at": task.completed_at,
                            "status": task.status.value,
                            "required_resources": list(task.required_resources),
                            "assigned_team_id": task.assigned_team_id,
                            "assigned_role_id": task.assigned_role_id,
                            "execution_topology": task.execution_topology,
                            "metadata": sanitize_checkpoint_metadata(_canonical_value(task.metadata)),
                        })

            # 2. Capture Budget Manager State
            budget_data: dict[str, Any] = {}
            if self.budget_manager is not None:
                with self.budget_manager._lock:
                    budget_data = {
                        "active_goals": dict(self.budget_manager._active_goals),
                        "goal_usage": {
                            gid: dict(usage)
                            for gid, usage in self.budget_manager._goal_usage.items()
                        },
                        "tool_call_history": [
                            (float(t), int(c)) for t, c in self.budget_manager._global_tool_call_history
                        ],
                        "token_history": [
                            (float(t), int(c)) for t, c in self.budget_manager._global_token_history
                        ],
                    }

            # 3. Capture Lock Manager State
            locks_data: list[dict[str, Any]] = []
            if self.lock_manager is not None:
                with self.lock_manager._lock:
                    for lk in self.lock_manager._locks.values():
                        locks_data.append({
                            "lock_id": lk.lock_id,
                            "resource_uri": lk.resource_uri,
                            "lock_type": lk.lock_type.value,
                            "owner_goal_id": lk.owner_goal_id,
                            "acquired_at": lk.acquired_at,
                            "ttl_seconds": lk.ttl_seconds,
                            "metadata": sanitize_checkpoint_metadata(_canonical_value(lk.metadata)),
                        })

            # 4. Capture Clarification Gateway State
            clarification_data: dict[str, Any] = {"requests": [], "responses": []}
            if self.clarification_gateway is not None:
                with self.clarification_gateway._lock:
                    for req in self.clarification_gateway._requests.values():
                        clarification_data["requests"].append({
                            "clarification_id": req.clarification_id,
                            "goal_id": req.goal_id,
                            "task_id": req.task_id,
                            "question": req.question,
                            "options": list(req.options),
                            "clarification_type": req.clarification_type.value,
                            "status": req.status.value,
                            "created_at": req.created_at,
                            "timeout_seconds": req.timeout_seconds,
                            "metadata": sanitize_checkpoint_metadata(_canonical_value(req.metadata)),
                        })
                    for resp in self.clarification_gateway._responses.values():
                        clarification_data["responses"].append({
                            "clarification_id": resp.clarification_id,
                            "goal_id": resp.goal_id,
                            "response_data": _canonical_value(resp.response_data),
                            "status": resp.status.value,
                            "answered_at": resp.answered_at,
                            "is_untrusted": resp.is_untrusted,
                            "metadata": sanitize_checkpoint_metadata(_canonical_value(resp.metadata)),
                        })

            # 5. Capture Event Dispatcher State
            events_data: dict[str, Any] = {"subscriptions": [], "queued_events": []}
            if self.event_dispatcher is not None:
                with self.event_dispatcher._lock:
                    for sub in self.event_dispatcher._subscriptions.values():
                        events_data["subscriptions"].append({
                            "subscription_id": sub.subscription_id,
                            "topic_pattern": sub.topic_pattern,
                            "goal_id": sub.goal_id,
                            "trigger_id": sub.trigger_id,
                            "created_at": sub.created_at,
                            "cooldown_seconds": sub.cooldown_seconds,
                            "last_dispatched_at": sub.last_dispatched_at,
                            "metadata": sanitize_checkpoint_metadata(_canonical_value(sub.metadata)),
                        })
                    for evt in self.event_dispatcher._event_queue:
                        events_data["queued_events"].append({
                            "event_id": evt.event_id,
                            "topic": evt.topic,
                            "payload": _canonical_value(evt.payload),
                            "timestamp": evt.timestamp,
                            "is_untrusted": evt.is_untrusted,
                            "source": evt.source,
                            "metadata": sanitize_checkpoint_metadata(_canonical_value(evt.metadata)),
                        })

            # 6. Capture Delegation Tree State (M22)
            delegation_data: dict[str, Any] = {"active_contracts": [], "contracts": [], "results": [], "tree_edges": {}}
            if self.delegation_tree is not None:
                with self.delegation_tree._lock:
                    all_cts = dict(getattr(self.delegation_tree, "_contracts", {}))
                    all_cts.update(self.delegation_tree._active_contracts)
                    for contract in all_cts.values():
                        delegation_data["contracts"].append({
                            "delegation_id": contract.delegation_id,
                            "delegator_role_id": contract.delegator_role_id,
                            "delegatee_role_id": contract.delegatee_role_id,
                            "task_description": contract.task_description,
                            "parent_task_id": contract.parent_task_id,
                            "context": sanitize_checkpoint_metadata(_canonical_value(contract.context)),
                            "allowed_skills": list(contract.allowed_skills),
                            "current_depth": contract.current_depth,
                            "max_depth": contract.max_depth,
                            "timeout_seconds": contract.timeout_seconds,
                            "is_untrusted": contract.is_untrusted,
                            "delegation_path": list(contract.delegation_path),
                            "metadata": sanitize_checkpoint_metadata(_canonical_value(contract.metadata)),
                        })
                    for contract in self.delegation_tree._active_contracts.values():
                        delegation_data["active_contracts"].append({
                            "delegation_id": contract.delegation_id,
                            "delegator_role_id": contract.delegator_role_id,
                            "delegatee_role_id": contract.delegatee_role_id,
                            "task_description": contract.task_description,
                            "parent_task_id": contract.parent_task_id,
                            "context": sanitize_checkpoint_metadata(_canonical_value(contract.context)),
                            "allowed_skills": list(contract.allowed_skills),
                            "current_depth": contract.current_depth,
                            "max_depth": contract.max_depth,
                            "timeout_seconds": contract.timeout_seconds,
                            "is_untrusted": contract.is_untrusted,
                            "delegation_path": list(contract.delegation_path),
                            "metadata": sanitize_checkpoint_metadata(_canonical_value(contract.metadata)),
                        })
                    for res in self.delegation_tree._results.values():
                        delegation_data["results"].append({
                            "delegation_id": res.delegation_id,
                            "delegator_role_id": res.delegator_role_id,
                            "delegatee_role_id": res.delegatee_role_id,
                            "status": res.status.value,
                            "output": res.output,
                            "result_payload": sanitize_checkpoint_metadata(_canonical_value(res.result_payload)),
                            "latency_seconds": res.latency_seconds,
                            "is_untrusted": res.is_untrusted,
                            "error": res.error,
                            "metadata": sanitize_checkpoint_metadata(_canonical_value(res.metadata)),
                        })
                    delegation_data["tree_edges"] = {
                        k: list(v) for k, v in self.delegation_tree._tree_edges.items()
                    }

            # 7. Capture Message Bus State (M22)
            message_bus_data: dict[str, Any] = {"mailboxes": {}, "history": []}
            if self.message_bus is not None:
                with self.message_bus._lock:
                    for role_id, msgs in self.message_bus._mailboxes.items():
                        message_bus_data["mailboxes"][role_id] = [
                            {
                                "message_id": m.message_id,
                                "sender_role_id": m.sender_role_id,
                                "recipient_role_id": m.recipient_role_id,
                                "message_type": m.message_type.value,
                                "content": m.content,
                                "payload": sanitize_checkpoint_metadata(_canonical_value(m.payload)),
                                "is_untrusted": m.is_untrusted,
                                "timestamp": m.timestamp,
                                "metadata": sanitize_checkpoint_metadata(_canonical_value(m.metadata)),
                            }
                            for m in msgs
                        ]
                    for m in self.message_bus._history:
                        message_bus_data["history"].append({
                            "message_id": m.message_id,
                            "sender_role_id": m.sender_role_id,
                            "recipient_role_id": m.recipient_role_id,
                            "message_type": m.message_type.value,
                            "content": m.content,
                            "payload": sanitize_checkpoint_metadata(_canonical_value(m.payload)),
                            "is_untrusted": m.is_untrusted,
                            "timestamp": m.timestamp,
                            "metadata": sanitize_checkpoint_metadata(_canonical_value(m.metadata)),
                        })

            # 8. Capture Role Registry State (M22)
            roles_data: list[dict[str, Any]] = []
            if self.role_registry is not None:
                with getattr(self.role_registry, "_lock", threading.RLock()):
                    for r in self.role_registry.list_roles():
                        roles_data.append({
                            "role_id": r.role_id,
                            "name": r.name,
                            "description": r.description,
                            "system_prompt": r.system_prompt,
                            "allowed_skills": list(r.allowed_skills),
                            "required_capabilities": [c.value if hasattr(c, "value") else str(c) for c in r.required_capabilities],
                            "temperature": r.temperature,
                            "max_tokens": r.max_tokens,
                            "metadata": sanitize_checkpoint_metadata(_canonical_value(r.metadata)),
                        })

            # 9. Capture Artifact Manager State (M24)
            artifacts_data: dict[str, Any] = {}
            if self.artifact_manager is not None:
                if hasattr(self.artifact_manager, "export_manifests"):
                    artifacts_data = self.artifact_manager.export_manifests()

            # 10. Capture Adaptive Policy Optimizer State (M24)
            optimizer_data: dict[str, Any] = {}
            if self.adaptive_optimizer is not None:
                if hasattr(self.adaptive_optimizer, "export_history"):
                    optimizer_data = self.adaptive_optimizer.export_history()

            # 11. Capture Active Tracing Context (M24)
            tracing_data: dict[str, Any] = {}
            if self.tracer is not None and hasattr(self.tracer, "get_current_context"):
                active_ctx = self.tracer.get_current_context()
                if active_ctx is not None:
                    tracing_data = {"active_context": active_ctx.to_dict()}

            # 12. Capture Campaign Engine State (M25)
            campaigns_data: dict[str, Any] = {"definitions": [], "graphs": {}, "routers": {}, "sagas": {}, "statuses": {}}
            if self.campaign_engine is not None:
                with getattr(self.campaign_engine, "_lock", threading.RLock()):
                    campaigns_data["definitions"] = [d.to_dict() for d in self.campaign_engine._definitions.values()]
                    campaigns_data["graphs"] = {k: v.to_dict() for k, v in self.campaign_engine._graphs.items()}
                    campaigns_data["routers"] = {k: v.to_dict() for k, v in self.campaign_engine._routers.items()}
                    campaigns_data["sagas"] = {k: v.to_dict() for k, v in self.campaign_engine._sagas.items()}
                    campaigns_data["statuses"] = {k: v.value for k, v in self.campaign_engine._statuses.items()}

            # 13. Capture Dynamic Skill Registry State (M26)
            dynamic_skills_data: dict[str, Any] = {}
            if self.dynamic_skill_registry is not None:
                with getattr(self.dynamic_skill_registry, "_lock", threading.RLock()):
                    dynamic_skills_data = self.dynamic_skill_registry.to_dict()

            # 14. Capture Self-Healing Orchestrator State (M27)
            self_healing_data: dict[str, Any] = {}
            if getattr(self, "self_healing_orchestrator", None) is not None:
                try:
                    self_healing_data = self.self_healing_orchestrator.to_dict()
                except Exception as ex:
                    logger.debug("Checkpoint: skipping self-healing state capture: %s", ex)

            # 15. Capture Epistemic Knowledge Graph State (M28)
            epistemic_graph_data: dict[str, Any] = {}
            if getattr(self, "epistemic_graph", None) is not None:
                try:
                    epistemic_graph_data = self.epistemic_graph.to_dict()
                except Exception as ex:
                    logger.debug("Checkpoint: skipping epistemic graph state capture: %s", ex)

            # 16. Capture Durable Personal State (M30)
            durable_state_data: dict[str, Any] = {}
            if getattr(self, "durable_state_store", None) is not None:
                try:
                    if hasattr(self.durable_state_store, "create_snapshot"):
                        durable_state_data = self.durable_state_store.create_snapshot().to_dict()
                except Exception as ex:
                    logger.debug("Checkpoint: skipping durable state capture: %s", ex)

            # 17. Capture Learning Loop Heuristics (M36)
            learning_data: dict[str, Any] = {}
            if getattr(self, "learning_engine", None) is not None:
                try:
                    with getattr(self.learning_engine, "_lock", threading.RLock()):
                        learning_data = {
                            "heuristics": [h.to_dict() for h in self.learning_engine._heuristics.values()]
                        }
                except Exception as ex:
                    logger.debug("Checkpoint: skipping learning engine capture: %s", ex)

            # 18. Capture Cross-Device Sync State (M39)
            sync_data: dict[str, Any] = {}
            if getattr(self, "cross_device_sync", None) is not None:
                try:
                    with getattr(self.cross_device_sync, "_lock", threading.RLock()):
                        sync_data = self.cross_device_sync.get_status().to_dict()
                except Exception as ex:
                    logger.debug("Checkpoint: skipping sync state capture: %s", ex)

            # Build metadata record
            ckpt_meta = CheckpointMetadata(
                checkpoint_id=cid,
                created_at=now,
                version="1.0",
                goal_count=len(scheduler_data),
                task_count=len(scheduler_data),
                lock_count=len(locks_data),
                clarification_count=len(clarification_data["requests"]),
                event_queue_size=len(events_data["queued_events"]),
                is_clean_shutdown=is_clean_shutdown,
                metadata=sanitize_checkpoint_metadata(_canonical_value(metadata or {})),
            )

            payload = {
                "metadata": {
                    "checkpoint_id": ckpt_meta.checkpoint_id,
                    "created_at": ckpt_meta.created_at,
                    "version": ckpt_meta.version,
                    "goal_count": ckpt_meta.goal_count,
                    "task_count": ckpt_meta.task_count,
                    "lock_count": ckpt_meta.lock_count,
                    "clarification_count": ckpt_meta.clarification_count,
                    "event_queue_size": ckpt_meta.event_queue_size,
                    "is_clean_shutdown": ckpt_meta.is_clean_shutdown,
                    "metadata": ckpt_meta.metadata,
                },
                "scheduler": scheduler_data,
                "budget": budget_data,
                "locks": locks_data,
                "clarification": clarification_data,
                "events": events_data,
                "delegation": delegation_data,
                "message_bus": message_bus_data,
                "roles": roles_data,
                "artifacts": artifacts_data,
                "optimizer": optimizer_data,
                "tracing": tracing_data,
                "campaigns": campaigns_data,
                "dynamic_skills": dynamic_skills_data,
                "self_healing": self_healing_data,  # M27
                "epistemic_graph": epistemic_graph_data,  # M28
                "durable_state": durable_state_data,  # M30
                "learning": learning_data,  # M36
                "cross_device_sync": sync_data,  # M39
            }


            ckpt_path = self.checkpoint_dir / f"{cid}.json"
            self._write_json_atomic(ckpt_path, payload)

            # Update latest pointer
            self._write_json_atomic(
                self._pointer_path(),
                {"latest_checkpoint_id": cid, "updated_at": now},
            )

            # Prune old checkpoints
            self.prune_old_checkpoints()

            logger.info("Saved runtime checkpoint '%s' successfully.", cid)
            return ckpt_meta

    # ------------------------------------------------------------------
    # Checkpoint Restoration & Recovery
    # ------------------------------------------------------------------
    def list_checkpoints(self) -> list[Path]:
        """List all valid checkpoint files ordered by creation time (newest first)."""
        files = [
            f for f in self.checkpoint_dir.glob("*.json")
            if f.is_file() and f.name != "latest_checkpoint.json"
        ]
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)

    def prune_old_checkpoints(self) -> int:
        """Retain only the latest N checkpoints and delete older ones."""
        with self._lock:
            all_ckpts = self.list_checkpoints()
            if len(all_ckpts) <= self.retention_count:
                return 0

            pruned_count = 0
            for old_file in all_ckpts[self.retention_count:]:
                try:
                    old_file.unlink()
                    pruned_count += 1
                except Exception as e:
                    logger.warning("Error deleting old checkpoint '%s': %s", old_file, e)
            return pruned_count

    def load_checkpoint_data(self, path: Path) -> dict[str, Any]:
        """Safely load and validate checkpoint JSON."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "metadata" not in data:
            raise ValueError(f"Invalid checkpoint schema in '{path}'")
        return data

    def restore_latest_checkpoint(self) -> CheckpointMetadata | None:
        """Find and restore from the newest valid checkpoint, falling back if corrupted."""
        with self._lock:
            ckpts = self.list_checkpoints()
            if not ckpts:
                logger.info("No runtime checkpoints found to restore.")
                return None

            for ckpt_path in ckpts:
                try:
                    meta = self.restore_from_file(ckpt_path)
                    logger.info("Restored runtime state from checkpoint '%s'.", ckpt_path.name)
                    return meta
                except Exception as e:
                    logger.warning("Failed to restore checkpoint '%s': %s. Trying fallback...", ckpt_path.name, e)

            logger.error("All available checkpoints were corrupted or invalid.")
            return None

    restore_latest = restore_latest_checkpoint

    def restore_from_file(self, checkpoint_path: str | Path) -> CheckpointMetadata:
        """Restore runtime state atomically from a specific checkpoint file."""
        path = Path(checkpoint_path)
        if not path.exists():
            raise FileNotFoundError(f"Checkpoint file not found: {path}")

        data = self.load_checkpoint_data(path)
        meta_d = data.get("metadata", {})
        ckpt_meta = CheckpointMetadata(
            checkpoint_id=str(meta_d.get("checkpoint_id", path.stem)),
            created_at=float(meta_d.get("created_at", time.time())),
            version=str(meta_d.get("version", "1.0")),
            goal_count=int(meta_d.get("goal_count", 0)),
            task_count=int(meta_d.get("task_count", 0)),
            lock_count=int(meta_d.get("lock_count", 0)),
            clarification_count=int(meta_d.get("clarification_count", 0)),
            event_queue_size=int(meta_d.get("event_queue_size", 0)),
            is_clean_shutdown=bool(meta_d.get("is_clean_shutdown", False)),
            metadata=sanitize_restored_metadata(_restore_value(meta_d.get("metadata", {}))),
        )

        with self._lock:
            # 1. Restore Scheduler
            if self.scheduler is not None and "scheduler" in data:
                with self.scheduler._lock:
                    self.scheduler._tasks.clear()
                    from core.goal import GoalPriority
                    for td in data["scheduler"]:
                        if not isinstance(td, dict):
                            continue
                        clean_meta = sanitize_restored_metadata(_restore_value(td.get("metadata", {})))
                        # Ensure no authorization flags in restored task metadata
                        for k in list(clean_meta.keys()):
                            if k.lower() in FORBIDDEN_PRIVILEGE_KEYS:
                                del clean_meta[k]

                        task = ScheduledGoalTask(
                            schedule_id=str(td.get("schedule_id", uuid4())),
                            goal_id=str(td["goal_id"]),
                            priority=GoalPriority(td.get("priority", GoalPriority.MEDIUM.value)),
                            base_weight=float(td.get("base_weight", 1.0)),
                            effective_priority=float(td.get("effective_priority", 1.0)),
                            enqueued_at=float(td.get("enqueued_at", time.time())),
                            started_at=float(td["started_at"]) if td.get("started_at") is not None else None,
                            completed_at=float(td["completed_at"]) if td.get("completed_at") is not None else None,
                            status=GoalScheduleStatus(td.get("status", GoalScheduleStatus.QUEUED.value)),
                            required_resources=tuple(td.get("required_resources", ())),
                            metadata=clean_meta,
                        )
                        self.scheduler._tasks[task.goal_id] = task

            # 2. Restore Budget Manager
            if self.budget_manager is not None and "budget" in data:
                with self.budget_manager._lock:
                    bd = data["budget"]
                    self.budget_manager._active_goals = {
                        str(k): float(v) for k, v in bd.get("active_goals", {}).items()
                    }
                    self.budget_manager._goal_usage = {
                        str(k): dict(v) for k, v in bd.get("goal_usage", {}).items()
                    }
                    self.budget_manager._global_tool_call_history = [
                        (float(t), int(c)) for t, c in bd.get("tool_call_history", [])
                    ]
                    self.budget_manager._global_token_history = [
                        (float(t), int(c)) for t, c in bd.get("token_history", [])
                    ]

            # 3. Restore Locks
            if self.lock_manager is not None and "locks" in data:
                with self.lock_manager._lock:
                    self.lock_manager._locks.clear()
                    self.lock_manager._resource_index.clear()
                    for ld in data["locks"]:
                        if not isinstance(ld, dict):
                            continue
                        clean_meta = sanitize_restored_metadata(_restore_value(ld.get("metadata", {})))
                        lk = ResourceLock(
                            lock_id=str(ld["lock_id"]),
                            resource_uri=str(ld["resource_uri"]),
                            lock_type=LockType(ld.get("lock_type", LockType.SHARED_READ.value)),
                            owner_goal_id=str(ld["owner_goal_id"]),
                            acquired_at=float(ld.get("acquired_at", time.time())),
                            ttl_seconds=float(ld.get("ttl_seconds", 30.0)),
                            metadata=clean_meta,
                        )
                        self.lock_manager._locks[lk.lock_id] = lk
                        uri = lk.resource_uri
                        if uri not in self.lock_manager._resource_index:
                            self.lock_manager._resource_index[uri] = []
                        self.lock_manager._resource_index[uri].append(lk.lock_id)

            # 4. Restore Clarifications
            if self.clarification_gateway is not None and "clarification" in data:
                with self.clarification_gateway._lock:
                    self.clarification_gateway._requests.clear()
                    self.clarification_gateway._responses.clear()
                    cd = data["clarification"]
                    for req_d in cd.get("requests", []):
                        if not isinstance(req_d, dict):
                            continue
                        clean_meta = sanitize_restored_metadata(_restore_value(req_d.get("metadata", {})))
                        req = ClarificationRequest(
                            clarification_id=str(req_d["clarification_id"]),
                            goal_id=str(req_d["goal_id"]),
                            task_id=str(req_d["task_id"]),
                            question=str(req_d["question"]),
                            options=tuple(req_d.get("options", ())),
                            clarification_type=ClarificationType(req_d.get("clarification_type", ClarificationType.SINGLE_CHOICE.value)),
                            status=ClarificationStatus(req_d.get("status", ClarificationStatus.PENDING.value)),
                            created_at=float(req_d.get("created_at", time.time())),
                            timeout_seconds=float(req_d.get("timeout_seconds", 600.0)),
                            metadata=clean_meta,
                        )
                        self.clarification_gateway._requests[req.clarification_id] = req

                    for resp_d in cd.get("responses", []):
                        if not isinstance(resp_d, dict):
                            continue
                        clean_meta = sanitize_restored_metadata(_restore_value(resp_d.get("metadata", {})))
                        resp = ClarificationResponse(
                            clarification_id=str(resp_d["clarification_id"]),
                            goal_id=str(resp_d.get("goal_id", "")),
                            response_data=_restore_value(resp_d.get("response_data")),
                            status=ClarificationStatus(resp_d.get("status", ClarificationStatus.ANSWERED.value)),
                            answered_at=float(resp_d.get("answered_at", time.time())),
                            is_untrusted=bool(resp_d.get("is_untrusted", False)),
                            metadata=clean_meta,
                        )
                        self.clarification_gateway._responses[resp.clarification_id] = resp

            # 5. Restore Events
            if self.event_dispatcher is not None and "events" in data:
                with self.event_dispatcher._lock:
                    self.event_dispatcher._subscriptions.clear()
                    self.event_dispatcher._event_queue.clear()
                    ed = data["events"]
                    for sub_d in ed.get("subscriptions", []):
                        if not isinstance(sub_d, dict):
                            continue
                        clean_meta = sanitize_restored_metadata(_restore_value(sub_d.get("metadata", {})))
                        sub = EventSubscription(
                            subscription_id=str(sub_d["subscription_id"]),
                            topic_pattern=str(sub_d["topic_pattern"]),
                            goal_id=str(sub_d["goal_id"]),
                            trigger_id=sub_d.get("trigger_id"),
                            created_at=float(sub_d.get("created_at", time.time())),
                            cooldown_seconds=float(sub_d.get("cooldown_seconds", 0.0)),
                            last_dispatched_at=float(sub_d["last_dispatched_at"]) if sub_d.get("last_dispatched_at") is not None else None,
                            metadata=clean_meta,
                        )
                        self.event_dispatcher._subscriptions[sub.subscription_id] = sub

                    for evt_d in ed.get("queued_events", []):
                        if not isinstance(evt_d, dict):
                            continue
                        clean_meta = sanitize_restored_metadata(_restore_value(evt_d.get("metadata", {})))
                        evt = ProactiveEvent(
                            event_id=str(evt_d.get("event_id", uuid4())),
                            topic=str(evt_d.get("topic", "")),
                            payload=_restore_value(evt_d.get("payload")),
                            timestamp=float(evt_d.get("timestamp", time.time())),
                            is_untrusted=bool(evt_d.get("is_untrusted", False)),
                            source=str(evt_d.get("source", "external")),
                            metadata=clean_meta,
                        )
                        self.event_dispatcher._event_queue.append(evt)

            # 6. Restore Delegation Tree (M22)
            if self.delegation_tree is not None and "delegation" in data:
                from core.agent_delegation import DelegationContract, DelegationResult, DelegationStatus
                with self.delegation_tree._lock:
                    self.delegation_tree._active_contracts.clear()
                    if hasattr(self.delegation_tree, "_contracts"):
                        self.delegation_tree._contracts.clear()
                    self.delegation_tree._results.clear()
                    self.delegation_tree._tree_edges.clear()
                    del_d = data["delegation"]
                    for cd in del_d.get("contracts", []):
                        if not isinstance(cd, dict):
                            continue
                        clean_meta = sanitize_restored_metadata(_restore_value(cd.get("metadata", {})))
                        contract = DelegationContract(
                            delegation_id=str(cd["delegation_id"]),
                            delegator_role_id=str(cd["delegator_role_id"]),
                            delegatee_role_id=str(cd["delegatee_role_id"]),
                            task_description=str(cd["task_description"]),
                            parent_task_id=str(cd.get("parent_task_id", "")),
                            context=dict(sanitize_restored_metadata(_restore_value(cd.get("context", {})))),
                            allowed_skills=tuple(cd.get("allowed_skills", ())),
                            current_depth=int(cd.get("current_depth", 1)),
                            max_depth=int(cd.get("max_depth", 3)),
                            timeout_seconds=float(cd.get("timeout_seconds", 300.0)),
                            is_untrusted=bool(cd.get("is_untrusted", False)),
                            delegation_path=tuple(cd.get("delegation_path", ())),
                            metadata=clean_meta,
                        )
                        if hasattr(self.delegation_tree, "_contracts"):
                            self.delegation_tree._contracts[contract.delegation_id] = contract

                    for cd in del_d.get("active_contracts", []):
                        if not isinstance(cd, dict):
                            continue
                        clean_meta = sanitize_restored_metadata(_restore_value(cd.get("metadata", {})))
                        contract = DelegationContract(
                            delegation_id=str(cd["delegation_id"]),
                            delegator_role_id=str(cd["delegator_role_id"]),
                            delegatee_role_id=str(cd["delegatee_role_id"]),
                            task_description=str(cd["task_description"]),
                            parent_task_id=str(cd.get("parent_task_id", "")),
                            context=dict(sanitize_restored_metadata(_restore_value(cd.get("context", {})))),
                            allowed_skills=tuple(cd.get("allowed_skills", ())),
                            current_depth=int(cd.get("current_depth", 1)),
                            max_depth=int(cd.get("max_depth", 3)),
                            timeout_seconds=float(cd.get("timeout_seconds", 300.0)),
                            is_untrusted=bool(cd.get("is_untrusted", False)),
                            delegation_path=tuple(cd.get("delegation_path", ())),
                            metadata=clean_meta,
                        )
                        self.delegation_tree._active_contracts[contract.delegation_id] = contract
                        if hasattr(self.delegation_tree, "_contracts"):
                            self.delegation_tree._contracts[contract.delegation_id] = contract

                    for rd in del_d.get("results", []):
                        if not isinstance(rd, dict):
                            continue
                        clean_meta = sanitize_restored_metadata(_restore_value(rd.get("metadata", {})))
                        res = DelegationResult(
                            delegation_id=str(rd["delegation_id"]),
                            delegator_role_id=str(rd["delegator_role_id"]),
                            delegatee_role_id=str(rd["delegatee_role_id"]),
                            status=DelegationStatus(rd.get("status", DelegationStatus.COMPLETED.value)),
                            output=str(rd.get("output", "")),
                            result_payload=dict(sanitize_restored_metadata(_restore_value(rd.get("result_payload", {})))),
                            latency_seconds=float(rd.get("latency_seconds", 0.0)),
                            is_untrusted=bool(rd.get("is_untrusted", True)),
                            error=rd.get("error"),
                            metadata=clean_meta,
                        )
                        self.delegation_tree._results[res.delegation_id] = res

                    self.delegation_tree._tree_edges = {
                        str(k): list(v) for k, v in del_d.get("tree_edges", {}).items()
                    }

            # 7. Restore Message Bus (M22)
            if self.message_bus is not None and "message_bus" in data:
                from core.agent_message_types import AgentMessage, AgentMessageType
                with self.message_bus._lock:
                    self.message_bus._mailboxes.clear()
                    self.message_bus._history.clear()
                    mb_d = data["message_bus"]
                    for role_id, msgs in mb_d.get("mailboxes", {}).items():
                        self.message_bus._mailboxes[str(role_id)] = [
                            AgentMessage(
                                message_id=str(md["message_id"]),
                                sender_role_id=str(md["sender_role_id"]),
                                recipient_role_id=str(md["recipient_role_id"]),
                                message_type=AgentMessageType(md.get("message_type", AgentMessageType.TASK_DELEGATION.value)),
                                content=str(md.get("content", "")),
                                payload=sanitize_restored_metadata(_restore_value(md.get("payload", {}))),
                                is_untrusted=bool(md.get("is_untrusted", False)),
                                timestamp=float(md.get("timestamp", time.time())),
                                metadata=sanitize_restored_metadata(_restore_value(md.get("metadata", {}))),
                            )
                            for md in msgs if isinstance(md, dict)
                        ]
                    for md in mb_d.get("history", []):
                        if not isinstance(md, dict):
                            continue
                        self.message_bus._history.append(
                            AgentMessage(
                                message_id=str(md["message_id"]),
                                sender_role_id=str(md["sender_role_id"]),
                                recipient_role_id=str(md["recipient_role_id"]),
                                message_type=AgentMessageType(md.get("message_type", AgentMessageType.TASK_DELEGATION.value)),
                                content=str(md.get("content", "")),
                                payload=sanitize_restored_metadata(_restore_value(md.get("payload", {}))),
                                is_untrusted=bool(md.get("is_untrusted", False)),
                                timestamp=float(md.get("timestamp", time.time())),
                                metadata=sanitize_restored_metadata(_restore_value(md.get("metadata", {}))),
                            )
                        )

            # 8. Restore Roles (M22)
            if self.role_registry is not None and "roles" in data:
                from core.agent_role import AgentRole
                from core.capability_registry import ModelCapability
                for rd in data.get("roles", []):
                    if not isinstance(rd, dict):
                        continue
                    caps = []
                    for c in rd.get("required_capabilities", []):
                        try:
                            caps.append(ModelCapability(c))
                        except Exception:
                            pass
                    clean_meta = sanitize_restored_metadata(_restore_value(rd.get("metadata", {})))
                    role = AgentRole(
                        role_id=str(rd["role_id"]),
                        name=str(rd.get("name", "")),
                        description=str(rd.get("description", "")),
                        system_prompt=str(rd.get("system_prompt", "")),
                        allowed_skills=tuple(rd.get("allowed_skills", ())),
                        required_capabilities=tuple(caps),
                        temperature=float(rd.get("temperature", 0.7)),
                        max_tokens=int(rd["max_tokens"]) if rd.get("max_tokens") is not None else None,
                        metadata=clean_meta,
                    )
                    self.role_registry.register_role(role, overwrite=True)

            # 9. Restore Artifacts (M24)
            if self.artifact_manager is not None and "artifacts" in data:
                if hasattr(self.artifact_manager, "import_manifests"):
                    self.artifact_manager.import_manifests(data["artifacts"])

            # 10. Restore Optimizer State (M24)
            if self.adaptive_optimizer is not None and "optimizer" in data:
                if hasattr(self.adaptive_optimizer, "import_history"):
                    self.adaptive_optimizer.import_history(data["optimizer"])

            # 11. Restore Campaign Engine State (M25)
            campaigns_data = data.get("campaigns", {})
            if self.campaign_engine is not None and campaigns_data:
                from core.campaign_types import CampaignDefinition, CampaignStatus
                from core.mission_graph import MissionGraph
                from core.artifact_pipeline import ArtifactPipelineRouter
                from core.saga_coordinator import SagaCoordinator
                with getattr(self.campaign_engine, "_lock", threading.RLock()):
                    for d in campaigns_data.get("definitions", []):
                        try:
                            defn = CampaignDefinition.from_dict(d)
                            self.campaign_engine._definitions[defn.campaign_id] = defn
                        except Exception as ex:
                            logger.debug("Error restoring campaign definition: %s", ex)
                    for cid, g_data in campaigns_data.get("graphs", {}).items():
                        try:
                            self.campaign_engine._graphs[cid] = MissionGraph.from_dict(g_data)
                        except Exception as ex:
                            logger.debug("Error restoring mission graph: %s", ex)
                    for cid, r_data in campaigns_data.get("routers", {}).items():
                        try:
                            self.campaign_engine._routers[cid] = ArtifactPipelineRouter.from_dict(r_data)
                        except Exception as ex:
                            logger.debug("Error restoring artifact router: %s", ex)
                    for cid, s_data in campaigns_data.get("sagas", {}).items():
                        try:
                            self.campaign_engine._sagas[cid] = SagaCoordinator.from_dict(s_data, engine=self.campaign_engine._default_compensating_engine)
                        except Exception as ex:
                            logger.debug("Error restoring saga coordinator: %s", ex)
                    for cid, st_val in campaigns_data.get("statuses", {}).items():
                        try:
                            self.campaign_engine._statuses[cid] = CampaignStatus(st_val)
                        except Exception as ex:
                            logger.debug("Error restoring campaign status: %s", ex)

            # 12. Restore Dynamic Skill Registry State (M26)
            dynamic_skills_data = data.get("dynamic_skills", {})
            if self.dynamic_skill_registry is not None and dynamic_skills_data:
                from core.dynamic_skill_registry import DynamicSkillRegistry
                with getattr(self.dynamic_skill_registry, "_lock", threading.RLock()):
                    try:
                        restored_dyn = DynamicSkillRegistry.from_dict(
                            dynamic_skills_data,
                            validator=getattr(self.dynamic_skill_registry, "_validator", None),
                            executor=getattr(self.dynamic_skill_registry, "_executor", None),
                            tracer=self.tracer,
                        )
                        self.dynamic_skill_registry._skills.update(restored_dyn._skills)
                        self.dynamic_skill_registry._composite_skills.update(restored_dyn._composite_skills)
                        self.dynamic_skill_registry._dynamic_tools.update(restored_dyn._dynamic_tools)
                    except Exception as ex:
                        logger.debug("Error restoring dynamic skill registry: %s", ex)

            # 13. Restore Self-Healing Orchestrator State (M27)
            self_healing_data = data.get("self_healing", {})
            _sho = getattr(self, "self_healing_orchestrator", None)
            if _sho is not None and self_healing_data:
                try:
                    _sho.restore_from_dict(self_healing_data)
                    logger.debug("Restored self-healing orchestrator state from checkpoint.")
                except Exception as ex:
                    logger.debug("Error restoring self-healing orchestrator state: %s", ex)

            # 14. Restore Epistemic Knowledge Graph State (M28)
            epistemic_graph_data = data.get("epistemic_graph", {})
            _ekg = getattr(self, "epistemic_graph", None)
            if _ekg is not None and epistemic_graph_data:
                try:
                    from core.epistemic_graph import EpistemicKnowledgeGraph
                    restored_ekg = EpistemicKnowledgeGraph.from_dict(
                        epistemic_graph_data,
                        max_entities=_ekg.max_entities,
                        max_relations=_ekg.max_relations,
                    )
                    with _ekg._lock:
                        for ent in restored_ekg._entities.values():
                            _ekg.add_entity(ent, overwrite=True)
                        for rel in restored_ekg._relations.values():
                            if _ekg.has_entity(rel.source_id) and _ekg.has_entity(rel.target_id):
                                _ekg.add_relation(rel, reinforce_if_exists=False)
                    logger.debug("Restored epistemic knowledge graph state from checkpoint.")
                except Exception as ex:
                    logger.debug("Error restoring epistemic knowledge graph state: %s", ex)

            # 15. Restore Durable Personal State (M30)
            durable_data = data.get("durable_state", {})
            _dss = getattr(self, "durable_state_store", None)
            if _dss is not None and durable_data:
                try:
                    from core.personal_state_types import PersonalStateSnapshot
                    snap = PersonalStateSnapshot.from_dict(durable_data)
                    _dss._apply_snapshot(snap)
                    logger.debug("Restored durable personal state from checkpoint.")
                except Exception as ex:
                    logger.debug("Error restoring durable personal state: %s", ex)

            # 16. Restore Learning Heuristics (M36)
            learning_data = data.get("learning", {})
            _le = getattr(self, "learning_engine", None)
            if _le is not None and learning_data:
                try:
                    from core.learning_loop_types import DistilledHeuristic
                    with _le._lock:
                        for hd in learning_data.get("heuristics", []):
                            h = DistilledHeuristic.from_dict(hd)
                            _le._heuristics[h.task_pattern] = h
                    logger.debug("Restored learning heuristics from checkpoint.")
                except Exception as ex:
                    logger.debug("Error restoring learning heuristics: %s", ex)

            # 17. Restore Cross-Device Sync State (M39)
            sync_data = data.get("cross_device_sync", {})
            _sync = getattr(self, "cross_device_sync", None)
            if _sync is not None and sync_data:
                try:
                    with _sync._lock:
                        if "vector_clock" in sync_data and hasattr(_sync, "_vector_clock"):
                            _sync._vector_clock.merge(sync_data["vector_clock"])
                        if "applied_deltas_count" in sync_data:
                            _sync._applied_deltas_count = int(sync_data["applied_deltas_count"])
                    logger.debug("Restored cross-device sync state from checkpoint.")
                except Exception as ex:
                    logger.debug("Error restoring cross-device sync state: %s", ex)

        return ckpt_meta

