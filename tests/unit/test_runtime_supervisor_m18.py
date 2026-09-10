import time
import pytest

from core.daemon_types import DaemonStatus, SupervisorConfig, SupervisorTelemetry
from core.runtime_supervisor import AutonomousSupervisor
from core.runtime_checkpoint import RuntimeCheckpointManager
from core.agentic_runtime import AgenticRuntime
from core.scheduling_types import ProactiveEvent, LockType
from core.goal import GoalPriority


def test_supervisor_lifecycle_start_stop_pause_resume(tmp_path):
    runtime = AgenticRuntime()
    ckpt_dir = tmp_path / "sup_ckpts"
    config = SupervisorConfig(
        heartbeat_interval_seconds=0.05,
        checkpoint_dir=str(ckpt_dir),
        shutdown_timeout_seconds=2.0,
    )
    supervisor = AutonomousSupervisor(runtime=runtime, config=config)

    assert supervisor.status == DaemonStatus.STOPPED
    assert supervisor.is_running() is False

    # Start
    assert supervisor.start(auto_recover=False) is True
    assert supervisor.is_running() is True
    assert supervisor.status == DaemonStatus.RUNNING

    # Pause
    assert supervisor.pause() is True
    assert supervisor.status == DaemonStatus.PAUSED
    assert supervisor.is_running() is True

    # Resume
    assert supervisor.resume() is True
    assert supervisor.status == DaemonStatus.RUNNING

    # Stop
    assert supervisor.stop() is True
    assert supervisor.status == DaemonStatus.STOPPED
    assert supervisor.is_running() is False


def test_supervisor_idempotent_start_and_stop():
    runtime = AgenticRuntime()
    supervisor = AutonomousSupervisor(runtime=runtime)

    assert supervisor.start(auto_recover=False) is True
    # Second start should be rejected gracefully
    assert supervisor.start(auto_recover=False) is False

    assert supervisor.stop() is True
    # Second stop should be rejected gracefully
    assert supervisor.stop() is False


def test_supervisor_step_once_deterministic(tmp_path):
    runtime = AgenticRuntime()
    ckpt_dir = tmp_path / "step_ckpts"
    config = SupervisorConfig(checkpoint_dir=str(ckpt_dir))
    supervisor = AutonomousSupervisor(runtime=runtime, config=config)

    # Add an event subscription and queued event
    runtime.event_dispatcher.subscribe("test.*", goal_id="g_test")
    runtime.event_dispatcher.publish_event(ProactiveEvent(topic="test.run", payload={"val": 1}))

    # Add an expired lock
    runtime.lock_manager.acquire_lock("resource://test", goal_id="g_test", ttl_seconds=0.01)
    time.sleep(0.02)

    # Step once
    result = supervisor.step_once()
    assert result["events_dispatched"] == 1
    assert result["expired_locks_pruned"] >= 1
    assert result["checkpoint_saved"] is True
    assert len(result["errors"]) == 0

    tel = supervisor.get_telemetry()
    assert tel.total_heartbeats >= 1
    assert tel.total_events_dispatched >= 1
    assert tel.total_checkpoints_saved >= 1


def test_worker_failure_isolation():
    runtime = AgenticRuntime()

    # Intentionally mock a sub-component to raise an exception
    class FailingDispatcher:
        def poll_and_dispatch(self, **kwargs):
            raise RuntimeError("Dispatcher hardware failure")

    runtime.event_dispatcher = FailingDispatcher()
    supervisor = AutonomousSupervisor(runtime=runtime)

    result = supervisor.step_once()
    # Step should isolate error without crashing
    assert len(result["errors"]) == 1
    assert "Dispatcher hardware failure" in result["errors"][0]

    tel = supervisor.get_telemetry()
    assert tel.total_errors == 1
    assert "Dispatcher hardware failure" in (tel.last_error or "")
