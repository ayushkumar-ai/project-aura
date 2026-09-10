from collections.abc import Sequence
from pathlib import Path
from typing import Any

from core.models import AURARequest
from core.orchestrator import Orchestrator
from core.scheduling_types import ProactiveEvent


class AURA:
    """Persistent application-level runtime for Project AURA."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        agentic_runtime: Any | None = None,
    ):
        self.orchestrator = orchestrator
        self.agentic_runtime = (
            agentic_runtime
            if agentic_runtime is not None
            else getattr(orchestrator, "agentic_runtime", None)
        )

    def run(self, user_input: str):
        """Run a user input through the persistent AURA runtime."""
        request = AURARequest(user_input=user_input)
        return self.run_request(request)

    def run_request(self, request: AURARequest):
        """Run a complete AURA request through the persistent runtime."""
        return self.orchestrator.run(request)

    def run_task(
        self,
        task: str,
        task_id: str | None = None,
        timeout: float | None = None,
    ):
        """Run a multi-step agentic task through the agentic runtime."""
        if self.agentic_runtime is None:
            raise RuntimeError("Agentic runtime is not configured.")
        return self.agentic_runtime.execute_task(
            task=task,
            task_id=task_id,
            timeout=timeout,
        )

    # ---------------------------------------------------------
    # M18 Autonomous Supervision & Daemon Controls
    # ---------------------------------------------------------
    def start_daemon(self, auto_recover: bool | None = None) -> bool:
        """Start the background autonomous supervisor daemon."""
        if self.agentic_runtime is None:
            raise RuntimeError("Agentic runtime is not configured.")
        return self.agentic_runtime.start_daemon(auto_recover=auto_recover)

    def stop_daemon(self, timeout: float | None = None) -> bool:
        """Gracefully stop the background autonomous supervisor daemon."""
        if self.agentic_runtime is None:
            return False
        return self.agentic_runtime.stop_daemon(timeout=timeout)

    def is_daemon_running(self) -> bool:
        """Check if the background autonomous supervisor daemon is actively running."""
        if self.agentic_runtime is None:
            return False
        return self.agentic_runtime.is_daemon_running()

    def publish_event(
        self,
        topic: str,
        payload: Any,
        source: str = "user",
        is_untrusted: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Publish a proactive event into AURA's event dispatcher."""
        if self.agentic_runtime is None:
            raise RuntimeError("Agentic runtime is not configured.")
        event = ProactiveEvent(
            topic=topic,
            payload=payload,
            source=source,
            is_untrusted=is_untrusted,
            metadata=metadata or {},
        )
        return self.agentic_runtime.publish_event(event)

    def submit_goal(
        self,
        title: str,
        priority: Any = None,
        metadata: dict[str, Any] | None = None,
        parent_goal_id: str | None = None,
        depends_on_goal_ids: Sequence[str] = (),
    ) -> Any:
        """Submit and schedule a new goal in the goal engine and scheduler."""
        if self.agentic_runtime is None:
            raise RuntimeError("Agentic runtime is not configured.")
        from core.goal import GoalPriority
        eff_priority = priority if priority is not None else GoalPriority.MEDIUM
        goal = self.agentic_runtime.goal_engine.create_goal(
            title=title,
            priority=eff_priority,
            metadata=metadata,
            parent_goal_id=parent_goal_id,
            depends_on_goal_ids=depends_on_goal_ids,
        )
        self.agentic_runtime.scheduler.schedule_goal(goal.goal_id, priority=goal.priority)
        return goal

    def answer_clarification(
        self,
        clarification_id: str,
        response_data: Any,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Submit a user response to a pending clarification request."""
        if self.agentic_runtime is None:
            raise RuntimeError("Agentic runtime is not configured.")
        gateway = self.agentic_runtime.get_clarification_gateway()
        resp = gateway.submit_response(
            clarification_id=clarification_id,
            response_data=response_data,
            metadata=metadata,
        )
        return resp is not None

    def save_state_checkpoint(
        self,
        checkpoint_id: str | None = None,
        is_clean_shutdown: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Persist an atomic session checkpoint of the entire runtime state."""
        if self.agentic_runtime is None:
            raise RuntimeError("Agentic runtime is not configured.")
        return self.agentic_runtime.create_checkpoint(
            checkpoint_id=checkpoint_id,
            is_clean_shutdown=is_clean_shutdown,
            metadata=metadata,
        )

    def restore_state_checkpoint(
        self,
        checkpoint_path: str | Path | None = None,
    ) -> Any:
        """Restore runtime session state from a checkpoint."""
        if self.agentic_runtime is None:
            raise RuntimeError("Agentic runtime is not configured.")
        return self.agentic_runtime.restore_checkpoint(checkpoint_path=checkpoint_path)

    def get_health_status(self) -> dict[str, Any]:
        """Query aggregated system health, telemetry, and queue diagnostics."""
        if self.agentic_runtime is None:
            return {"status": "unconfigured", "agentic": False}
        telemetry = self.agentic_runtime.get_supervisor_telemetry()
        return {
            "status": telemetry.status.value,
            "uptime_seconds": telemetry.uptime_seconds,
            "total_heartbeats": telemetry.total_heartbeats,
            "total_events_dispatched": telemetry.total_events_dispatched,
            "total_goals_stepped": telemetry.total_goals_stepped,
            "total_checkpoints_saved": telemetry.total_checkpoints_saved,
            "total_errors": telemetry.total_errors,
            "active_workers": telemetry.active_workers,
            "scheduler_queue_depth": telemetry.scheduler_queue_depth,
            "active_locks_count": telemetry.active_locks_count,
            "pending_clarifications_count": telemetry.pending_clarifications_count,
            "budget_utilization": telemetry.budget_utilization,
            "last_heartbeat_timestamp": telemetry.last_heartbeat_timestamp,
            "last_checkpoint_timestamp": telemetry.last_checkpoint_timestamp,
            "last_error": telemetry.last_error,
        }
