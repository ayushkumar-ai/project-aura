"""
Unit tests for Milestone 23 evaluation models, grades, dimension scores, and reports.
"""

import json
import pytest
from evaluation.models import (
    BenchmarkRunSummary,
    DimensionScore,
    EvaluationGrade,
    EvaluationReport,
    EvaluationResult,
    MetricDimension,
    TrajectoryEvaluation,
)


def test_evaluation_grade_from_score():
    assert EvaluationGrade.from_score(0.95) == EvaluationGrade.A_EXCELLENT
    assert EvaluationGrade.from_score(0.90) == EvaluationGrade.A_EXCELLENT
    assert EvaluationGrade.from_score(0.85) == EvaluationGrade.B_GOOD
    assert EvaluationGrade.from_score(0.80) == EvaluationGrade.B_GOOD
    assert EvaluationGrade.from_score(0.75) == EvaluationGrade.C_ACCEPTABLE
    assert EvaluationGrade.from_score(0.70) == EvaluationGrade.C_ACCEPTABLE
    assert EvaluationGrade.from_score(0.65) == EvaluationGrade.D_MARGINAL
    assert EvaluationGrade.from_score(0.60) == EvaluationGrade.D_MARGINAL
    assert EvaluationGrade.from_score(0.59) == EvaluationGrade.F_FAILED
    assert EvaluationGrade.from_score(0.0) == EvaluationGrade.F_FAILED


def test_dimension_score_creation_and_bounds():
    ds = DimensionScore(
        dimension=MetricDimension.GOAL_CONVERGENCE,
        score=1.5,  # Out of bounds -> should bound to 1.0
        passed=True,
        weight=1.5,
        metrics={"test_metric": 42},
        evidence=("Evidence 1", "Evidence 2"),
    )
    assert ds.dimension == MetricDimension.GOAL_CONVERGENCE
    assert ds.score == 1.0
    assert ds.passed is True
    assert ds.weight == 1.5
    assert len(ds.evidence) == 2

    d_dict = ds.to_dict()
    assert d_dict["dimension"] == "goal_convergence"
    assert d_dict["score"] == 1.0


def test_trajectory_evaluation_serialization():
    traj = TrajectoryEvaluation(
        trajectory_id="traj_123",
        is_valid=True,
        drift_detected=False,
        drift_score=0.05,
        loops_detected=False,
        loop_count=0,
        completion_consistent=True,
        completion_finding="Supported Completion",
        efficiency_score=0.92,
        step_count=5,
        tool_call_count=3,
        message_count=2,
        delegation_depth=2,
        evidence=("Step 1 passed", "Step 2 passed"),
    )
    assert traj.is_valid is True
    assert traj.step_count == 5

    traj_dict = traj.to_dict()
    assert traj_dict["trajectory_id"] == "traj_123"
    assert traj_dict["efficiency_score"] == 0.92


def test_evaluation_report_aggregation_and_json():
    ds_goal = DimensionScore(dimension=MetricDimension.GOAL_CONVERGENCE, score=0.95, passed=True)
    ds_safety = DimensionScore(dimension=MetricDimension.SAFETY_COMPLIANCE, score=1.0, passed=True)
    
    report = EvaluationReport(
        report_id="rep_test_001",
        target_id="goal_test_001",
        target_type="Goal",
        overall_score=0.975,
        passed=True,
        grade=EvaluationGrade.A_EXCELLENT,
        dimension_scores={
            MetricDimension.GOAL_CONVERGENCE: ds_goal,
            MetricDimension.SAFETY_COMPLIANCE: ds_safety,
        },
        findings=("Goal converged successfully",),
        safety_findings=("Zero privilege escalations",),
    )

    assert report.passed is True
    assert report.grade == EvaluationGrade.A_EXCELLENT
    
    json_str = report.to_json()
    data = json.loads(json_str)
    assert data["report_id"] == "rep_test_001"
    assert data["grade"] == "A"
    assert "goal_convergence" in data["dimension_scores"]


def test_benchmark_run_summary_serialization():
    ds = DimensionScore(dimension=MetricDimension.TASK_COMPLETION, score=1.0, passed=True)
    rep = EvaluationReport(
        report_id="rep_sc_1",
        target_id="sc_1",
        target_type="Scenario",
        overall_score=1.0,
        passed=True,
        grade=EvaluationGrade.A_EXCELLENT,
        dimension_scores={MetricDimension.TASK_COMPLETION: ds},
    )

    summary = BenchmarkRunSummary(
        benchmark_id="bench_001",
        suite_name="Core Benchmark Suite",
        total_scenarios=1,
        passed_scenarios=1,
        failed_scenarios=0,
        pass_rate=1.0,
        mean_score=1.0,
        mean_latency_seconds=0.15,
        scenario_reports={"sc_1": rep},
        grade=EvaluationGrade.A_EXCELLENT,
        started_at=100.0,
        completed_at=100.15,
    )

    assert summary.pass_rate == 1.0
    assert summary.grade == EvaluationGrade.A_EXCELLENT
    summary_dict = summary.to_dict()
    assert summary_dict["suite_name"] == "Core Benchmark Suite"
