"""Unit tests for CampaignEngine Master Coordinator (M25)."""

from unittest.mock import MagicMock
import pytest
from core.artifact_manager import ArtifactManager
from core.artifact_store import InMemoryArtifactStore
from core.artifact_types import ArtifactType
from core.campaign_engine import CampaignEngine
from core.campaign_types import (
    ArtifactContract,
    CampaignDefinition,
    CampaignMilestone,
    CampaignPhase,
    CampaignStatus,
    DataflowBinding,
    PhaseStatus,
)
from core.goal_engine import GoalEngine
from core.goal_scheduler import MultiGoalScheduler
from core.tracing import Tracer
from evaluation.engine import EvaluationEngine


def test_campaign_submission_and_status_query():
    engine = CampaignEngine()

    p1 = CampaignPhase(phase_id="p1", name="Phase 1", goal_ids=("g1",))
    c = CampaignDefinition(
        campaign_id="camp_test_1",
        title="Test Campaign",
        description="Testing engine submission",
        phases=(p1,),
    )

    submitted = engine.submit_campaign(c)
    assert submitted.campaign_id == "camp_test_1"

    status = engine.get_campaign_status("camp_test_1")
    assert status["status"] == "scheduled"
    assert status["title"] == "Test Campaign"

    # Duplicate submission rejection
    with pytest.raises(ValueError, match="already registered"):
        engine.submit_campaign(c)


def test_campaign_multi_phase_execution_with_dataflow():
    store = InMemoryArtifactStore()
    art_mgr = ArtifactManager(store=store)
    engine = CampaignEngine(artifact_manager=art_mgr)

    p1 = CampaignPhase(phase_id="p1", name="Extraction Phase", goal_ids=("g_extract",))
    p2 = CampaignPhase(
        phase_id="p2",
        name="Analysis Phase",
        goal_ids=("g_analyze",),
        depends_on_phase_ids=("p1",),
    )

    binding = DataflowBinding(
        binding_id="b_extract_to_analyze",
        source_goal_id="g_extract",
        target_goal_id="g_analyze",
        source_artifact_name="*",
        target_input_key="extracted_data",
    )

    campaign = CampaignDefinition(
        campaign_id="camp_pipeline",
        title="Extraction & Analysis Pipeline",
        description="Two-phase mission",
        phases=(p1, p2),
        dataflows=(binding,),
    )

    engine.submit_campaign(campaign)
    res = engine.execute_campaign("camp_pipeline")

    assert res.status == CampaignStatus.COMPLETED
    assert res.completed_phases == ("p1", "p2")
    assert len(res.produced_artifact_ids) >= 2


def test_campaign_milestone_evaluation_gate_failure_and_compensation():
    eval_engine = MagicMock()
    # Mock evaluation engine returning a failing score
    mock_report = MagicMock()
    mock_report.overall_score = 0.5  # Below 0.8 min threshold
    eval_engine.evaluate.return_value = mock_report

    engine = CampaignEngine(evaluation_engine=eval_engine)

    ms = CampaignMilestone(
        milestone_id="gate_1",
        title="High Precision Gate",
        criteria=("Score >= 0.8",),
        min_evaluation_score=0.8,
    )
    p1 = CampaignPhase(phase_id="p1", name="Precision Phase", goal_ids=("g1",), milestones=(ms,))

    campaign = CampaignDefinition(
        campaign_id="camp_milestone_fail",
        title="Failing Milestone Campaign",
        description="Testing milestone gate failure",
        phases=(p1,),
        auto_compensate_on_failure=True,
    )

    engine.submit_campaign(campaign)
    res = engine.execute_campaign("camp_milestone_fail")

    assert res.status == CampaignStatus.FAILED
    assert "p1" in res.failed_phases
    assert "p1" in res.compensated_phases
    assert res.error is not None
    assert "failed gate" in res.error


def test_campaign_cancellation():
    engine = CampaignEngine()
    p1 = CampaignPhase(phase_id="p1", name="P1", goal_ids=("g1",))
    campaign = CampaignDefinition(campaign_id="camp_cancel", title="To Cancel", description="", phases=(p1,))

    engine.submit_campaign(campaign)
    assert engine.cancel_campaign("camp_cancel")
    status = engine.get_campaign_status("camp_cancel")
    assert status["status"] == "cancelled"

    # Cancelling again returns False
    assert not engine.cancel_campaign("camp_cancel")


def test_campaign_manual_rollback():
    engine = CampaignEngine()
    p1 = CampaignPhase(phase_id="p1", name="P1", goal_ids=("g1",))
    campaign = CampaignDefinition(campaign_id="camp_rb", title="To Rollback", description="", phases=(p1,))

    engine.submit_campaign(campaign)
    res = engine.execute_campaign("camp_rb")
    assert res.status == CampaignStatus.COMPLETED

    # Manual rollback
    rollback_results = engine.rollback_campaign("camp_rb")
    assert len(rollback_results) >= 1
    status = engine.get_campaign_status("camp_rb")
    assert status["status"] == "failed"
