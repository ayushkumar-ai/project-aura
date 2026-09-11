"""Unit tests for M24 methods exposed on the top-level AURA facade."""

import pytest
from app.aura import AURA
from core.agentic_runtime import AgenticRuntime
from core.artifact_types import ArtifactType
from core.history import ConversationHistory
from core.orchestrator import Orchestrator
from core.policy import Policy
from core.trace_types import SpanKind
from evaluation.models import (
    DimensionScore,
    EvaluationGrade,
    EvaluationReport,
    MetricDimension,
)
from providers.fake_model import FakeModelProvider


def test_aura_m24_facade_methods():
    model = FakeModelProvider()
    policy = Policy()
    runtime = AgenticRuntime(model=model, policy=policy)
    orchestrator = Orchestrator(model=model, policy=policy, history=ConversationHistory())
    aura = AURA(orchestrator=orchestrator, agentic_runtime=runtime)

    # 1. Tracing facade
    tracer = aura.get_tracer()
    with tracer.start_span("facade_span", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("version", "m24")
        span.record_resource_usage(tokens=120)

    trace = aura.get_trace(span.trace_id)
    assert len(trace) == 1
    assert trace[0].name == "facade_span"

    graph = aura.get_causal_graph(span.trace_id)
    assert len(graph.spans) == 1
    assert graph.total_tokens == 120

    # 2. Artifact facade
    art_v1 = aura.create_artifact(
        name="guide.md",
        content="# AURA Guide v1\nIntroductory documentation.",
        artifact_type=ArtifactType.DOCUMENT,
        creator_role_id="doc_writer",
    )
    assert art_v1.artifact_id
    assert art_v1.version == 1

    retrieved = aura.get_artifact(art_v1.artifact_id)
    assert retrieved is not None
    assert retrieved.name == "guide.md"

    content = aura.get_artifact_content(art_v1.artifact_id)
    assert "AURA Guide v1" in content

    arts = aura.list_artifacts()
    assert len(arts) >= 1

    lineage = aura.get_artifact_lineage(art_v1.artifact_id)
    assert lineage.target_artifact_id == art_v1.artifact_id

    # Create v2 and diff
    art_v2 = runtime.get_artifact_manager().create_next_version(
        artifact_id=art_v1.artifact_id,
        content="# AURA Guide v2\nUpdated documentation.",
    )
    diff = aura.diff_artifacts(art_v1.artifact_id, 1, 2)
    assert not diff["same_content"]
    assert len(diff["diff_lines"]) > 0

    # 3. Adaptive Optimizer facade
    report = EvaluationReport(
        report_id="rep-facade-1",
        target_id="target-f",
        target_type="goal",
        overall_score=0.91,
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

    events = aura.optimize_from_evaluation(report, context={"strategy_type": "react"})
    assert len(events) >= 1

    history = aura.get_optimization_history()
    assert len(history) >= 1
