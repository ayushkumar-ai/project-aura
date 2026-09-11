"""Unit tests for M24 Tracer, Exporters & CausalExecutionGraph."""

import tempfile
from pathlib import Path
import pytest
from core.trace_types import SpanKind, SpanStatus, TraceContext
from core.tracing import Tracer
from core.trace_exporter import (
    CausalExecutionGraph,
    InMemoryTraceExporter,
    JsonlTraceExporter,
    OpenTelemetryDictExporter,
)


def test_tracer_nested_spans_and_contextvars():
    tracer = Tracer()
    exporter = InMemoryTraceExporter()
    tracer.register_exporter(exporter)

    with tracer.start_span("root_goal", kind=SpanKind.AGENT_STEP) as root_span:
        root_span.set_attribute("goal_id", "g-101")
        root_span.record_resource_usage(tokens=100)

        with tracer.start_span("subtask_step", kind=SpanKind.TOOL_CALL) as child_span:
            child_span.set_attribute("tool", "calc")
            child_span.record_resource_usage(tokens=50, tool_calls=1)
            assert child_span.parent_span_id == root_span.span_id
            assert child_span.trace_id == root_span.trace_id

    spans = exporter.get_spans(root_span.trace_id)
    assert len(spans) == 2

    span_names = [s.name for s in spans]
    assert "root_goal" in span_names
    assert "subtask_step" in span_names


def test_tracer_exception_handling():
    tracer = Tracer()
    exporter = InMemoryTraceExporter()
    tracer.register_exporter(exporter)

    try:
        with tracer.start_span("failing_op", kind=SpanKind.INTERNAL) as span:
            raise ValueError("Something broke")
    except ValueError:
        pass

    spans = exporter.get_all_spans()
    assert len(spans) == 1
    assert spans[0].status == SpanStatus.ERROR
    assert "ValueError" in spans[0].status_message
    assert any(e.name == "exception" for e in spans[0].events)


def test_carrier_inject_and_extract():
    tracer = Tracer()
    ctx = TraceContext(trace_id="4bf92f3577b34da6a3ce929d0e0e4736", span_id="00f067aa0ba902b7", baggage={"user": "bob"})

    carrier = {}
    tracer.inject(carrier, context=ctx)
    assert "traceparent" in carrier

    extracted = tracer.extract(carrier)
    assert extracted is not None
    assert extracted.trace_id == ctx.trace_id
    assert extracted.span_id == ctx.span_id
    assert extracted.baggage.get("user") == "bob"


def test_causal_execution_graph():
    tracer = Tracer()
    exporter = InMemoryTraceExporter()
    tracer.register_exporter(exporter)

    with tracer.start_span("goal_root", kind=SpanKind.AGENT_STEP) as root:
        with tracer.start_span("research_task", kind=SpanKind.DELEGATION) as child1:
            child1.record_resource_usage(tokens=200)
        with tracer.start_span("synthesis_task", kind=SpanKind.DELEGATION) as child2:
            child2.record_resource_usage(tokens=150)

    spans = exporter.get_spans(root.trace_id)
    graph = CausalExecutionGraph(spans)

    assert len(graph.get_root_spans()) == 1
    assert graph.get_root_spans()[0].name == "goal_root"

    children = graph.get_children(root.span_id)
    assert len(children) == 2

    assert graph.total_tokens == 350
    assert not graph.has_cycles()

    crit_path = graph.get_critical_path()
    assert len(crit_path) >= 2
    assert crit_path[0].span_id == root.span_id


def test_opentelemetry_dict_exporter():
    tracer = Tracer(service_name="aura-agent-service")
    exporter = OpenTelemetryDictExporter(service_name="aura-agent-service")
    tracer.register_exporter(exporter)

    with tracer.start_span("inference_step", kind=SpanKind.MODEL_INFERENCE) as span:
        span.set_attribute("model", "claude-3-5-sonnet")

    exported = exporter.get_exported()
    assert len(exported) == 1
    assert "resourceSpans" in exported[0]
    scope_spans = exported[0]["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert len(scope_spans) == 1
    assert scope_spans[0]["name"] == "inference_step"
