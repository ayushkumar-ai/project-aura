import pytest
from datetime import datetime
from core.reflection_types import (
    FailureIssueType,
    ReflectionAssessment,
    ReflectionRule,
    ReflectionRecord,
    ConsolidationSourceType,
    ResolutionStrategy,
    ContradictionRecord,
    ConsolidationRecord,
    DistillationResult,
    strip_forbidden_metadata_keys,
    sanitize_reflection_metadata,
)
from core.provenance import TaintedValue


def test_failure_issue_type_values():
    assert FailureIssueType.NONE.value == "none"
    assert FailureIssueType.TOOL_PAYLOAD_ERROR.value == "tool_payload_error"
    assert FailureIssueType.TOOL_EXECUTION_FAILURE.value == "tool_execution_failure"
    assert FailureIssueType.TIMEOUT.value == "timeout"
    assert FailureIssueType.POLICY_DENIAL.value == "policy_denial"
    assert FailureIssueType.DEPENDENCY_FAILURE.value == "dependency_failure"
    assert FailureIssueType.RESOURCE_LIMIT_EXCEEDED.value == "resource_limit_exceeded"
    assert FailureIssueType.UNKNOWN.value == "unknown"


def test_strip_forbidden_metadata_keys():
    meta = {
        "approved": True,
        "auto_approve": True,
        "permission": "admin",
        "authorized": True,
        "valid_key": "valid_value",
        "count": 42,
    }
    cleaned = strip_forbidden_metadata_keys(meta)
    assert "approved" not in cleaned
    assert "auto_approve" not in cleaned
    assert "permission" not in cleaned
    assert "authorized" not in cleaned
    assert cleaned["valid_key"] == "valid_value"
    assert cleaned["count"] == 42


def test_reflection_assessment_defaults():
    assessment = ReflectionAssessment(
        success=True,
        efficiency_score=0.9,
        steps_executed=3,
        steps_failed=0,
        retried_steps_count=0,
        replan_count=0,
        summary="Executed cleanly",
    )
    assert assessment.failure_issue_type == FailureIssueType.NONE
    assert assessment.root_cause is None
    assert assessment.was_retry_useful is False
    assert assessment.was_replan_useful is False
    assert assessment.lessons_learned == []
    assert assessment.rules_distilled == []
    assert assessment.metadata == {}

    d = assessment.to_dict()
    assert d["success"] is True
    assert d["efficiency_score"] == 0.9
    assert d["failure_issue_type"] == "none"

    restored = ReflectionAssessment.from_dict(d)
    assert restored.success == assessment.success
    assert restored.efficiency_score == assessment.efficiency_score
    assert restored.failure_issue_type == FailureIssueType.NONE


def test_reflection_rule_and_taint():
    untrusted_val = TaintedValue("Use sandbox curl instead of raw sockets", is_untrusted=True, source_urls=("https://docs.example.com",))
    rule = ReflectionRule(
        rule_id="rule-001",
        trigger_condition="Tool execution socket error",
        guidance=untrusted_val,
        confidence=0.85,
        source_trace_id="trace-123",
        tags=["network", "security"],
        metadata={"permission": "root", "safe_meta": 10},
    )
    assert rule.guidance.is_untrusted is True
    # Verify metadata stripping in post_init
    assert "permission" not in rule.metadata
    assert rule.metadata["safe_meta"] == 10

    d = rule.to_dict()
    assert d["rule_id"] == "rule-001"
    assert d["guidance"]["is_untrusted"] is True
    assert d["guidance"]["source_urls"] == ["https://docs.example.com"]

    restored = ReflectionRule.from_dict(d)
    assert restored.rule_id == "rule-001"
    assert isinstance(restored.guidance, TaintedValue)
    assert restored.guidance.raw_value == "Use sandbox curl instead of raw sockets"
    assert restored.guidance.is_untrusted is True


def test_reflection_record_serialization():
    rule = ReflectionRule(
        rule_id="rule-002",
        trigger_condition="Timeout in web scraper",
        guidance="Increase timeout to 30s",
        confidence=0.9,
    )
    assessment = ReflectionAssessment(
        success=False,
        efficiency_score=0.4,
        steps_executed=4,
        steps_failed=1,
        retried_steps_count=1,
        replan_count=0,
        failure_issue_type=FailureIssueType.TIMEOUT,
        root_cause="Scraper took 15s",
        was_retry_useful=False,
        lessons_learned=["Scraper target is slow"],
        rules_distilled=[rule],
        summary="Failed due to timeout",
    )
    record = ReflectionRecord(
        reflection_id="refl-001",
        target_id="task-456",
        target_type="plan",
        assessment=assessment,
        trace_id="trace-456",
        plan_id="plan-456",
        goal_id="goal-456",
        heuristics_distilled=["Slow targets need longer timeout"],
        metadata={"auto_approve": True, "agent": "executor"},
    )
    assert "auto_approve" not in record.metadata
    assert record.metadata["agent"] == "executor"

    d = record.to_dict()
    restored = ReflectionRecord.from_dict(d)
    assert restored.reflection_id == "refl-001"
    assert restored.target_id == "task-456"
    assert restored.assessment.failure_issue_type == FailureIssueType.TIMEOUT
    assert len(restored.assessment.rules_distilled) == 1
    assert restored.assessment.rules_distilled[0].rule_id == "rule-002"
    assert restored.heuristics_distilled == ["Slow targets need longer timeout"]


def test_contradiction_record():
    contradiction = ContradictionRecord(
        subject="server_port",
        existing_value="8080",
        existing_confidence=0.7,
        new_value="9090",
        new_confidence=0.95,
        resolution=ResolutionStrategy.REPLACE_NEWER_CONFIDENT,
        chosen_value="9090",
        rationale="New observation has 0.95 confidence vs 0.7",
    )
    d = contradiction.to_dict()
    assert d["resolution"] == "replace_newer_confident"
    assert d["chosen_value"] == "9090"

    restored = ContradictionRecord.from_dict(d)
    assert restored.resolution == ResolutionStrategy.REPLACE_NEWER_CONFIDENT
    assert restored.chosen_value == "9090"


def test_consolidation_record_and_distillation_result():
    consolidation = ConsolidationRecord(
        consolidation_id="cons-001",
        source_type=ConsolidationSourceType.EPISODIC_RUNS,
        episodes_analyzed=3,
        facts_created=2,
        facts_updated=1,
        contradictions_resolved=[
            ContradictionRecord(
                subject="status",
                existing_value="pending",
                existing_confidence=0.5,
                new_value="completed",
                new_confidence=0.9,
                resolution=ResolutionStrategy.REPLACE_NEWER_CONFIDENT,
                chosen_value="completed",
                rationale="Updated status",
            )
        ],
        heuristics_extracted=["Batch API calls to reduce round trips"],
        metadata={"approved": True, "env": "test"},
    )
    assert "approved" not in consolidation.metadata
    assert consolidation.metadata["env"] == "test"

    d = consolidation.to_dict()
    restored = ConsolidationRecord.from_dict(d)
    assert restored.consolidation_id == "cons-001"
    assert restored.source_type == ConsolidationSourceType.EPISODIC_RUNS
    assert len(restored.contradictions_resolved) == 1

    dist_result = DistillationResult(
        source_id="report-999",
        source_type=ConsolidationSourceType.RESEARCH_REPORT,
        facts_distilled=[],
        heuristics=["Fact checking improves accuracy"],
        rules=[],
        summary="Ingested 0 facts",
    )
    dist_d = dist_result.to_dict()
    rest_dist = DistillationResult.from_dict(dist_d)
    assert rest_dist.source_id == "report-999"
    assert rest_dist.source_type == ConsolidationSourceType.RESEARCH_REPORT
