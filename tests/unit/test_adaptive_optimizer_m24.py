"""Unit tests for M24 Adaptive Policy Optimizer & Feedback Bridge."""

import pytest
from core.adaptive_optimizer import (
    AdaptivePolicyOptimizer,
    OptimizationEvent,
    OptimizationHistory,
    TuningTarget,
)
from core.feedback_bridge import FeedbackBridge
from core.heuristic_calibrator import HeuristicCalibrator
from evaluation.models import (
    DimensionScore,
    EvaluationGrade,
    EvaluationReport,
    MetricDimension,
    TrajectoryEvaluation,
)


def test_optimizer_heuristic_calibration_tuning():
    calibrator = HeuristicCalibrator(min_trials_for_promotion=2)
    optimizer = AdaptivePolicyOptimizer(calibrator=calibrator)

    # 1. Trajectory with drift and loops -> penalize
    traj_bad = TrajectoryEvaluation(
        trajectory_id="traj-err-1",
        is_valid=False,
        drift_detected=True,
        drift_score=0.85,
        loops_detected=True,
        loop_count=3,
        efficiency_score=0.30,
    )
    report_bad = EvaluationReport(
        report_id="rep-bad-1",
        target_id="target-1",
        target_type="goal",
        overall_score=0.45,
        grade=EvaluationGrade.F_FAILED,
        passed=False,
        dimension_scores={},
        trajectory_evaluation=traj_bad,
    )

    events = optimizer.optimize_from_evaluation(report_bad)
    assert len(events) >= 1
    ev = events[0]
    assert ev.target == TuningTarget.HEURISTIC_CALIBRATOR
    assert "trajectory_guard" in ev.parameter_name
    assert ev.delta is not None and ev.delta < 0

    # 2. Trajectory with high efficiency -> boost
    traj_good = TrajectoryEvaluation(
        trajectory_id="traj-good-1",
        is_valid=True,
        drift_detected=False,
        drift_score=0.0,
        loops_detected=False,
        efficiency_score=0.95,
    )
    report_good = EvaluationReport(
        report_id="rep-good-1",
        target_id="target-2",
        target_type="goal",
        overall_score=0.95,
        grade=EvaluationGrade.A_EXCELLENT,
        passed=True,
        dimension_scores={},
        trajectory_evaluation=traj_good,
    )

    events2 = optimizer.optimize_from_evaluation(report_good)
    assert len(events2) >= 1
    assert events2[0].delta is not None and events2[0].delta > 0


def test_optimizer_meta_policy_and_model_router_tuning():
    optimizer = AdaptivePolicyOptimizer(
        meta_policy="dummy_meta_policy",
        model_router="dummy_model_router",
        max_adjustment_delta=0.20,
    )

    dims = {
        MetricDimension.GOAL_CONVERGENCE: DimensionScore(
            dimension=MetricDimension.GOAL_CONVERGENCE,
            score=0.92,
            passed=True,
        ),
        MetricDimension.PROVIDER_RELIABILITY: DimensionScore(
            dimension=MetricDimension.PROVIDER_RELIABILITY,
            score=0.88,
            passed=True,
        ),
    }

    report = EvaluationReport(
        report_id="rep-multi-1",
        target_id="target-3",
        target_type="goal",
        overall_score=0.90,
        grade=EvaluationGrade.A_EXCELLENT,
        passed=True,
        dimension_scores=dims,
    )

    events = optimizer.optimize_from_evaluation(
        report,
        context={"strategy_type": "team_consensus", "provider_name": "anthropic_claude"},
    )

    assert len(events) == 2
    targets = [e.target for e in events]
    assert TuningTarget.META_POLICY in targets
    assert TuningTarget.MODEL_ROUTER in targets

    history = optimizer.history.list_events()
    assert len(history) == 2


def test_feedback_bridge_dispatch():
    calibrator = HeuristicCalibrator()
    optimizer = AdaptivePolicyOptimizer(calibrator=calibrator)
    bridge = FeedbackBridge(optimizer=optimizer)

    report = EvaluationReport(
        report_id="rep-bridge-1",
        target_id="target-4",
        target_type="goal",
        overall_score=0.85,
        grade=EvaluationGrade.B_GOOD,
        passed=True,
        dimension_scores={
            MetricDimension.GOAL_CONVERGENCE: DimensionScore(
                dimension=MetricDimension.GOAL_CONVERGENCE,
                score=0.85,
                passed=True,
            )
        },
    )

    events = bridge.process_evaluation(report)
    assert isinstance(events, list)
