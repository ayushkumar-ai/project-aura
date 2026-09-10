import time
from pathlib import Path
import pytest
from uuid import uuid4

from core.agent_plan import AgentPlan, AgentPlanStep, Observation, StepStatus
from core.agent_runtime import AgentRuntime
from core.agentic_runtime import AgenticRuntime, ExecutionMode
from core.approval import ApprovalGateway, ApprovalStatus
from core.autonomous_agent import AutonomousAgentExecutor
from core.clarification_gateway import ClarificationGateway
from core.daemon_types import DaemonStatus, SupervisorConfig, SupervisorTelemetry, FORBIDDEN_PRIVILEGE_KEYS
from core.event_dispatcher import ProactiveEventDispatcher
from core.file_task_state_store import FileTaskStateStore
from core.goal import Goal, GoalObservation, GoalPriority, GoalProgress, GoalStatus, GoalTrigger, TriggerType
from core.goal_adapter import GoalAdapter
from core.goal_engine import GoalEngine
from core.goal_reasoner import GoalEvaluationResult, GoalReasoner
from core.goal_scheduler import MultiGoalScheduler
from core.goal_store import InMemoryGoalStore
from core.policy import Policy
from core.provenance import TaintedValue, is_tainted, wrap_tainted
from core.resource_budget import ResourceBudgetManager
from core.resource_locks import SharedResourceLockManager
from core.runtime_checkpoint import RuntimeCheckpointManager
from core.runtime_supervisor import AutonomousSupervisor
from core.scheduling_types import (
    ClarificationRequest,
    ClarificationResponse,
    ClarificationStatus,
    ClarificationType,
    EventSubscription,
    LockType,
    ProactiveEvent,
    ResourceQuota,
)
from core.skill import Skill
from core.skill_registry import SkillRegistry
from core.strategy_types import StrategyType
from core.tool_registry import ToolRegistry
from providers.fake_model import FakeModelProvider


class WorkerTool:
    name = "worker_tool"
    description = "Executes background job"

    def __init__(self):
        self.executed_jobs: list[str] = []

    def execute(self, payload: str):
        self.executed_jobs.append(payload)
        return f"SUCCESS: {payload}"


def test_autonomous_supervisor_background_loop_and_telemetry(tmp_path: Path):
    """Scenario 1: Background supervisor executes event dispatch, multi-goal stepping, and telemetry tracking."""
    worker = WorkerTool()
    tool_reg = ToolRegistry()
    tool_reg.register(worker.name, worker)

    skill_reg = SkillRegistry()
    skill_reg.register(
        Skill(
            name="worker_skill",
            description="Executes worker tool",
            handler=lambda inp: worker.execute(str(inp)),
            tools=["worker_tool"],
        )
    )

    policy = Policy(authorized_tools={"worker_tool"})
    budget_mgr = ResourceBudgetManager(max_concurrent_goals=3, global_max_tool_calls_per_minute=50)
    lock_mgr = SharedResourceLockManager()
    clarif_gw = ClarificationGateway(default_timeout_seconds=2.0)
    event_disp = ProactiveEventDispatcher()
    scheduler = MultiGoalScheduler(budget_manager=budget_mgr, lock_manager=lock_mgr, max_concurrent_goals=3)
    state_store = FileTaskStateStore(storage_dir=str(tmp_path / "task_states"))
    ckpt_mgr = RuntimeCheckpointManager(
        checkpoint_dir=str(tmp_path / "checkpoints"),
        scheduler=scheduler,
        budget_manager=budget_mgr,
        lock_manager=lock_mgr,
        clarification_gateway=clarif_gw,
        event_dispatcher=event_disp,
    )

    sup_config = SupervisorConfig(
        heartbeat_interval_seconds=0.03,
        scheduler_interval_seconds=0.03,
        event_interval_seconds=0.03,
        lock_prune_interval_seconds=0.05,
        clarification_interval_seconds=0.05,
        checkpoint_interval_seconds=0.05,
        checkpoint_dir=str(tmp_path / "checkpoints"),
        checkpoint_retention_count=3,
        auto_recover_on_startup=False,
    )

    runtime = AgenticRuntime(
        skill_registry=skill_reg,
        policy=policy,
        budget_manager=budget_mgr,
        lock_manager=lock_mgr,
        scheduler=scheduler,
        event_dispatcher=event_disp,
        clarification_gateway=clarif_gw,
        state_store=state_store,
        checkpoint_manager=ckpt_mgr,
        supervisor_config=sup_config,
    )

    # 1. Create a Goal with success criteria and trigger
    goal = runtime.goal_engine.create_goal(
        title="Automated Event Handling",
        success_criteria=("worker_skill",),
        priority=GoalPriority.HIGH,
    )

    # Subscribe goal to event topic
    runtime.event_dispatcher.subscribe("iot/temperature", goal_id=goal.goal_id)

    # 2. Start Supervisor Daemon
    assert runtime.start_daemon(auto_recover=False) is True
    assert runtime.is_daemon_running() is True
    assert runtime.daemon_status in (DaemonStatus.STARTING, DaemonStatus.RUNNING)

    # 3. Publish an event matching the subscription
    event = ProactiveEvent(
        topic="iot/temperature",
        payload={"temp": 88.5, "alert": "HIGH_TEMP"},
        source="sensor_hub",
    )
    runtime.publish_event(event)

    # 4. Wait for daemon to step scheduler, process goal, and perform checkpoint passes
    max_wait = 3.0
    start = time.time()
    while time.time() - start < max_wait:
        updated_goal = runtime.goal_engine.get_goal(goal.goal_id)
        if updated_goal and updated_goal.status == GoalStatus.COMPLETED:
            if runtime.get_supervisor_telemetry().total_checkpoints_saved >= 1:
                break
        time.sleep(0.05)

    updated_goal = runtime.goal_engine.get_goal(goal.goal_id)
    assert updated_goal is not None
    assert updated_goal.status == GoalStatus.COMPLETED

    # 5. Check Telemetry
    telemetry = runtime.get_supervisor_telemetry()
    assert telemetry.status in (DaemonStatus.STARTING, DaemonStatus.RUNNING)
    assert telemetry.uptime_seconds > 0.0
    assert telemetry.total_heartbeats > 0
    assert telemetry.total_events_dispatched >= 1
    assert telemetry.total_goals_stepped >= 1
    assert telemetry.total_checkpoints_saved >= 1

    # 6. Stop daemon gracefully
    assert runtime.stop_daemon(timeout=2.0) is True
    assert runtime.is_daemon_running() is False
    assert runtime.daemon_status == DaemonStatus.STOPPED


def test_crash_recovery_from_checkpoint_and_task_state_store(tmp_path: Path):
    """Scenario 2: State persistence across crash simulation with checkpoint and task state restoration."""
    ckpt_dir = str(tmp_path / "crash_ckpts")
    state_dir = str(tmp_path / "crash_tasks")

    # --- PHASE 1: Runtime 1 Setup & Execution ---
    budget_mgr1 = ResourceBudgetManager(max_concurrent_goals=2)
    lock_mgr1 = SharedResourceLockManager()
    clarif_gw1 = ClarificationGateway()
    event_disp1 = ProactiveEventDispatcher()
    sched1 = MultiGoalScheduler(budget_manager=budget_mgr1, lock_manager=lock_mgr1, max_concurrent_goals=2)
    state_store1 = FileTaskStateStore(storage_dir=state_dir)
    ckpt_mgr1 = RuntimeCheckpointManager(
        checkpoint_dir=ckpt_dir,
        scheduler=sched1,
        budget_manager=budget_mgr1,
        lock_manager=lock_mgr1,
        clarification_gateway=clarif_gw1,
        event_dispatcher=event_disp1,
    )

    runtime1 = AgenticRuntime(
        budget_manager=budget_mgr1,
        lock_manager=lock_mgr1,
        scheduler=sched1,
        event_dispatcher=event_disp1,
        clarification_gateway=clarif_gw1,
        state_store=state_store1,
        checkpoint_manager=ckpt_mgr1,
    )

    # Create goals and enqueue in scheduler
    g1 = runtime1.goal_engine.create_goal(title="Recoverable Goal 1", priority=GoalPriority.HIGH)
    g2 = runtime1.goal_engine.create_goal(title="Recoverable Goal 2", priority=GoalPriority.MEDIUM)
    runtime1.scheduler.schedule_goal(g1.goal_id, priority=GoalPriority.HIGH)
    runtime1.scheduler.schedule_goal(g2.goal_id, priority=GoalPriority.MEDIUM)

    # Acquire resource lock
    lock_mgr1.acquire_lock("database://users", g1.goal_id, LockType.EXCLUSIVE_WRITE, ttl_seconds=60.0)

    # Register clarification request and response
    req = clarif_gw1.request_clarification(
        goal_id=g1.goal_id,
        task_id="task_rec_1",
        question="Select DB partition",
        options=["p1", "p2"],
    )
    clarif_gw1.submit_response(req.clarification_id, "p1")

    # Record task plan in FileTaskStateStore with TaintedValue
    tainted_out = wrap_tainted("dirty_input_payload", source_type="untrusted_web")
    obs = Observation(
        step_id="step_1",
        task_id="task_rec_1",
        skill_name="read_payload",
        output=tainted_out,
    )
    plan = AgentPlan(
        plan_id="plan_rec_1",
        task_goal="Process partitioned DB migration",
        steps=[
            AgentPlanStep(step_id="step_1", skill_name="read_payload", objective="Read payload", status=StepStatus.SUCCEEDED, result=obs),
            AgentPlanStep(step_id="step_2", skill_name="migrate", objective="Migrate partition", status=StepStatus.PENDING),
        ],
    )
    state_store1.create(
        task_id="task_rec_1",
        plan_id="plan_rec_1",
        plan=plan,
        goal_id=g1.goal_id,
    )

    # Register event subscription
    event_disp1.subscribe("sys/alert", goal_id=g2.goal_id)

    # Create manual checkpoint before "crash"
    meta = runtime1.create_checkpoint(checkpoint_id="pre_crash_ckpt_1")
    assert meta.checkpoint_id == "pre_crash_ckpt_1"
    assert meta.goal_count == 2
    assert meta.lock_count == 1
    assert meta.clarification_count == 1

    # --- PHASE 2: Simulate Crash & Runtime 2 Recovery ---
    budget_mgr2 = ResourceBudgetManager(max_concurrent_goals=2)
    lock_mgr2 = SharedResourceLockManager()
    clarif_gw2 = ClarificationGateway()
    event_disp2 = ProactiveEventDispatcher()
    sched2 = MultiGoalScheduler(budget_manager=budget_mgr2, lock_manager=lock_mgr2, max_concurrent_goals=2)
    state_store2 = FileTaskStateStore(storage_dir=state_dir)
    ckpt_mgr2 = RuntimeCheckpointManager(
        checkpoint_dir=ckpt_dir,
        scheduler=sched2,
        budget_manager=budget_mgr2,
        lock_manager=lock_mgr2,
        clarification_gateway=clarif_gw2,
        event_dispatcher=event_disp2,
    )

    # Restore checkpoint
    restored_meta = ckpt_mgr2.restore_latest_checkpoint()
    assert restored_meta is not None
    assert restored_meta.checkpoint_id == "pre_crash_ckpt_1"

    # Verify Scheduler Tasks restored
    queued = sched2.get_queued_tasks()
    assert len(queued) == 2
    queued_goal_ids = {t.goal_id for t in queued}
    assert g1.goal_id in queued_goal_ids
    assert g2.goal_id in queued_goal_ids

    # Verify Lock Manager restored
    assert lock_mgr2.is_locked("database://users", LockType.EXCLUSIVE_WRITE) is True

    # Verify Clarification Gateway restored
    active_reqs = clarif_gw2.get_pending_requests()
    # It was answered, so active_reqs is empty but answered response exists
    assert len(active_reqs) == 0
    resp = clarif_gw2.get_response(req.clarification_id)
    assert resp is not None
    assert resp.response_data == "p1"
    assert resp.goal_id == g1.goal_id

    # Verify Event Dispatcher restored
    status = event_disp2.get_status()
    assert status["active_subscriptions_count"] == 1

    # Verify FileTaskStateStore preserved plan and TaintedValue
    loaded_state = state_store2.get("task_rec_1")
    assert loaded_state is not None
    assert loaded_state.task_id == "task_rec_1"
    assert loaded_state.plan is not None
    assert len(loaded_state.plan.steps) == 2
    assert loaded_state.plan.steps[0].result is not None
    assert is_tainted(loaded_state.plan.steps[0].result.output)
    assert loaded_state.plan.steps[0].result.output.raw_value == "dirty_input_payload"


def test_corrupted_latest_checkpoint_fallback_recovery(tmp_path: Path):
    """Scenario 3: Corrupted latest checkpoint is bypassed in favor of the previous valid checkpoint."""
    ckpt_dir = str(tmp_path / "fallback_ckpts")

    sched = MultiGoalScheduler()
    ckpt_mgr = RuntimeCheckpointManager(
        checkpoint_dir=ckpt_dir,
        scheduler=sched,
    )

    # Save Checkpoint 1 (valid)
    sched.schedule_goal("goal_v1", priority=GoalPriority.HIGH)
    meta1 = ckpt_mgr.save_checkpoint(checkpoint_id="ckpt_valid_1")
    assert meta1.checkpoint_id == "ckpt_valid_1"

    time.sleep(0.05)

    # Save Checkpoint 2 (valid initially)
    sched.schedule_goal("goal_v2", priority=GoalPriority.MEDIUM)
    meta2 = ckpt_mgr.save_checkpoint(checkpoint_id="ckpt_valid_2")
    assert meta2.checkpoint_id == "ckpt_valid_2"

    # Corrupt latest checkpoint pointer and ckpt_valid_2 file
    latest_file = Path(ckpt_dir) / "latest_checkpoint.json"
    latest_file.write_text("CORRUPTED_JSON_DATA{{{", encoding="utf-8")

    ckpt2_file = Path(ckpt_dir) / "ckpt_valid_2.json"
    ckpt2_file.write_text('{"invalid": "schema"}', encoding="utf-8")

    # Restore latest checkpoint on a fresh manager
    sched_fresh = MultiGoalScheduler()
    ckpt_mgr_fresh = RuntimeCheckpointManager(
        checkpoint_dir=ckpt_dir,
        scheduler=sched_fresh,
    )

    restored = ckpt_mgr_fresh.restore_latest_checkpoint()
    assert restored is not None
    assert restored.checkpoint_id == "ckpt_valid_1"

    # Verify that goal_v1 was restored into fresh scheduler
    queued = sched_fresh.get_queued_tasks()
    assert len(queued) == 1
    assert queued[0].goal_id == "goal_v1"


def test_checkpoint_metadata_sanitization_privilege_escalation_defense(tmp_path: Path):
    """Scenario 4: Checkpoints strip forbidden authorization keys and prevent privilege escalation."""
    ckpt_dir = str(tmp_path / "secure_ckpts")
    sched = MultiGoalScheduler()
    clarif_gw = ClarificationGateway()
    ckpt_mgr = RuntimeCheckpointManager(
        checkpoint_dir=ckpt_dir,
        scheduler=sched,
        clarification_gateway=clarif_gw,
    )

    # Enqueue goal with malicious privilege injection in metadata
    malicious_meta = {
        "is_authorized": True,
        "bypass_policy": True,
        "skip_approval": True,
        "approved": "yes",
        "permission": "admin",
        "role_override": "root",
        "user_context": "guest",
        "safe_int": 42,
    }
    sched.schedule_goal("goal_exploit", priority=GoalPriority.HIGH, metadata=malicious_meta)

    # Register clarification with forbidden keys
    clarif_gw.request_clarification(
        goal_id="goal_exploit",
        task_id="task_exploit",
        question="Allow execution?",
        options=["yes", "no"],
        metadata=malicious_meta,
    )

    # Save checkpoint
    saved_meta = ckpt_mgr.save_checkpoint(checkpoint_id="ckpt_secure_1", metadata=malicious_meta)
    assert saved_meta.checkpoint_id == "ckpt_secure_1"

    # Verify JSON file on disk contains NO forbidden keys
    raw_json = (Path(ckpt_dir) / "ckpt_secure_1.json").read_text(encoding="utf-8")
    for k in FORBIDDEN_PRIVILEGE_KEYS:
        assert f'"{k}"' not in raw_json

    # Restore in fresh environment
    sched_fresh = MultiGoalScheduler()
    clarif_fresh = ClarificationGateway()
    ckpt_mgr_fresh = RuntimeCheckpointManager(
        checkpoint_dir=ckpt_dir,
        scheduler=sched_fresh,
        clarification_gateway=clarif_fresh,
    )

    ckpt_mgr_fresh.restore_latest_checkpoint()

    # Verify restored scheduler task metadata
    tasks = sched_fresh.get_queued_tasks()
    assert len(tasks) == 1
    restored_task = tasks[0]
    assert restored_task.metadata["user_context"] == "guest"
    assert restored_task.metadata["safe_int"] == 42
    for k in FORBIDDEN_PRIVILEGE_KEYS:
        assert k not in restored_task.metadata

    # Verify restored clarification metadata
    reqs = clarif_fresh.get_pending_requests()
    assert len(reqs) == 1
    assert reqs[0].metadata["user_context"] == "guest"
    for k in FORBIDDEN_PRIVILEGE_KEYS:
        assert k not in reqs[0].metadata
