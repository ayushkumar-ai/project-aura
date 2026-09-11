"""
Unit tests for Milestone 23 EvaluationEngine.
Tests multi-dimensional grading, goal convergence, team collaboration, safety audits, and hard safety veto.
"""

import pytest
from core.provenance import TaintedValue
from evaluation.engine import EvaluationEngine
from evaluation.models import EvaluationGrade, MetricDimension


def test_evaluation_engine_goal_convergence_scoring():
    engine = EvaluationEngine()

    class MockGoal:
        goal_id = "g_test_01"
        title = "Implement Database Indexing"
        description = "Add B-tree indexes for fast lookups"
        status = "completed"
        success = True
        success_criteria = ("Create users index", "Create orders index")

        class progress:
            percentage = 1.0
            satisfied_criteria = ("Create users index", "Create orders index")

    goal = MockGoal()
    report = engine.evaluate(goal, target_type="Goal")

    assert report.passed is True
    assert report.overall_score >= 0.90
    assert report.grade == EvaluationGrade.A_EXCELLENT
    assert MetricDimension.GOAL_CONVERGENCE in report.dimension_scores
    assert report.dimension_scores[MetricDimension.GOAL_CONVERGENCE].score == 1.0


def test_evaluation_engine_team_collaboration_scoring():
    engine = EvaluationEngine()

    class MockTeamResult:
        team_id = "eng_team_01"
        topology = "hierarchical"
        success = True
        consensus_score = 0.95
        messages_exchanged = 4
        subtask_results = {
            "architect": "Architecture design completed",
            "coder": "Implementation completed",
        }
        total_latency_seconds = 1.25

    team_res = MockTeamResult()
    report = engine.evaluate(team_res, target_type="TeamExecutionResult")

    assert report.passed is True
    assert report.overall_score >= 0.85
    assert MetricDimension.COLLABORATION_QUALITY in report.dimension_scores
    assert report.dimension_scores[MetricDimension.COLLABORATION_QUALITY].passed is True


def test_evaluation_engine_safety_compliance_and_taint_isolation():
    engine = EvaluationEngine()

    tainted_input = TaintedValue(raw_value="<script>malicious</script>", source_type="untrusted_web")

    class MockSecureTarget:
        task_id = "task_sec_01"
        success = True
        metadata = {
            "source_data": tainted_input,
            "session_id": "session_001",
        }

    target = MockSecureTarget()
    report = engine.evaluate(target, target_type="AutonomousTask")

    assert report.passed is True
    assert report.dimension_scores[MetricDimension.SAFETY_COMPLIANCE].passed is True
    assert report.dimension_scores[MetricDimension.PROVENANCE_INTEGRITY].passed is True


def test_evaluation_engine_hard_safety_veto_on_privilege_escalation():
    engine = EvaluationEngine()

    class MockMaliciousTarget:
        task_id = "task_exploit_01"
        success = True
        metadata = {
            "is_admin": True,  # Forbidden privilege injection
            "bypass_policy": True,
        }

    target = MockMaliciousTarget()
    report = engine.evaluate(target, target_type="AutonomousTask")

    # Hard safety veto should force failure and cap grade at D or F
    assert report.passed is False
    assert report.overall_score <= 0.45
    assert report.grade == EvaluationGrade.F_FAILED
    assert len(report.safety_findings) >= 2
    assert any("is_admin" in f for f in report.safety_findings)
