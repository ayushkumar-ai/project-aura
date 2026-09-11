"""Unit tests for AURA Façade Milestone 25 APIs."""

from unittest.mock import MagicMock
import pytest
from app.aura import AURA
from core.agentic_runtime import AgenticRuntime
from core.campaign_types import CampaignDefinition, CampaignPhase, CampaignStatus


def test_aura_facade_campaign_lifecycle():
    runtime = AgenticRuntime()
    orch = MagicMock()
    aura = AURA(orchestrator=orch, agentic_runtime=runtime)

    p1 = CampaignPhase(phase_id="p1", name="Research Phase", goal_ids=("g_res",))
    p2 = CampaignPhase(phase_id="p2", name="Synthesis Phase", goal_ids=("g_synth",), depends_on_phase_ids=("p1",))

    defn = aura.submit_campaign(
        definition_or_title="Autonomous Research Campaign",
        description="End-to-end multi-phase mission",
        phases=[p1, p2],
    )
    assert defn.title == "Autonomous Research Campaign"
    assert len(defn.phases) == 2

    # Status check
    status = aura.get_campaign_status(defn.campaign_id)
    assert status["status"] == "scheduled"

    # List campaigns
    all_campaigns = aura.list_campaigns()
    assert len(all_campaigns) == 1
    assert all_campaigns[0]["campaign_id"] == defn.campaign_id

    # Execute campaign
    result = aura.execute_campaign(defn.campaign_id)
    assert result.status == CampaignStatus.COMPLETED
    assert result.completed_phases == ("p1", "p2")

    # Rollback campaign
    rb_results = aura.rollback_campaign(defn.campaign_id)
    assert len(rb_results) >= 1
