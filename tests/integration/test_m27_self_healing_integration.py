"""Integration tests for Milestone 27 Autonomous Causal Fault Diagnosis & Closed-Loop Self-Healing."""

from unittest.mock import MagicMock
import pytest
import time

from core.agentic_runtime import AgenticRuntime
from core.artifact_manager import ArtifactManager
from core.artifact_store import InMemoryArtifactStore
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
from core.goal import GoalStatus
from core.self_healing_orchestrator import SelfHealingOrchestrator


def test_m27_campaign_auto_heal_transient_recovery_integration():
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
        campaign_id="camp_auto_heal_integ",
        title="Auto Heal Campaign",
        description="Testing self-healing loop",
        phases=(p1, p2),
        auto_heal_on_failure=True,  # M27 feature enabled
        max_healing_attempts_per_phase=2,
    )

    engine.submit_campaign(campaign)
    res = engine.execute_campaign("camp_auto_heal_integ")

    assert res.status == CampaignStatus.COMPLETED
    assert "p1" in res.completed_phases
    assert "p2" in res.completed_phases
    assert goal_eval_counts["g1"] == 2  # Proves retry happened


def test_m27_campaign_unrecoverable_triggers_compensation_integration():
    """Verify that unrecoverable failures trigger saga rollback compensation."""
    class FailingGoalEngine:
        def evaluate_goal(self, goal_id: str):
            if goal_id == "g1":
                mock_res = MagicMock()
                mock_res.status = GoalStatus.COMPLETED
                return mock_res
            if goal_id == "g2":
                raise PermissionError("Unrecoverable policy violation: forbidden operation")
            mock_res = MagicMock()
            mock_res.status = GoalStatus.COMPLETED
            return mock_res

    failing_ge = FailingGoalEngine()
    store = InMemoryArtifactStore()
    art_mgr = ArtifactManager(store=store)

    orchestrator = SelfHealingOrchestrator()
    engine = CampaignEngine(
        goal_engine=failing_ge,  # type: ignore
        artifact_manager=art_mgr,
        self_healing_orchestrator=orchestrator,
    )

    p1 = CampaignPhase(phase_id="p1", name="Phase 1", goal_ids=("g1",))
    p2 = CampaignPhase(phase_id="p2", name="Phase 2 (Failing)", goal_ids=("g2",), depends_on_phase_ids=("p1",))

    campaign = CampaignDefinition(
        campaign_id="camp_fail_compensate_integ",
        title="Compensation Campaign",
        description="Testing saga rollback on unrecoverable failure",
        phases=(p1, p2),
        auto_heal_on_failure=True,
    )

    engine.submit_campaign(campaign)
    res = engine.execute_campaign("camp_fail_compensate_integ")

    assert res.status == CampaignStatus.FAILED
    assert "p2" in res.failed_phases
