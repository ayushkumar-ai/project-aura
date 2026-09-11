"""Unit tests for MissionGraph DAG Engine (M25)."""

import pytest
from core.campaign_types import CampaignPhase, PhaseStatus, MAX_CAMPAIGN_PHASES
from core.mission_graph import MissionGraph


def test_mission_graph_acyclic_construction_and_topological_sort():
    p1 = CampaignPhase(phase_id="p1", name="Data Ingestion", goal_ids=("g1",))
    p2 = CampaignPhase(phase_id="p2", name="Model Training", goal_ids=("g2",), depends_on_phase_ids=("p1",))
    p3 = CampaignPhase(phase_id="p3", name="Validation", goal_ids=("g3",), depends_on_phase_ids=("p2",))
    p4 = CampaignPhase(phase_id="p4", name="Deployment", goal_ids=("g4",), depends_on_phase_ids=("p3",))

    mg = MissionGraph([p1, p2, p3, p4])
    mg.validate_graph()

    order = mg.get_topological_order()
    assert order == ["p1", "p2", "p3", "p4"]


def test_mission_graph_cycle_rejection():
    # Direct cycle: p1 -> p2 -> p1
    p1 = CampaignPhase(phase_id="p1", name="P1", goal_ids=("g1",), depends_on_phase_ids=("p2",))
    p2 = CampaignPhase(phase_id="p2", name="P2", goal_ids=("g2",), depends_on_phase_ids=("p1",))

    mg = MissionGraph([p1, p2])
    with pytest.raises(ValueError, match="Cyclic dependency detected"):
        mg.validate_graph()


def test_mission_graph_self_dependency_rejection():
    p1 = CampaignPhase(phase_id="p1", name="P1", goal_ids=("g1",), depends_on_phase_ids=("p1",))
    mg = MissionGraph([p1])
    with pytest.raises(ValueError, match="Self-dependency detected"):
        mg.validate_graph()


def test_mission_graph_missing_dependency_rejection():
    p1 = CampaignPhase(phase_id="p1", name="P1", goal_ids=("g1",), depends_on_phase_ids=("non_existent",))
    mg = MissionGraph([p1])
    with pytest.raises(ValueError, match="depends on non-existent phase"):
        mg.validate_graph()


def test_mission_graph_ready_phases_discovery():
    p1 = CampaignPhase(phase_id="p1", name="P1", goal_ids=("g1",))
    p2 = CampaignPhase(phase_id="p2", name="P2", goal_ids=("g2",))
    p3 = CampaignPhase(phase_id="p3", name="P3", goal_ids=("g3",), depends_on_phase_ids=("p1", "p2"))

    mg = MissionGraph([p1, p2, p3])
    ready = mg.get_ready_phases()
    assert len(ready) == 2
    assert {p.phase_id for p in ready} == {"p1", "p2"}

    # Complete p1
    mg.update_phase_status("p1", PhaseStatus.COMPLETED)
    ready = mg.get_ready_phases()
    assert len(ready) == 1
    assert ready[0].phase_id == "p2"

    # Complete p2
    mg.update_phase_status("p2", PhaseStatus.COMPLETED)
    ready = mg.get_ready_phases()
    assert len(ready) == 1
    assert ready[0].phase_id == "p3"


def test_mission_graph_contingency_branch_injection():
    p1 = CampaignPhase(phase_id="p1", name="Primary Phase", goal_ids=("g1",))
    p2 = CampaignPhase(phase_id="p2", name="Downstream Phase", goal_ids=("g2",), depends_on_phase_ids=("p1",))

    mg = MissionGraph([p1, p2])

    contingency = CampaignPhase(
        phase_id="p1_fallback",
        name="Contingency Fallback",
        goal_ids=("g1_alt",),
        depends_on_phase_ids=(),
    )

    mg.inject_contingency_branch(contingency, failed_phase_id="p1")
    assert mg.get_phase("p1_fallback") is not None
    assert mg.get_phase("p1_fallback").is_contingency
    assert mg.get_phase("p1_fallback").contingency_for_phase_id == "p1"


def test_mission_graph_terminal_and_success_states():
    p1 = CampaignPhase(phase_id="p1", name="P1", goal_ids=("g1",))
    p2 = CampaignPhase(phase_id="p2", name="P2", goal_ids=("g2",), depends_on_phase_ids=("p1",))

    mg = MissionGraph([p1, p2])
    assert not mg.is_terminal()
    assert not mg.is_successful()

    mg.update_phase_status("p1", PhaseStatus.COMPLETED)
    mg.update_phase_status("p2", PhaseStatus.COMPLETED)
    assert mg.is_terminal()
    assert mg.is_successful()
    assert not mg.has_failed_phases()


def test_mission_graph_failed_phase_tracking():
    p1 = CampaignPhase(phase_id="p1", name="P1", goal_ids=("g1",))
    mg = MissionGraph([p1])
    mg.update_phase_status("p1", PhaseStatus.FAILED, error="Task crashed")
    assert mg.is_terminal()
    assert not mg.is_successful()
    assert mg.has_failed_phases()


def test_mission_graph_phase_removal():
    p1 = CampaignPhase(phase_id="p1", name="P1", goal_ids=("g1",))
    p2 = CampaignPhase(phase_id="p2", name="P2", goal_ids=("g2",), depends_on_phase_ids=("p1",))
    mg = MissionGraph([p1, p2])

    assert mg.remove_phase("p1")
    assert mg.get_phase("p1") is None
    # Downstream dependency should be cleaned up
    assert mg.get_phase("p2").depends_on_phase_ids == ()
