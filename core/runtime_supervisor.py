"""Autonomous Background Execution Daemon & Runtime Supervisor (M18).

Provides continuous, asynchronous runtime orchestration and health supervision:
- Periodic proactive event perception and cron tick dispatch
- Priority-weighted multi-goal batch scheduling
- Resource lock lease watchdog and TTL auto-reclaim
- Human clarification timeout pruning
- Periodic multi-tier memory lifecycle and compaction passes
- Periodic atomic session checkpointing
- Worker error isolation and resilience against individual task failures
- Graceful startup, pause, resume, and bounded shutdown lifecycle
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from core.daemon_types import (
    CheckpointMetadata,
    DaemonStatus,
    SupervisorConfig,
    SupervisorTelemetry,
    sanitize_checkpoint_metadata,
)
from core.runtime_checkpoint import RuntimeCheckpointManager

logger = logging.getLogger("aura.runtime_supervisor")


class AutonomousSupervisor:
    """Continuous background worker daemon orchestrating Project AURA's runtime lifecycle."""

    def __init__(
        self,
        runtime: Any,
        config: SupervisorConfig | None = None,
        checkpoint_manager: RuntimeCheckpointManager | None = None,
    ) -> None:
        self.runtime = runtime
        self.config = config if config is not None else SupervisorConfig()
        self.checkpoint_manager = (
            checkpoint_manager
            if checkpoint_manager is not None
            else getattr(runtime, "checkpoint_manager", None)
        )
        if self.checkpoint_manager is None:
            self.checkpoint_manager = RuntimeCheckpointManager(
                checkpoint_dir=self.config.checkpoint_dir,
                retention_count=self.config.checkpoint_retention_count,
                scheduler=getattr(runtime, "scheduler", None),
                budget_manager=getattr(runtime, "budget_manager", None),
                lock_manager=getattr(runtime, "lock_manager", None),
                clarification_gateway=getattr(runtime, "clarification_gateway", None),
                event_dispatcher=getattr(runtime, "event_dispatcher", None),
                delegation_tree=getattr(runtime, "delegation_tree", None),
                message_bus=getattr(runtime, "message_bus", None),
                role_registry=getattr(runtime, "role_registry", None),
                artifact_manager=getattr(runtime, "artifact_manager", None),
                adaptive_optimizer=getattr(runtime, "adaptive_optimizer", None),
                tracer=getattr(runtime, "tracer", None),
                campaign_engine=getattr(runtime, "campaign_engine", None),
            )

        self._lock = threading.RLock()
        self._status = DaemonStatus.STOPPED
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None

        # Telemetry & Diagnostics state
        self._started_at: float | None = None
        self._total_heartbeats: int = 0
        self._total_events_dispatched: int = 0
        self._total_goals_stepped: int = 0
        self._total_checkpoints_saved: int = 0
        self._total_errors: int = 0
        self._last_heartbeat_timestamp: float | None = None
        self._last_checkpoint_timestamp: float | None = None
        self._last_error: str | None = None

        # Sub-routine timestamp trackers
        self._last_event_dispatch_time: float = 0.0
        self._last_scheduler_step_time: float = 0.0
        self._last_lock_prune_time: float = 0.0
        self._last_clarification_prune_time: float = 0.0
        self._last_memory_maintenance_time: float = 0.0
        self._last_checkpoint_time: float = 0.0

    @property
    def status(self) -> DaemonStatus:
        with self._lock:
            return self._status

    def is_running(self) -> bool:
        with self._lock:
            return self._status in (DaemonStatus.RUNNING, DaemonStatus.PAUSED)

    # ------------------------------------------------------------------
    # Lifecycle Controls
    # ------------------------------------------------------------------
    def start(self, auto_recover: bool | None = None) -> bool:
        """Start the background supervisor daemon thread."""
        with self._lock:
            if self._status in (DaemonStatus.RUNNING, DaemonStatus.STARTING):
                logger.warning("Supervisor is already running.")
                return False

            self._status = DaemonStatus.STARTING
            self._stop_event.clear()
            now = time.time()
            self._started_at = now
            self._last_event_dispatch_time = now
            self._last_scheduler_step_time = now
            self._last_lock_prune_time = now
            self._last_clarification_prune_time = now
            self._last_memory_maintenance_time = now
            self._last_checkpoint_time = now

            should_recover = (
                auto_recover
                if auto_recover is not None
                else self.config.auto_recover_on_startup
            )
            if should_recover and self.checkpoint_manager is not None:
                try:
                    self.checkpoint_manager.restore_latest_checkpoint()
                except Exception as e:
                    logger.warning("Startup checkpoint recovery encountered error: %s", e)
                    self._total_errors += 1
                    self._last_error = f"Recovery error: {e}"

            self._status = DaemonStatus.RUNNING
            self._worker_thread = threading.Thread(
                target=self._run_loop,
                name="AuraSupervisorWorker",
                daemon=True,
            )
            self._worker_thread.start()
            logger.info("Autonomous supervisor started successfully.")
            return True

    def stop(self, timeout: float | None = None) -> bool:
        """Gracefully stop the background supervisor daemon and save a clean shutdown checkpoint."""
        with self._lock:
            if self._status == DaemonStatus.STOPPED:
                return False

            self._status = DaemonStatus.STOPPING
            self._stop_event.set()

        eff_timeout = (
            float(timeout)
            if timeout is not None
            else self.config.shutdown_timeout_seconds
        )

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=eff_timeout)
            if self._worker_thread.is_alive():
                logger.warning("Supervisor worker thread did not terminate within timeout %.2fs", eff_timeout)

        # Save clean shutdown checkpoint
        with self._lock:
            if self.checkpoint_manager is not None:
                try:
                    self.checkpoint_manager.save_checkpoint(is_clean_shutdown=True)
                    self._total_checkpoints_saved += 1
                    self._last_checkpoint_timestamp = time.time()
                except Exception as e:
                    logger.warning("Failed to save shutdown checkpoint: %s", e)
                    self._total_errors += 1
                    self._last_error = f"Shutdown checkpoint error: {e}"

            self._status = DaemonStatus.STOPPED
            self._worker_thread = None
            logger.info("Autonomous supervisor stopped cleanly.")
            return True

    def pause(self) -> bool:
        """Pause execution of background sub-routines."""
        with self._lock:
            if self._status != DaemonStatus.RUNNING:
                return False
            self._status = DaemonStatus.PAUSED
            logger.info("Autonomous supervisor paused.")
            return True

    def resume(self) -> bool:
        """Resume execution of paused background sub-routines."""
        with self._lock:
            if self._status != DaemonStatus.PAUSED:
                return False
            self._status = DaemonStatus.RUNNING
            logger.info("Autonomous supervisor resumed.")
            return True

    # ------------------------------------------------------------------
    # Stepping & Sub-Routines
    # ------------------------------------------------------------------
    def step_once(self, current_time: float | None = None) -> dict[str, Any]:
        """Execute one complete supervision tick across all sub-routines."""
        now = current_time if current_time is not None else time.time()
        results: dict[str, Any] = {
            "events_dispatched": 0,
            "goals_stepped": 0,
            "expired_locks_pruned": 0,
            "clarifications_pruned": 0,
            "memory_maintenance_run": False,
            "checkpoint_saved": False,
            "errors": [],
        }

        with self._lock:
            self._total_heartbeats += 1
            self._last_heartbeat_timestamp = now

            # 1. Proactive Event Perception Tick
            try:
                ed = getattr(self.runtime, "event_dispatcher", None)
                sched = getattr(self.runtime, "scheduler", None)
                ge = getattr(self.runtime, "goal_engine", None)
                if ed is not None:
                    dispatched = ed.poll_and_dispatch(
                        goal_scheduler=sched,
                        goal_engine=ge,
                        current_time=now,
                    )
                    results["events_dispatched"] = len(dispatched)
                    self._total_events_dispatched += len(dispatched)
                    self._last_event_dispatch_time = now
            except Exception as e:
                logger.error("Error during event perception tick: %s", e, exc_info=True)
                results["errors"].append(f"event_dispatch: {e}")
                self._total_errors += 1
                self._last_error = str(e)

            # 2. Multi-Goal Scheduling Tick
            try:
                if hasattr(self.runtime, "step_scheduled_goals"):
                    stepped = self.runtime.step_scheduled_goals(
                        max_batch_size=self.config.max_batch_goals_per_step
                    )
                    results["goals_stepped"] = len(stepped)
                    self._total_goals_stepped += len(stepped)
                    self._last_scheduler_step_time = now
            except Exception as e:
                logger.error("Error during scheduler step tick: %s", e, exc_info=True)
                results["errors"].append(f"scheduler_step: {e}")
                self._total_errors += 1
                self._last_error = str(e)

            # 3. Lock & Lease Watchdog
            try:
                lm = getattr(self.runtime, "lock_manager", None)
                if lm is not None:
                    pruned_locks = lm.prune_expired_locks(current_time=now)
                    results["expired_locks_pruned"] = pruned_locks
                    self._last_lock_prune_time = now
            except Exception as e:
                logger.error("Error during lock watchdog tick: %s", e, exc_info=True)
                results["errors"].append(f"lock_prune: {e}")
                self._total_errors += 1
                self._last_error = str(e)

            # 4. Clarification Timeout Watchdog
            try:
                cg = getattr(self.runtime, "clarification_gateway", None)
                if cg is not None:
                    pruned_reqs = cg.prune_timed_out_requests(current_time=now)
                    results["clarifications_pruned"] = pruned_reqs
                    self._last_clarification_prune_time = now
            except Exception as e:
                logger.error("Error during clarification watchdog tick: %s", e, exc_info=True)
                results["errors"].append(f"clarification_prune: {e}")
                self._total_errors += 1
                self._last_error = str(e)

            # 5. Memory Maintenance Watchdog
            try:
                if hasattr(self.runtime, "run_memory_lifecycle_pass"):
                    self.runtime.run_memory_lifecycle_pass(current_time=now)
                    results["memory_maintenance_run"] = True
                    self._last_memory_maintenance_time = now
            except Exception as e:
                logger.error("Error during memory lifecycle pass: %s", e, exc_info=True)
                results["errors"].append(f"memory_lifecycle: {e}")
                self._total_errors += 1
                self._last_error = str(e)

            # 6. Checkpoint Snapshot Watchdog
            try:
                if self.checkpoint_manager is not None:
                    self.checkpoint_manager.save_checkpoint(current_time=now)
                    results["checkpoint_saved"] = True
                    self._total_checkpoints_saved += 1
                    self._last_checkpoint_timestamp = now
                    self._last_checkpoint_time = now
            except Exception as e:
                logger.error("Error during periodic checkpoint creation: %s", e, exc_info=True)
                results["errors"].append(f"checkpoint_save: {e}")
                self._total_errors += 1
                self._last_error = str(e)

        return results

    # ------------------------------------------------------------------
    # Worker Thread Loop
    # ------------------------------------------------------------------
    def _run_loop(self) -> None:
        """Main execution loop for background supervisor worker."""
        logger.debug("Supervisor background worker loop started.")
        sleep_slice = min(0.2, self.config.heartbeat_interval_seconds)

        while not self._stop_event.is_set():
            try:
                now = time.time()
                with self._lock:
                    is_active = (self._status == DaemonStatus.RUNNING)

                if is_active:
                    self._total_heartbeats += 1
                    self._last_heartbeat_timestamp = now

                    # 1. Event Dispatch
                    if now - self._last_event_dispatch_time >= self.config.event_interval_seconds:
                        try:
                            ed = getattr(self.runtime, "event_dispatcher", None)
                            sched = getattr(self.runtime, "scheduler", None)
                            ge = getattr(self.runtime, "goal_engine", None)
                            if ed is not None:
                                dispatched = ed.poll_and_dispatch(
                                    goal_scheduler=sched,
                                    goal_engine=ge,
                                    current_time=now,
                                )
                                self._total_events_dispatched += len(dispatched)
                                self._last_event_dispatch_time = now
                        except Exception as e:
                            logger.error("Worker error in event perception: %s", e)
                            self._total_errors += 1
                            self._last_error = str(e)

                    # 2. Scheduling Step
                    if now - self._last_scheduler_step_time >= self.config.scheduler_interval_seconds:
                        try:
                            if hasattr(self.runtime, "step_scheduled_goals"):
                                stepped = self.runtime.step_scheduled_goals(
                                    max_batch_size=self.config.max_batch_goals_per_step
                                )
                                self._total_goals_stepped += len(stepped)
                                self._last_scheduler_step_time = now
                        except Exception as e:
                            logger.error("Worker error in scheduler step: %s", e)
                            self._total_errors += 1
                            self._last_error = str(e)

                    # 3. Lock Watchdog
                    if now - self._last_lock_prune_time >= self.config.lock_prune_interval_seconds:
                        try:
                            lm = getattr(self.runtime, "lock_manager", None)
                            if lm is not None:
                                lm.prune_expired_locks(current_time=now)
                                self._last_lock_prune_time = now
                        except Exception as e:
                            logger.error("Worker error in lock prune: %s", e)
                            self._total_errors += 1
                            self._last_error = str(e)

                    # 4. Clarification Watchdog
                    if now - self._last_clarification_prune_time >= self.config.clarification_interval_seconds:
                        try:
                            cg = getattr(self.runtime, "clarification_gateway", None)
                            if cg is not None:
                                cg.prune_timed_out_requests(current_time=now)
                                self._last_clarification_prune_time = now
                        except Exception as e:
                            logger.error("Worker error in clarification prune: %s", e)
                            self._total_errors += 1
                            self._last_error = str(e)

                    # 5. Memory Maintenance Watchdog
                    if now - self._last_memory_maintenance_time >= self.config.memory_interval_seconds:
                        try:
                            if hasattr(self.runtime, "run_memory_lifecycle_pass"):
                                self.runtime.run_memory_lifecycle_pass(current_time=now)
                                self._last_memory_maintenance_time = now
                        except Exception as e:
                            logger.error("Worker error in memory lifecycle: %s", e)
                            self._total_errors += 1
                            self._last_error = str(e)

                    # 6. Periodic Checkpoint Snapshot
                    if now - self._last_checkpoint_time >= self.config.checkpoint_interval_seconds:
                        try:
                            if self.checkpoint_manager is not None:
                                self.checkpoint_manager.save_checkpoint(current_time=now)
                                self._total_checkpoints_saved += 1
                                self._last_checkpoint_timestamp = now
                                self._last_checkpoint_time = now
                        except Exception as e:
                            logger.error("Worker error in checkpoint creation: %s", e)
                            self._total_errors += 1
                            self._last_error = str(e)

            except Exception as e:
                logger.error("Fatal exception in supervisor worker loop: %s", e, exc_info=True)
                with self._lock:
                    self._total_errors += 1
                    self._last_error = str(e)

            # Interruptible wait
            self._stop_event.wait(sleep_slice)

        logger.debug("Supervisor background worker loop exited.")

    # ------------------------------------------------------------------
    # Telemetry & Diagnostics
    # ------------------------------------------------------------------
    def get_telemetry(self) -> SupervisorTelemetry:
        """Assemble an aggregated telemetry and health status snapshot."""
        with self._lock:
            now = time.time()
            uptime = (now - self._started_at) if (self._started_at and self._status in (DaemonStatus.RUNNING, DaemonStatus.PAUSED)) else 0.0

            queue_depth = 0
            sched = getattr(self.runtime, "scheduler", None)
            if sched is not None:
                with sched._lock:
                    queue_depth = len(sched._tasks)

            active_locks = 0
            lm = getattr(self.runtime, "lock_manager", None)
            if lm is not None:
                with lm._lock:
                    active_locks = len(lm._locks)

            pending_clarif = 0
            cg = getattr(self.runtime, "clarification_gateway", None)
            if cg is not None:
                with cg._lock:
                    from core.scheduling_types import ClarificationStatus
                    pending_clarif = sum(1 for r in cg._requests.values() if r.status == ClarificationStatus.PENDING)

            budget_util = {}
            bm = getattr(self.runtime, "budget_manager", None)
            if bm is not None:
                with bm._lock:
                    budget_util = {
                        "active_goals_count": len(bm._active_goals),
                        "max_concurrent_goals": bm.max_concurrent_goals,
                        "global_tool_calls_rate": sum(c for _, c in bm._global_tool_call_history),
                        "global_tokens_rate": sum(c for _, c in bm._global_token_history),
                    }

            return SupervisorTelemetry(
                status=self._status,
                uptime_seconds=max(0.0, uptime),
                total_heartbeats=self._total_heartbeats,
                total_events_dispatched=self._total_events_dispatched,
                total_goals_stepped=self._total_goals_stepped,
                total_checkpoints_saved=self._total_checkpoints_saved,
                total_errors=self._total_errors,
                active_workers=1 if (self._worker_thread and self._worker_thread.is_alive()) else 0,
                scheduler_queue_depth=queue_depth,
                active_locks_count=active_locks,
                pending_clarifications_count=pending_clarif,
                budget_utilization=budget_util,
                last_heartbeat_timestamp=self._last_heartbeat_timestamp,
                last_checkpoint_timestamp=self._last_checkpoint_timestamp,
                last_error=self._last_error,
            )
