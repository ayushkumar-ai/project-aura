"""Unit tests for Milestone 27 Self-Healing Orchestrator."""

import pytest
import time
from unittest.mock import MagicMock
from core.fault_types import (
    ConfidenceLevel,
    FaultCategory,
    HealingBudget,
    HealingStatus,
)
from core.self_healing_orchestrator import SelfHealingOrchestrator


def test_orchestrator_transient_retry_recovery():
    orchestrator = SelfHealingOrchestrator()
    res = orchestrator.heal_campaign_phase(
        campaign_id="camp_1",
        phase_id="phase_1",
        goal_id="goal_1",
        error_message="Network request timed out after 5000ms",
    )

    assert res.status == HealingStatus.RECOVERED
    assert res.campaign_resumed is True
    assert res.actions_attempted >= 1
    assert res.actions_succeeded >= 1
    assert res.attempt_number == 1

    # Check history
    history = orchestrator.get_healing_history("camp_1")
    assert len(history) == 1
    assert history[0].status == HealingStatus.RECOVERED


def test_orchestrator_skill_defect_deprecates_and_recovers():
    mock_registry = MagicMock()
    mock_registry.deprecate_skill.return_value = True

    orchestrator = SelfHealingOrchestrator(dynamic_skill_registry=mock_registry)
    res = orchestrator.heal_campaign_phase(
        campaign_id="camp_1",
        phase_id="phase_1",
        goal_id="goal_1",
        error_message="execute_skill failed: dynamic_skill raised KeyError",
        context={"skill_name": "calc_tool"},
    )

    assert res.status == HealingStatus.RECOVERED
    assert res.campaign_resumed is True
    mock_registry.deprecate_skill.assert_called_once()


def test_orchestrator_policy_block_operator_escalation():
    mock_gateway = MagicMock()
    orchestrator = SelfHealingOrchestrator(clarification_gateway=mock_gateway)

    res = orchestrator.heal_campaign_phase(
        campaign_id="camp_1",
        phase_id="phase_1",
        goal_id="goal_1",
        error_message="Action denied by policy: permission denied",
    )

    assert res.status == HealingStatus.OPERATOR_ESCALATED
    assert res.campaign_resumed is False
    mock_gateway.submit_request.assert_called_once()


def test_orchestrator_max_attempts_budget_exhaustion():
    orchestrator = SelfHealingOrchestrator(default_budget=HealingBudget(max_attempts=2))

    # Attempt 1
    res1 = orchestrator.heal_campaign_phase(
        campaign_id="camp_1",
        phase_id="phase_1",
        goal_id="goal_1",
        error_message="Timeout error",
    )
    assert res1.status == HealingStatus.RECOVERED

    # Attempt 2
    res2 = orchestrator.heal_campaign_phase(
        campaign_id="camp_1",
        phase_id="phase_1",
        goal_id="goal_1",
        error_message="Timeout error",
    )
    assert res2.attempt_number == 2

    # Attempt 3 — should exceed max_attempts=2
    res3 = orchestrator.heal_campaign_phase(
        campaign_id="camp_1",
        phase_id="phase_1",
        goal_id="goal_1",
        error_message="Timeout error",
    )
    assert res3.status == HealingStatus.BUDGET_EXHAUSTED
    assert res3.campaign_resumed is False


def test_orchestrator_checkpoint_persistence_and_restore():
    orchestrator1 = SelfHealingOrchestrator()
    orchestrator1.heal_campaign_phase(
        campaign_id="camp_1",
        phase_id="phase_1",
        goal_id="goal_1",
        error_message="Connection timed out",
    )

    # Export state
    ckpt_data = orchestrator1.to_dict()
    assert "history" in ckpt_data
    assert "attempts" in ckpt_data

    # Restore in new orchestrator
    orchestrator2 = SelfHealingOrchestrator()
    orchestrator2.restore_from_dict(ckpt_data)

    history = orchestrator2.get_healing_history("camp_1")
    assert len(history) == 1
    assert history[0].status == HealingStatus.RECOVERED
    assert orchestrator2.get_attempt_count("camp_1", "phase_1") == 1
