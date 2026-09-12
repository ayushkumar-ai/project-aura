"""Unit tests for Milestone 27 Fault Types & Data Contracts."""

import pytest
import time
from core.fault_types import (
    ConfidenceLevel,
    FaultCategory,
    FaultDiagnosticReport,
    HealingBudget,
    HealingStatus,
    RemediationAction,
    RemediationActionType,
    RemediationPlan,
    SelfHealingResult,
    _sanitize_fault_metadata,
)


def test_fault_category_properties():
    assert FaultCategory.TRANSIENT_INFRASTRUCTURE.is_retryable()
    assert not FaultCategory.POLICY_SECURITY_BLOCK.is_retryable()

    assert FaultCategory.POLICY_SECURITY_BLOCK.is_operator_escalation_required()
    assert FaultCategory.UNKNOWN.is_operator_escalation_required()
    assert not FaultCategory.TRANSIENT_INFRASTRUCTURE.is_operator_escalation_required()

    assert FaultCategory.DYNAMIC_SKILL_DEFECT.is_auto_remediable()
    assert FaultCategory.ARTIFACT_SCHEMA_MISMATCH.is_auto_remediable()
    assert not FaultCategory.POLICY_SECURITY_BLOCK.is_auto_remediable()


def test_confidence_level_from_score():
    assert ConfidenceLevel.from_score(0.85) == ConfidenceLevel.HIGH
    assert ConfidenceLevel.from_score(0.75) == ConfidenceLevel.HIGH
    assert ConfidenceLevel.from_score(0.60) == ConfidenceLevel.MEDIUM
    assert ConfidenceLevel.from_score(0.50) == ConfidenceLevel.MEDIUM
    assert ConfidenceLevel.from_score(0.35) == ConfidenceLevel.LOW
    assert ConfidenceLevel.from_score(0.15) == ConfidenceLevel.INSUFFICIENT


def test_healing_budget_validation_and_serialization():
    budget = HealingBudget(
        max_attempts=3,
        max_total_seconds=45.0,
        max_actions_per_attempt=5,
        max_retries=2,
    )
    assert budget.max_attempts == 3
    assert budget.max_total_seconds == 45.0
    assert budget.max_actions_per_attempt == 5
    assert budget.max_retries == 2

    d = budget.to_dict()
    restored = HealingBudget.from_dict(d)
    assert restored == budget

    with pytest.raises(ValueError):
        HealingBudget(max_attempts=0)
    with pytest.raises(ValueError):
        HealingBudget(max_total_seconds=-1.0)
    with pytest.raises(ValueError):
        HealingBudget(max_actions_per_attempt=0)
    with pytest.raises(ValueError):
        HealingBudget(max_retries=-1)


def test_sanitize_fault_metadata_strips_forbidden_keys():
    raw = {
        "valid_key": "safe_value",
        "is_admin": True,
        "sudo": "root",
        "bypass_policy": True,
        "approved": True,
        "count": 42,
    }
    cleaned = _sanitize_fault_metadata(raw)
    assert cleaned["valid_key"] == "safe_value"
    assert cleaned["count"] == 42
    assert "is_admin" not in cleaned
    assert "sudo" not in cleaned
    assert "bypass_policy" not in cleaned
    assert "approved" not in cleaned


def test_fault_diagnostic_report_immutability_and_serialization():
    report = FaultDiagnosticReport(
        report_id="fdr_001",
        campaign_id="camp_1",
        phase_id="phase_a",
        goal_id="goal_x",
        fault_category=FaultCategory.DYNAMIC_SKILL_DEFECT,
        confidence_level=ConfidenceLevel.HIGH,
        culpability_score=0.85,
        error_message="TypeError in skill execution",
        error_traceback="Traceback (most recent call last)...",
        root_cause_span_id="span_123",
        affected_artifact_ids=("art_1", "art_2"),
        failing_input="test input data",
        evidence_items=("Error in dynamic_skill span", "Exception raised"),
        timestamp=time.time(),
        metadata={"custom_info": "val", "is_admin": True},
    )

    assert report.is_autonomously_remediable() is True
    assert "is_admin" not in report.metadata
    assert report.metadata["custom_info"] == "val"

    # Test serialization round-trip
    d = report.to_dict()
    restored = FaultDiagnosticReport.from_dict(d)
    assert restored.report_id == report.report_id
    assert restored.campaign_id == report.campaign_id
    assert restored.fault_category == FaultCategory.DYNAMIC_SKILL_DEFECT
    assert restored.confidence_level == ConfidenceLevel.HIGH
    assert restored.culpability_score == 0.85
    assert restored.affected_artifact_ids == ("art_1", "art_2")

    # Frozen check
    with pytest.raises(Exception):
        report.error_message = "New error"


def test_remediation_action_and_plan_serialization():
    action1 = RemediationAction(
        action_id="ra_1",
        action_type=RemediationActionType.PATCH_DYNAMIC_SKILL,
        target_id="my_skill",
        tier=2,
        requires_approval=False,
        parameters={"skill_name": "my_skill"},
        rationale="Deprecate buggy skill",
        estimated_duration_seconds=5.0,
    )
    action2 = RemediationAction(
        action_id="ra_2",
        action_type=RemediationActionType.RETRY_PHASE,
        target_id="phase_a",
        tier=1,
        requires_approval=False,
        parameters={"retry_delay_seconds": "1.0"},
        rationale="Retry after patch",
        estimated_duration_seconds=2.0,
    )

    plan = RemediationPlan(
        plan_id="rp_001",
        fault_report_id="fdr_001",
        campaign_id="camp_1",
        phase_id="phase_a",
        actions=(action1, action2),
        budget=HealingBudget(max_attempts=2),
        created_at=time.time(),
        rationale="Patch and retry",
        is_operator_escalation=False,
    )

    assert len(plan.actions) == 2
    assert plan.actions[0].action_type == RemediationActionType.PATCH_DYNAMIC_SKILL

    d = plan.to_dict()
    restored = RemediationPlan.from_dict(d)
    assert restored.plan_id == plan.plan_id
    assert len(restored.actions) == 2
    assert restored.actions[0].action_type == RemediationActionType.PATCH_DYNAMIC_SKILL
    assert restored.actions[1].action_type == RemediationActionType.RETRY_PHASE


def test_self_healing_result_serialization():
    result = SelfHealingResult(
        result_id="shr_001",
        plan_id="rp_001",
        campaign_id="camp_1",
        phase_id="phase_a",
        status=HealingStatus.RECOVERED,
        fault_category=FaultCategory.TRANSIENT_INFRASTRUCTURE,
        actions_attempted=1,
        actions_succeeded=1,
        campaign_resumed=True,
        duration_seconds=1.5,
        error=None,
        attempt_number=1,
        timestamp=time.time(),
        metadata={"detail": "recovered cleanly"},
    )

    assert result.status == HealingStatus.RECOVERED
    assert result.campaign_resumed is True

    d = result.to_dict()
    restored = SelfHealingResult.from_dict(d)
    assert restored.result_id == result.result_id
    assert restored.status == HealingStatus.RECOVERED
    assert restored.campaign_resumed is True
    assert restored.actions_succeeded == 1
