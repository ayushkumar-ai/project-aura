"""Unit tests for Milestone 27 Remediation Planner."""

import pytest
import time
from core.fault_types import (
    ConfidenceLevel,
    FaultCategory,
    FaultDiagnosticReport,
    HealingBudget,
    RemediationActionType,
)
from core.remediation_planner import RemediationPlanner


def _make_report(
    category: FaultCategory,
    confidence: ConfidenceLevel = ConfidenceLevel.HIGH,
    error_msg: str = "Test error",
) -> FaultDiagnosticReport:
    return FaultDiagnosticReport(
        report_id="fdr_test",
        campaign_id="c1",
        phase_id="p1",
        goal_id="g1",
        fault_category=category,
        confidence_level=confidence,
        culpability_score=0.85 if confidence == ConfidenceLevel.HIGH else 0.4,
        error_message=error_msg,
        error_traceback="",
        root_cause_span_id=None,
        affected_artifact_ids=(),
        failing_input="",
        evidence_items=(),
        timestamp=time.time(),
    )


def test_planner_policy_block_always_escalates():
    planner = RemediationPlanner()
    rep = _make_report(FaultCategory.POLICY_SECURITY_BLOCK)
    plan = planner.plan(rep)

    assert plan.is_operator_escalation is True
    assert len(plan.actions) == 1
    assert plan.actions[0].action_type == RemediationActionType.REQUEST_OPERATOR_CLARIFICATION
    assert plan.actions[0].tier == 4
    assert plan.actions[0].requires_approval is True


def test_planner_insufficient_confidence_always_escalates():
    planner = RemediationPlanner()
    rep = _make_report(FaultCategory.DYNAMIC_SKILL_DEFECT, confidence=ConfidenceLevel.INSUFFICIENT)
    plan = planner.plan(rep)

    assert plan.is_operator_escalation is True
    assert plan.actions[0].action_type == RemediationActionType.REQUEST_OPERATOR_CLARIFICATION


def test_planner_transient_infrastructure_retry():
    planner = RemediationPlanner()
    rep = _make_report(FaultCategory.TRANSIENT_INFRASTRUCTURE)
    plan = planner.plan(rep)

    assert plan.is_operator_escalation is False
    assert len(plan.actions) == 1
    assert plan.actions[0].action_type == RemediationActionType.RETRY_PHASE
    assert plan.actions[0].tier == 1
    assert plan.actions[0].requires_approval is False


def test_planner_dynamic_skill_defect_patch_and_retry():
    planner = RemediationPlanner()
    rep = _make_report(FaultCategory.DYNAMIC_SKILL_DEFECT)
    plan = planner.plan(rep, context={"skill_name": "calc_tool"})

    assert plan.is_operator_escalation is False
    assert len(plan.actions) == 2
    assert plan.actions[0].action_type == RemediationActionType.PATCH_DYNAMIC_SKILL
    assert plan.actions[0].target_id == "calc_tool"
    assert plan.actions[0].tier == 2
    assert plan.actions[1].action_type == RemediationActionType.RETRY_PHASE
    assert plan.actions[1].tier == 1


def test_planner_artifact_schema_mismatch_inject_transformer():
    planner = RemediationPlanner()
    rep = _make_report(FaultCategory.ARTIFACT_SCHEMA_MISMATCH)
    plan = planner.plan(rep, context={"artifact_id": "art_dataset_1"})

    assert plan.is_operator_escalation is False
    assert len(plan.actions) == 2
    assert plan.actions[0].action_type == RemediationActionType.INJECT_DATAFLOW_TRANSFORMER
    assert plan.actions[1].action_type == RemediationActionType.RETRY_PHASE


def test_planner_budget_constraints():
    planner = RemediationPlanner()
    rep = _make_report(FaultCategory.TRANSIENT_INFRASTRUCTURE)
    budget = HealingBudget(max_retries=0)  # Zero retries allowed
    plan = planner.plan(rep, budget=budget)

    # When retry budget is 0, planner must escalate
    assert plan.actions[0].action_type == RemediationActionType.REQUEST_OPERATOR_CLARIFICATION


def test_planner_type_error_on_invalid_input():
    planner = RemediationPlanner()
    with pytest.raises(TypeError):
        planner.plan("not a fault report")  # type: ignore
