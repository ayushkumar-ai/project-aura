"""Unit tests for Milestone 27 Causal Fault Analyzer."""

import pytest
import time
from core.causal_fault_analyzer import CausalFaultAnalyzer
from core.fault_types import ConfidenceLevel, FaultCategory
from core.trace_types import SpanEvent, SpanKind, SpanRecord, SpanStatus
from core.trace_exporter import CausalExecutionGraph


def test_analyzer_without_trace_graph_heuristic_classification():
    analyzer = CausalFaultAnalyzer()

    # Transient error
    rep1 = analyzer.analyze(
        campaign_id="c1",
        phase_id="p1",
        goal_id="g1",
        error_message="Connection timed out after 30s",
    )
    assert rep1.fault_category == FaultCategory.TRANSIENT_INFRASTRUCTURE
    assert rep1.confidence_level in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM)

    # Dynamic skill defect
    rep2 = analyzer.analyze(
        campaign_id="c1",
        phase_id="p1",
        goal_id="g1",
        error_message="Exception in execute_skill: dynamic_skill raised KeyError 'missing'",
    )
    assert rep2.fault_category == FaultCategory.DYNAMIC_SKILL_DEFECT
    assert rep2.confidence_level in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM)

    # Artifact schema mismatch
    rep3 = analyzer.analyze(
        campaign_id="c1",
        phase_id="p1",
        goal_id="g1",
        error_message="ArtifactPipelineRouter contract failure: schema_validation failed, required key 'id' missing",
    )
    assert rep3.fault_category == FaultCategory.ARTIFACT_SCHEMA_MISMATCH

    # Policy security block
    rep4 = analyzer.analyze(
        campaign_id="c1",
        phase_id="p1",
        goal_id="g1",
        error_message="Policy violation: action denied by policy, unauthorized resource access",
    )
    assert rep4.fault_category == FaultCategory.POLICY_SECURITY_BLOCK
    assert rep4.confidence_level == ConfidenceLevel.HIGH

    # Unknown error
    rep5 = analyzer.analyze(
        campaign_id="c1",
        phase_id="p1",
        goal_id="g1",
        error_message="XYZ unexpected condition foo bar",
    )
    assert rep5.fault_category == FaultCategory.UNKNOWN
    assert rep5.confidence_level == ConfidenceLevel.INSUFFICIENT


def test_analyzer_with_causal_graph_root_cause_traversal():
    t0 = time.time()
    # Construct a trace tree: Root -> Phase -> Goal -> DynamicToolExecution
    root_span = SpanRecord(
        trace_id="t_1",
        span_id="span_root",
        parent_span_id=None,
        name="campaign.execute",
        kind=SpanKind.INTERNAL,
        status=SpanStatus.ERROR,
        status_message="campaign error",
        start_time=t0,
        end_time=t0 + 2.0,
        duration_ms=2000.0,
    )
    phase_span = SpanRecord(
        trace_id="t_1",
        span_id="span_phase",
        parent_span_id="span_root",
        name="campaign.phase.p1",
        kind=SpanKind.INTERNAL,
        status=SpanStatus.ERROR,
        status_message="phase error",
        start_time=t0 + 0.1,
        end_time=t0 + 1.8,
        duration_ms=1700.0,
    )
    goal_span = SpanRecord(
        trace_id="t_1",
        span_id="span_goal",
        parent_span_id="span_phase",
        name="goal.execute.g1",
        kind=SpanKind.INTERNAL,
        status=SpanStatus.ERROR,
        status_message="goal error",
        start_time=t0 + 0.2,
        end_time=t0 + 1.5,
        duration_ms=1300.0,
    )
    tool_span = SpanRecord(
        trace_id="t_1",
        span_id="span_tool",
        parent_span_id="span_goal",
        name="dynamic_skill.compute_stats",
        kind=SpanKind.INTERNAL,
        status=SpanStatus.ERROR,
        status_message="ZeroDivisionError",
        start_time=t0 + 0.3,
        end_time=t0 + 0.9,
        duration_ms=600.0,
        events=(
            SpanEvent(
                name="exception",
                timestamp=t0 + 0.8,
                attributes={"message": "ZeroDivisionError: division by zero in dynamic_skill execution"},
            ),
        ),
    )

    graph = CausalExecutionGraph([root_span, phase_span, goal_span, tool_span])
    analyzer = CausalFaultAnalyzer()

    rep = analyzer.analyze(
        campaign_id="c1",
        phase_id="p1",
        goal_id="g1",
        error_message="Division by zero in tool execution",
        causal_graph=graph,
    )

    assert rep.fault_category == FaultCategory.DYNAMIC_SKILL_DEFECT
    assert rep.root_cause_span_id is not None
    assert len(rep.evidence_items) > 0
    assert rep.culpability_score >= 0.75
    assert rep.confidence_level == ConfidenceLevel.HIGH


def test_analyzer_context_signals_and_taint():
    analyzer = CausalFaultAnalyzer()
    rep = analyzer.analyze(
        campaign_id="c1",
        phase_id="p1",
        goal_id="g1",
        error_message="Evaluation score too low",
        context={"milestone_score": 0.45, "phase_error": "Milestone gate failed"},
    )
    assert rep.fault_category == FaultCategory.SEMANTIC_CRITERIA_UNMET
    assert any("Milestone evaluation score" in ev for ev in rep.evidence_items)
