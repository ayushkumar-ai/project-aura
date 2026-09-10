import time
import pytest
from pathlib import Path

from core.agentic_runtime import AgenticRuntime, ExecutionMode
from core.daemon_types import DaemonStatus
from core.file_task_state_store import FileTaskStateStore
from core.models import AURARequest
from core.skill_registry import SkillRegistry, Skill
from core.task_planner import ExecutionPlan, PlanStep
from core.policy import Policy, PolicyDecision


def dummy_echo_handler(input_data, context=None):
    msg = input_data.get("message", "hello") if isinstance(input_data, dict) else str(input_data)
    return f"echo: {msg}"


def test_agentic_runtime_daemon_controls(tmp_path):
    ckpt_dir = tmp_path / "runtime_ckpts"
    runtime = AgenticRuntime()
    runtime.supervisor.config = runtime.supervisor.config.__class__(
        checkpoint_dir=str(ckpt_dir),
        shutdown_timeout_seconds=2.0,
    )
    runtime.checkpoint_manager.checkpoint_dir = ckpt_dir

    assert runtime.is_daemon_running() is False
    assert runtime.daemon_status == DaemonStatus.STOPPED

    # Start daemon
    assert runtime.start_daemon(auto_recover=False) is True
    assert runtime.is_daemon_running() is True
    assert runtime.daemon_status in (DaemonStatus.STARTING, DaemonStatus.RUNNING)

    # Telemetry
    telemetry = runtime.get_supervisor_telemetry()
    assert telemetry.status in (DaemonStatus.STARTING, DaemonStatus.RUNNING)

    # Stop daemon
    assert runtime.stop_daemon() is True
    assert runtime.is_daemon_running() is False
    assert runtime.daemon_status == DaemonStatus.STOPPED


def test_agentic_runtime_checkpoint_integration(tmp_path):
    ckpt_dir = tmp_path / "ckpt_int"
    runtime = AgenticRuntime()
    runtime.checkpoint_manager.checkpoint_dir = ckpt_dir

    # Create a goal
    goal = runtime.goal_engine.create_goal(title="Test Goal Checkpoint")
    runtime.scheduler.schedule_goal(goal.goal_id)

    # Save checkpoint
    meta = runtime.create_checkpoint(checkpoint_id="manual_ckpt_1", is_clean_shutdown=True)
    assert meta.checkpoint_id == "manual_ckpt_1"
    assert meta.goal_count >= 1

    # Clear scheduler
    runtime.scheduler._tasks.clear()
    assert len(runtime.scheduler._tasks) == 0

    # Restore checkpoint
    restored = runtime.restore_checkpoint()
    assert restored is not None
    assert restored.checkpoint_id == "manual_ckpt_1"
    assert goal.goal_id in runtime.scheduler._tasks


def test_agentic_runtime_with_file_task_state_store(tmp_path):
    tasks_dir = tmp_path / "file_tasks"
    state_store = FileTaskStateStore(tasks_dir)
    registry = SkillRegistry()
    registry.register(Skill(name="echo", description="Echo skill", handler=dummy_echo_handler))

    runtime = AgenticRuntime(
        skill_registry=registry,
        state_store=state_store,
        default_mode=ExecutionMode.STANDARD_WORKFLOW,
    )

    plan = ExecutionPlan(
        plan_id="plan_echo_1",
        steps=(PlanStep(step_id="step_1", skill_name="echo", input_data={"message": "test"}),),
    )

    result = runtime.execute(task=plan)
    assert result.success is True
    assert result.task_id is not None
    assert state_store.exists(result.task_id) is True
    saved_state = state_store.get(result.task_id)
    assert saved_state.is_completed() is True
