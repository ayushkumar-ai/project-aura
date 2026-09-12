"""Integration tests for Milestone 27 Autonomous Causal Fault Diagnosis & Closed-Loop Self-Healing."""

from unittest.mock import MagicMock
import pytest
import time

from core.agentic_runtime import AgenticRuntime
from core.artifact_manager import ArtifactManager
from core.artifact_store import InMemoryArtifactStore
from core.artifact_types import ArtifactType
from core.campaign_engine import CampaignEngine
from core.campaign_types import (
    CampaignDefinition,
    CampaignMilestone,
    CampaignPhase,
    CampaignStatus,
    PhaseStatus,
)
from core.fault_types import (
    FaultCategory,
    HealingStatus,
    SelfHealingResult,
)
from core.goal_engine import GoalEngine
from core.goal import GoalStatus
from core.runtime_checkpoint import RuntimeCheckpointManager
from core.self_healing_orchestrator import SelfHealingOrchestrator


def test_m27_campaign_default_auto_heal_disabled_preserves_m25():
    """Verify that auto_heal_on_failure=False preserves standard M25 failure/compensation behavior."""
    mock_eval = MagicMock()
    mock_report = MagicMock()
    mock_report.overall_score = 0.4  # Fails min 0.8
    mock_eval.evaluate.return_value = mock_report

    mock_healer = MagicMock()
    engine = CampaignEngine(evaluation_engine=mock_eval, self_healing_orchestrator=mock_healer)

    ms = CampaignMilestone(
        milestone_id="gate_1",
        title="Gate 1",
        criteria=("Score >= 0.8",),
        min_evaluation_score=0.8,
    )
    p1 = CampaignPhase(phase_id="p1", name="Phase 1", goal_ids=("g1",), milestones=(ms,))

    campaign = CampaignDefinition(
        campaign_id="camp_no_heal",
        title="No Healing Campaign",
        description="Testing backward compatibility",
        phases=(p1,),
        auto_heal_on_failure=False,  # default
    )

    engine.submit_campaign(campaign)
    res = engine.execute_campaign("camp_no_heal")

    assert res.status == CampaignStatus.FAILED
    assert "p1" in res.failed_phases
    # The healer must NOT have been invoked because auto_heal_on_failure is False
    mock_healer.heal_campaign_phase.assert_not_called()


def test_m27_campaign_auto_heal_transient_recovery():
    """Verify that auto_heal_on_failure=True heals transient failures and resumes to completion."""
    goal_eval_counts = {"g1": 0}

    class FlakyGoalEngine:
        def evaluate_goal(self, goal_id: str):
            if goal_id == "g1":
                goal_eval_counts["g1"] += 1
                if goal_eval_counts["g1"] == 1:
                    raise TimeoutError("Simulated transient connection timeout on first attempt")
            mock_res = MagicMock()
            mock_res.status = GoalStatus.COMPLETED
            return mock_res

    flaky_ge = FlakyGoalEngine()
    store = InMemoryArtifactStore()
    art_mgr = ArtifactManager(store=store)

    orchestrator = SelfHealingOrchestrator()
    engine = CampaignEngine(
        goal_engine=flaky_ge,  # type: ignore
        artifact_manager=art_mgr,
        self_healing_orchestrator=orchestrator,
    )

    p1 = CampaignPhase(phase_id="p1", name="Flaky Phase", goal_ids=("g1",))
    p2 = CampaignPhase(phase_id="p2", name="Followup Phase", goal_ids=("g2",), depends_on_phase_ids=("p1",))

    campaign = CampaignDefinition(
        campaign_id="camp_auto_heal",
        title="Auto Heal Campaign",
        description="Testing self-healing loop",
        phases=(p1, p2),
        auto_heal_on_failure=True,  # M27 feature enabled
        max_healing_attempts_per_phase=2,
    )

    engine.submit_campaign(campaign)
    res = engine.execute_campaign("camp_auto_heal")

    assert res.status == CampaignStatus.COMPLETED
    assert res.completed_phases == ("p1", "p2")
    assert goal_eval_counts["g1"] == 2  # Evaluated once (failed), healed & retried (passed)

    # Check healing history on orchestrator
    hist = orchestrator.get_healing_history("camp_auto_heal")
    assert len(hist) >= 1
    assert hist[0].status == HealingStatus.RECOVERED
    assert hist[0].campaign_resumed is True


def test_m27_campaign_auto_heal_irrecoverable_operator_escalation():
    """Verify that policy errors trigger operator escalation and gracefully abort."""
    class PolicyBlockedGoalEngine:
        def evaluate_goal(self, goal_id: str):
            raise PermissionError("Action denied by policy: permission denied for goal")

    mock_gateway = MagicMock()
    orchestrator = SelfHealingOrchestrator(clarification_gateway=mock_gateway)
    engine = CampaignEngine(
        goal_engine=PolicyBlockedGoalEngine(),  # type: ignore
        self_healing_orchestrator=orchestrator,
    )

    p1 = CampaignPhase(phase_id="p1", name="Blocked Phase", goal_ids=("g_blocked",))
    campaign = CampaignDefinition(
        campaign_id="camp_policy_blocked",
        title="Policy Blocked Campaign",
        description="Testing escalation",
        phases=(p1,),
        auto_heal_on_failure=True,
    )

    engine.submit_campaign(campaign)
    res = engine.execute_campaign("camp_policy_blocked")

    assert res.status == CampaignStatus.FAILED
    assert "p1" in res.failed_phases
    # Check operator clarification request was submitted
    mock_gateway.submit_request.assert_called_once()

    hist = orchestrator.get_healing_history("camp_policy_blocked")
    assert len(hist) == 1
    assert hist[0].status == HealingStatus.OPERATOR_ESCALATED
    assert hist[0].campaign_resumed is False


def test_m27_agentic_runtime_wiring_and_facade():
    """Verify that AgenticRuntime correctly wires and exposes M27 components."""
    runtime = AgenticRuntime()

    assert runtime.causal_fault_analyzer is not None
    assert runtime.remediation_planner is not None
    assert runtime.self_healing_orchestrator is not None
    assert runtime.campaign_engine.self_healing_orchestrator is runtime.self_healing_orchestrator
    assert runtime.checkpoint_manager.self_healing_orchestrator is runtime.self_healing_orchestrator

    # Test diagnose_failure facade
    rep = runtime.diagnose_failure(
        campaign_id="c_diag",
        phase_id="p_diag",
        goal_id="g_diag",
        error_message="Connection timed out after 30s",
    )
    assert rep.fault_category == FaultCategory.TRANSIENT_INFRASTRUCTURE

    # Test plan_remediation facade
    plan = runtime.plan_remediation(rep)
    assert len(plan.actions) > 0

    # Test heal_campaign_phase facade
    res = runtime.heal_campaign_phase(
        campaign_id="c_diag",
        phase_id="p_diag",
        goal_id="g_diag",
        error_message="Connection timed out after 30s",
    )
    assert res.status == HealingStatus.RECOVERED

    # Test history query
    history = runtime.get_healing_history("c_diag")
    assert len(history) == 1
    assert runtime.get_healing_attempt_count("c_diag", "p_diag") == 1


def test_m27_checkpoint_roundtrip_with_self_healing_state(tmp_path):
    """Verify runtime checkpoint preserves self-healing state across restarts."""
    orchestrator = SelfHealingOrchestrator()
    orchestrator.heal_campaign_phase(
        campaign_id="c_ckpt",
        phase_id="p_ckpt",
        goal_id="g_ckpt",
        error_message="Network error",
    )

    ckpt_mgr1 = RuntimeCheckpointManager(
        checkpoint_dir=tmp_path / "checkpoints",
        self_healing_orchestrator=orchestrator,
    )

    meta = ckpt_mgr1.save_checkpoint(checkpoint_id="ckpt_m27_test")
    assert meta.checkpoint_id == "ckpt_m27_test"

    # Restore in fresh environment
    new_orchestrator = SelfHealingOrchestrator()
    ckpt_mgr2 = RuntimeCheckpointManager(
        checkpoint_dir=tmp_path / "checkpoints",
        self_healing_orchestrator=new_orchestrator,
    )

    ckpt_mgr2.restore_latest_checkpoint()
    hist = new_orchestrator.get_healing_history("c_ckpt")
    assert len(hist) == 1
    assert hist[0].status == HealingStatus.RECOVERED
    assert new_orchestrator.get_attempt_count("c_ckpt", "p_ckpt") == 1
