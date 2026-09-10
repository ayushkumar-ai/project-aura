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
    # ---------------------------------------------------------
    # M19 Multi-Session, Real-Time Streaming & Operator Bridge
    # ---------------------------------------------------------
    def create_session(
        self,
        session_id: str | None = None,
        user_id: str = "default_user",
        metadata: dict[str, Any] | None = None,
        ttl_seconds: float | None = None,
    ) -> Any:
        """Create a new isolated session context."""
        if self.agentic_runtime is None:
            raise RuntimeError("Agentic runtime is not configured.")
        return self.agentic_runtime.create_session(
            session_id=session_id,
            user_id=user_id,
            metadata=metadata,
            ttl_seconds=ttl_seconds,
        )

    def get_session(self, session_id: str) -> Any:
        """Retrieve an existing session context by ID."""
        if self.agentic_runtime is None:
            raise RuntimeError("Agentic runtime is not configured.")
        return self.agentic_runtime.get_session(session_id=session_id)

    def close_session(self, session_id: str, reason: str = "normal") -> bool:
        """Close an active session."""
        if self.agentic_runtime is None:
            raise RuntimeError("Agentic runtime is not configured.")
        return self.agentic_runtime.close_session(session_id=session_id, reason=reason)

    def list_sessions(self, user_id: str | None = None, active_only: bool = False) -> list[Any]:
        """List metadata for registered sessions."""
        if self.agentic_runtime is None:
            raise RuntimeError("Agentic runtime is not configured.")
        return self.agentic_runtime.get_session_manager().list_sessions(
            user_id=user_id,
            active_only=active_only,
        )

    def get_session_status(self, session_id: str) -> dict[str, Any]:
        """Inspect the status and active bindings of a specific session."""
        if self.agentic_runtime is None:
            raise RuntimeError("Agentic runtime is not configured.")
        ctx = self.agentic_runtime.get_session(session_id=session_id)
        if ctx is None:
            return {"status": "not_found", "session_id": session_id}
        return {
            "session_id": ctx.session_id,
            "user_id": ctx.metadata.user_id,
            "status": ctx.status.value,
            "active_goals": list(ctx.active_goal_ids),
            "active_tasks": list(ctx.active_task_ids),
            "turn_count": len(ctx.history.turns),
            "created_at": ctx.metadata.created_at,
            "last_accessed_at": ctx.metadata.last_accessed_at,
        }

    def send_message_stream(
        self,
        user_input: str,
        session_id: str = "default",
        user_id: str = "default_user",
        mode: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Stream real-time tokens and execution progress for a session message."""
        if self.agentic_runtime is None:
            raise RuntimeError("Agentic runtime is not configured.")
        return self.agentic_runtime.send_message_stream(
            message=user_input,
            session_id=session_id,
            user_id=user_id,
            mode=mode,
            metadata=metadata,
        )

    def submit_session_goal(
        self,
        title: str,
        session_id: str = "default",
        priority: Any = None,
        metadata: dict[str, Any] | None = None,
        parent_goal_id: str | None = None,
        depends_on_goal_ids: Sequence[str] = (),
    ) -> Any:
        """Submit and schedule a goal bound to a specific session."""
        if self.agentic_runtime is None:
            raise RuntimeError("Agentic runtime is not configured.")
        return self.agentic_runtime.submit_session_goal(
            title=title,
            session_id=session_id,
            priority=priority,
            metadata=metadata,
            parent_goal_id=parent_goal_id,
            depends_on_goal_ids=depends_on_goal_ids,
        )

    def approve_action(
        self,
        approval_id: str,
        session_id: str = "default",
        operator_id: str = "operator",
        rationale: str = "",
    ) -> Any:
        """Submit an operator approval for a pending sensitive action."""
        if self.agentic_runtime is None:
            raise RuntimeError("Agentic runtime is not configured.")
        return self.agentic_runtime.approve_action(
            approval_id=approval_id,
            session_id=session_id,
            operator_id=operator_id,
            rationale=rationale,
        )

    def answer_session_clarification(
        self,
        clarification_id: str,
        response_data: Any,
        session_id: str = "default",
        operator_id: str = "operator",
    ) -> Any:
        """Submit an operator response to a pending clarification via the Operator Bridge."""
        if self.agentic_runtime is None:
            raise RuntimeError("Agentic runtime is not configured.")
        return self.agentic_runtime.answer_clarification(
            clarification_id=clarification_id,
            response_data=response_data,
            session_id=session_id,
            operator_id=operator_id,
        )

    def subscribe_events(
        self,
        subscriber_id: str | None = None,
        session_id: str | None = None,
        event_types: set[Any] | None = None,
        last_event_id: str | None = None,
    ) -> tuple[str, Any]:
        """Subscribe to real-time events on the AURA streaming gateway."""
        if self.agentic_runtime is None:
            raise RuntimeError("Agentic runtime is not configured.")
        return self.agentic_runtime.subscribe_stream(
            subscriber_id=subscriber_id,
            session_id=session_id,
            event_types=event_types,
            last_event_id=last_event_id,
        )

    # ---------------------------------------------------------
    # M20 Model Provider Health & Intelligence
    # ---------------------------------------------------------
    def get_provider_health(self) -> dict[str, Any]:
        """Retrieve sanitized provider health metrics and circuit breaker states."""
        if self.agentic_runtime is None:
            return {}
        return self.agentic_runtime.get_provider_health_telemetry()

    # ---------------------------------------------------------
    # M21 Multi-Agent Team Collaboration & Delegation Protocol
    # ---------------------------------------------------------
    def execute_team(
        self,
        task: str,
        team: Any = None,
        session_id: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a multi-agent team collaborative task across a configured topology."""
        if self.agentic_runtime is None:
            raise RuntimeError("Agentic runtime is not configured.")
        return self.agentic_runtime.execute_team(
            task=task,
            team=team,
            session_id=session_id,
            metadata=metadata,
        )

    def list_roles(self) -> list[dict[str, Any]]:
        """List all registered agent roles in the role registry."""
        if self.agentic_runtime is None:
            return []
        registry = self.agentic_runtime.get_role_registry()
        return [
            {
                "role_id": r.role_id,
                "name": r.name,
                "description": r.description,
                "system_prompt": r.system_prompt,
                "required_capabilities": [c.value for c in r.required_capabilities],
                "allowed_skills": list(r.allowed_skills),
                "temperature": r.temperature,
                "max_tokens": r.max_tokens,
                "metadata": r.metadata,
            }
            for r in registry.list_roles()
        ]

    def get_team_orchestrator(self) -> Any:
        """Return the multi-agent team orchestrator instance."""
        if self.agentic_runtime is None:
            return None
        return self.agentic_runtime.get_team_orchestrator()

    def get_role_registry(self) -> Any:
        """Return the agent role registry instance."""
        if self.agentic_runtime is None:
            return None
        return self.agentic_runtime.get_role_registry()
