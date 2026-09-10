import pytest
from pathlib import Path
from unittest.mock import MagicMock

from app.aura import AURA
from core.models import AURARequest, AURAResponse
from core.daemon_types import DaemonStatus, SupervisorTelemetry, CheckpointMetadata, SupervisorConfig
from core.agentic_runtime import AgenticRuntime
from core.orchestrator import Orchestrator
from core.policy import Policy
from core.goal import GoalPriority
from providers.fake_model import FakeModelProvider


def test_aura_without_agentic_runtime():
    orchestrator = MagicMock(spec=Orchestrator)
    orchestrator.agentic_runtime = None
    app = AURA(orchestrator=orchestrator)

    assert app.is_daemon_running() is False
    assert app.stop_daemon() is False
    assert app.get_health_status() == {"status": "unconfigured", "agentic": False}

    with pytest.raises(RuntimeError, match="Agentic runtime is not configured"):
        app.run_task("do task")
    with pytest.raises(RuntimeError, match="Agentic runtime is not configured"):
        app.start_daemon()
    with pytest.raises(RuntimeError, match="Agentic runtime is not configured"):
        app.publish_event("test_topic", {"key": "val"})
    with pytest.raises(RuntimeError, match="Agentic runtime is not configured"):
        app.submit_goal("test goal")
    with pytest.raises(RuntimeError, match="Agentic runtime is not configured"):
        app.answer_clarification("req_1", "response")
    with pytest.raises(RuntimeError, match="Agentic runtime is not configured"):
        app.save_state_checkpoint()
    with pytest.raises(RuntimeError, match="Agentic runtime is not configured"):
        app.restore_state_checkpoint()


def test_aura_m18_lifecycle_and_operations(tmp_path: Path):
    runtime = AgenticRuntime(
        model=FakeModelProvider(),
        policy=Policy(),
        supervisor_config=SupervisorConfig(
            checkpoint_dir=str(tmp_path / "aura_ckpts"),
            heartbeat_interval_seconds=0.1,
            scheduler_interval_seconds=0.1,
            event_interval_seconds=0.1,
        ),
    )
    orchestrator = MagicMock(spec=Orchestrator)
    app = AURA(orchestrator=orchestrator, agentic_runtime=runtime)

    # Health before daemon start
    health = app.get_health_status()
    assert health["status"] == "stopped"
    assert health["uptime_seconds"] == 0.0

    # Publish event
    count = app.publish_event("sensor/test", {"temp": 25.0}, source="sensor_agent")
    assert count == 0  # No subscribers registered yet, but event queued

    # Submit goal
    goal = app.submit_goal("Optimize Resource Allocation", priority=GoalPriority.HIGH)
    assert goal.title == "Optimize Resource Allocation"
    assert goal.priority == GoalPriority.HIGH

    # Clarification gateway
    gateway = runtime.get_clarification_gateway()
    req = gateway.request_clarification(
        goal_id=goal.goal_id,
        task_id="task_123",
        question="Confirm optimization parameter",
        options=["Option A", "Option B"],
    )
    answered = app.answer_clarification(req.clarification_id, "Option A")
    assert answered is True

    # Save state checkpoint
    ckpt_meta = app.save_state_checkpoint(checkpoint_id="manual_ckpt_1")
    assert isinstance(ckpt_meta, CheckpointMetadata)
    assert ckpt_meta.checkpoint_id == "manual_ckpt_1"
    assert ckpt_meta.goal_count >= 1

    # Restore state checkpoint
    restored = app.restore_state_checkpoint()
    assert restored is not None
    assert restored.checkpoint_id == "manual_ckpt_1"

    # Start and stop daemon
    assert app.is_daemon_running() is False
    started = app.start_daemon(auto_recover=False)
    assert started is True
    assert app.is_daemon_running() is True

    health_running = app.get_health_status()
    assert health_running["status"] in ("starting", "running")

    stopped = app.stop_daemon(timeout=2.0)
    assert stopped is True
    assert app.is_daemon_running() is False
