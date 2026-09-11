"""Comprehensive end-to-end integration test for Milestone 24:
Unified Causal Tracing, Durable Versioned Artifacts & Closed-Loop Adaptive Self-Tuning.
"""

import tempfile
from pathlib import Path
import pytest
from app.aura import AURA
from core.agent_role import AgentRole
from core.agentic_runtime import AgenticRuntime
from core.artifact_types import ArtifactType
from core.history import ConversationHistory
from core.orchestrator import Orchestrator
from core.policy import Policy
from core.role_registry import RoleRegistry
from core.runtime_checkpoint import RuntimeCheckpointManager
from core.team_types import TeamDefinition, TeamMember, TeamTopology
from core.trace_types import SpanKind
from evaluation.engine import EvaluationEngine
from evaluation.models import EvaluationGrade, MetricDimension
from providers.fake_model import FakeModelProvider


def test_m24_end_to_end_trace_artifact_evaluation_feedback_lifecycle():
    # 1. Initialize Runtime with Full M24 Stack
    model = FakeModelProvider()
    policy = Policy()
    role_registry = RoleRegistry()

    lead_role = AgentRole(
        role_id="lead_architect",
        name="Lead Architect",
        description="Coordinates team deliverables and synthesizes final reports",
        system_prompt="You are the lead architect.",
    )
    researcher_role = AgentRole(
        role_id="security_researcher",
        name="Security Researcher",
        description="Researches security vulnerabilities and produces audit artifacts",
        system_prompt="You are the security researcher.",
    )
    role_registry.register_role(lead_role)
    role_registry.register_role(researcher_role)

    runtime = AgenticRuntime(
        model=model,
        policy=policy,
        role_registry=role_registry,
    )
    orchestrator = Orchestrator(model=model, policy=policy, history=ConversationHistory())
    aura = AURA(orchestrator=orchestrator, agentic_runtime=runtime)

    # 2. Start Distributed Trace & Execute Multi-Agent Workflow
    tracer = aura.get_tracer()
    with tracer.start_span("team_security_audit_goal", kind=SpanKind.TEAM_COORDINATION) as root_span:
        root_span.set_attribute("goal_name", "enterprise_security_audit")
        root_span.record_resource_usage(tokens=500, tool_calls=3)

        # 3. Produce Versioned Artifacts during Execution
        # Researcher produces raw finding artifact v1
        raw_art = aura.create_artifact(
            name="vulnerability_findings.json",
            content={"cve_id": "CVE-2026-9999", "severity": "HIGH", "status": "CONFIRMED"},
            artifact_type=ArtifactType.DATASET,
            creator_role_id="security_researcher",
            session_id="sec_sess_1",
        )
        assert raw_art.version == 1

        # Lead produces executive summary report citing raw findings as parent
        summary_art = aura.create_artifact(
            name="security_audit_report.md",
            content="# Security Audit Report\nConfirmed high severity vulnerability.",
            artifact_type=ArtifactType.REPORT,
            creator_role_id="lead_architect",
            session_id="sec_sess_1",
            parent_artifact_ids=[raw_art.artifact_id],
        )
        assert summary_art.version == 1
        assert raw_art.artifact_id in summary_art.lineage_parent_ids

    # 4. Verify Causal Tracing
    trace_spans = aura.get_trace(root_span.trace_id)
    assert len(trace_spans) == 1
    graph = aura.get_causal_graph(root_span.trace_id)
    assert graph.total_tokens == 500
    assert not graph.has_cycles()

    # 5. Verify Artifact Lineage
    lineage = aura.get_artifact_lineage(summary_art.artifact_id)
    assert lineage.depth == 2
    assert raw_art.artifact_id in lineage.nodes
    assert summary_art.artifact_id in lineage.nodes

    # 6. Evaluate Execution Output using M23 EvaluationEngine
    eval_engine = runtime.get_evaluation_engine()
    eval_report = eval_engine.evaluate(
        target={
            "output": "Completed comprehensive audit with 1 high severity vulnerability identified.",
            "status": "succeeded",
            "step_count": 4,
            "tool_call_count": 2,
        },
        target_id=root_span.trace_id,
        target_type="team_goal",
        expected_criteria=["high severity vulnerability identified", "succeeded"],
    )

    assert eval_report.passed is True
    assert eval_report.grade in (EvaluationGrade.A_EXCELLENT, EvaluationGrade.B_GOOD)

    # 7. Closed-Loop Adaptive Self-Tuning from Evaluation
    opt_events = aura.optimize_from_evaluation(
        report=eval_report,
        context={"strategy_type": "team_consensus", "provider_name": "fake_provider"},
    )
    assert len(opt_events) >= 1

    opt_history = aura.get_optimization_history()
    assert len(opt_history) >= 1
    latest_event = opt_history[-1]
    assert latest_event.evaluation_id == eval_report.report_id


def test_m24_checkpoint_full_recovery():
    with tempfile.TemporaryDirectory() as tmpdir:
        ckpt_dir = Path(tmpdir)
        runtime = AgenticRuntime()
        ckpt_mgr = RuntimeCheckpointManager(
            checkpoint_dir=ckpt_dir,
            artifact_manager=runtime.get_artifact_manager(),
            adaptive_optimizer=runtime.get_adaptive_optimizer(),
            tracer=runtime.get_tracer(),
        )

        # Create versioned artifact
        art1 = runtime.create_artifact(
            name="spec.md",
            content="# Spec v1",
            artifact_type=ArtifactType.DOCUMENT,
        )
        art2 = runtime.get_artifact_manager().create_next_version(
            artifact_id=art1.artifact_id,
            content="# Spec v2",
        )

        # Save checkpoint
        saved_meta = ckpt_mgr.save_checkpoint(checkpoint_id="ckpt_m24_rec")
        assert saved_meta.checkpoint_id == "ckpt_m24_rec"

        # Restore in fresh runtime
        restored_runtime = AgenticRuntime()
        restored_ckpt_mgr = RuntimeCheckpointManager(
            checkpoint_dir=ckpt_dir,
            artifact_manager=restored_runtime.get_artifact_manager(),
            adaptive_optimizer=restored_runtime.get_adaptive_optimizer(),
            tracer=restored_runtime.get_tracer(),
        )

        restored_meta = restored_ckpt_mgr.restore_latest_checkpoint()
        assert restored_meta is not None
        assert restored_meta.checkpoint_id == "ckpt_m24_rec"

        restored_art = restored_runtime.get_artifact(art1.artifact_id)
        assert restored_art is not None
        assert restored_art.version == 2
