"""Integration tests for M24 capabilities in AgenticRuntime & Checkpoint Recovery."""

import tempfile
from pathlib import Path
import pytest
from core.agentic_runtime import AgenticRuntime
from core.artifact_types import ArtifactType
from core.runtime_checkpoint import RuntimeCheckpointManager
from core.trace_types import SpanKind
from evaluation.models import (
    DimensionScore,
    EvaluationGrade,
    EvaluationReport,
    MetricDimension,
)


def test_agentic_runtime_m24_core_subsystems():
    runtime = AgenticRuntime()
    assert runtime.get_tracer() is not None
    assert runtime.get_artifact_manager() is not None
    assert runtime.get_adaptive_optimizer() is not None
    assert runtime.get_feedback_bridge() is not None

    # 1. Tracing
    tracer = runtime.get_tracer()
    with tracer.start_span("runtime_test_span", kind=SpanKind.AGENT_STEP) as span:
        span.set_attribute("env", "test")
        span.record_resource_usage(tokens=75)

    spans = runtime.get_trace(span.trace_id)
    assert len(spans) == 1
    assert spans[0].name == "runtime_test_span"

    graph = runtime.get_causal_graph(span.trace_id)
    assert len(graph.spans) == 1
    assert graph.total_tokens == 75

    # 2. Artifacts
    art = runtime.create_artifact(
        name="summary.md",
        content="# Executive Summary\nAll tests operational.",
        artifact_type=ArtifactType.REPORT,
        session_id="session-alpha",
        creator_role_id="lead",
    )
    assert art.artifact_id
    assert art.name == "summary.md"

    retrieved = runtime.get_artifact(art.artifact_id)
    assert retrieved is not None
    assert retrieved.artifact_id == art.artifact_id

    content = runtime.get_artifact_content(art.artifact_id)
    assert "Executive Summary" in content

    lineage = runtime.get_artifact_lineage(art.artifact_id)
    assert lineage.target_artifact_id == art.artifact_id
    assert art.artifact_id in lineage.nodes

    # 3. Adaptive Policy Optimization
    report = EvaluationReport(
        report_id="rep-integ-1",
        target_id="goal-101",
        target_type="goal",
        overall_score=0.92,
        grade=EvaluationGrade.A_EXCELLENT,
        passed=True,
        dimension_scores={
            MetricDimension.GOAL_CONVERGENCE: DimensionScore(
                dimension=MetricDimension.GOAL_CONVERGENCE,
                score=0.95,
                passed=True,
            )
        },
    )

    events = runtime.optimize_from_evaluation(report, context={"strategy_type": "react"})
    assert len(events) >= 1
    history = runtime.get_optimization_history()
    assert len(history) >= 1


def test_agentic_runtime_m24_checkpoint_save_and_restore():
    with tempfile.TemporaryDirectory() as tmpdir:
        ckpt_dir = Path(tmpdir)
        runtime = AgenticRuntime()
        checkpoint_mgr = RuntimeCheckpointManager(
            checkpoint_dir=ckpt_dir,
            artifact_manager=runtime.get_artifact_manager(),
            adaptive_optimizer=runtime.get_adaptive_optimizer(),
            tracer=runtime.get_tracer(),
        )

        # Create an artifact
        art = runtime.create_artifact(
            name="dataset.json",
            content={"status": "active", "metrics": [1, 2, 3]},
            artifact_type=ArtifactType.DATASET,
        )

        # Trigger optimization event
        report = EvaluationReport(
            report_id="rep-ckpt-1",
            target_id="target-ckpt",
            target_type="goal",
            overall_score=0.88,
            grade=EvaluationGrade.B_GOOD,
            passed=True,
            dimension_scores={
                MetricDimension.GOAL_CONVERGENCE: DimensionScore(
                    dimension=MetricDimension.GOAL_CONVERGENCE,
                    score=0.88,
                    passed=True,
                )
            },
        )
        runtime.optimize_from_evaluation(report, context={"strategy_type": "hierarchical"})

        # Save checkpoint
        saved_meta = checkpoint_mgr.save_checkpoint(checkpoint_id="ckpt-m24-test")
        assert saved_meta.checkpoint_id == "ckpt-m24-test"

        # Create new runtime instance and restore
        new_runtime = AgenticRuntime()
        new_ckpt_mgr = RuntimeCheckpointManager(
            checkpoint_dir=ckpt_dir,
            artifact_manager=new_runtime.get_artifact_manager(),
            adaptive_optimizer=new_runtime.get_adaptive_optimizer(),
            tracer=new_runtime.get_tracer(),
        )

        restored_meta = new_ckpt_mgr.restore_latest_checkpoint()
        assert restored_meta is not None

        # Verify restored artifact
        restored_art = new_runtime.get_artifact(art.artifact_id)
        assert restored_art is not None
        assert restored_art.name == "dataset.json"

        # Verify restored optimization history
        history = new_runtime.get_optimization_history()
        assert len(history) >= 1
