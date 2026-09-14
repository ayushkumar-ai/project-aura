"""Tests for RAG and Tool Execution Telemetry (M44)."""

import pytest

from core.metrics import get_metrics_registry
from core.retrieval_pipeline import AdvancedRetrievalPipeline
from core.tool_ecosystem import ToolEcosystemRegistry
from core.tool_ecosystem_types import ToolExecutionRequest


def test_rag_telemetry_metrics():
    metrics = get_metrics_registry()
    metrics.reset_all()

    pipeline = AdvancedRetrievalPipeline()
    pipeline.add_knowledge_document(
        doc_id="doc_telemetry_1",
        title="Telemetry Guide",
        content="Production observability involves metrics, logs, and traces.",
    )

    bundle = pipeline.execute_rag("observability traces")

    assert bundle.retrieval_latency_ms >= 0.0
    assert len(bundle.candidates) >= 1

    # Verify RAG metrics recorded
    rag_count = metrics.get_counter("aura_rag_retrievals_total").get({"status": "success"})
    assert rag_count == 1.0


def test_tool_execution_telemetry_and_audit():
    metrics = get_metrics_registry()
    metrics.reset_all()

    registry = ToolEcosystemRegistry()
    req = ToolExecutionRequest(
        tool_name="calculator",
        parameters={"expression": "10 * 5 + 2"},
        caller_role="agent",
    )

    result = registry.execute_tool(req)
    assert result.success is True
    assert result.output["result"] == 52.0
    assert result.duration_seconds >= 0.0

    # Verify tool execution metrics
    tool_count = metrics.get_counter("aura_tool_executions_total").get(
        {"tool_name": "calculator", "caller_role": "agent", "status": "success"}
    )
    assert tool_count == 1.0

    # Verify audit record
    audit_records = registry.get_audit_log(tool_name="calculator")
    assert len(audit_records) >= 1
    assert audit_records[-1].success is True
