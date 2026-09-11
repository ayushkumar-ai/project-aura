"""Trace Exporters & Causal Execution Graph Engine (M24).

Provides standard and specialized trace exporters (InMemory, JSONL, OpenTelemetryDict)
and the CausalExecutionGraph analyzer that reconstructs hierarchical DAG execution trees,
critical paths, and resource attribution.
"""

from __future__ import annotations

import abc
import json
import logging
import os
import threading
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Sequence

from core.trace_types import SpanKind, SpanRecord, SpanStatus

logger = logging.getLogger("aura.trace_exporter")


class TraceExporter(abc.ABC):
    """Abstract interface for receiving and persisting completed SpanRecords."""

    @abc.abstractmethod
    def export(self, spans: Sequence[SpanRecord]) -> bool:
        """Export a batch of span records. Returns True on success."""
        raise NotImplementedError

    def flush(self) -> None:
        """Flush any pending buffered spans."""
        pass

    def shutdown(self) -> None:
        """Cleanly close any resources or files."""
        pass


class InMemoryTraceExporter(TraceExporter):
    """Thread-safe in-memory ring buffer for trace storage, querying, and analysis."""

    def __init__(self, max_traces: int = 500, max_spans_per_trace: int = 1000):
        self.max_traces = max(10, int(max_traces))
        self.max_spans_per_trace = max(50, int(max_spans_per_trace))
        self._traces: dict[str, list[SpanRecord]] = defaultdict(list)
        self._trace_order: deque[str] = deque()
        self._lock = threading.RLock()

    def export(self, spans: Sequence[SpanRecord]) -> bool:
        """Record incoming spans into memory."""
        with self._lock:
            for s in spans:
                tid = s.trace_id
                if tid not in self._traces:
                    # Enforce max traces limit by popping oldest
                    while len(self._trace_order) >= self.max_traces:
                        oldest = self._trace_order.popleft()
                        self._traces.pop(oldest, None)
                    self._trace_order.append(tid)

                trace_spans = self._traces[tid]
                if len(trace_spans) < self.max_spans_per_trace:
                    trace_spans.append(s)
        return True

    def get_spans(self, trace_id: str) -> list[SpanRecord]:
        """Retrieve all spans for a specific trace_id."""
        with self._lock:
            return list(self._traces.get(str(trace_id).strip(), []))

    def get_all_spans(self) -> list[SpanRecord]:
        """Retrieve all currently buffered spans across all traces."""
        with self._lock:
            all_spans: list[SpanRecord] = []
            for spans in self._traces.values():
                all_spans.extend(spans)
            return all_spans

    def get_traces(self) -> dict[str, list[SpanRecord]]:
        """Retrieve a copy of all traces."""
        with self._lock:
            return {k: list(v) for k, v in self._traces.items()}

    def clear(self) -> None:
        """Clear all buffered traces."""
        with self._lock:
            self._traces.clear()
            self._trace_order.clear()


class JsonlTraceExporter(TraceExporter):
    """Appends completed span records to an atomic line-delimited JSON (JSONL) file."""

    def __init__(self, output_path: str | Path = ".aura_traces/traces.jsonl"):
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def export(self, spans: Sequence[SpanRecord]) -> bool:
        """Append span records to the JSONL file."""
        with self._lock:
            try:
                with open(self.output_path, "a", encoding="utf-8") as f:
                    for s in spans:
                        line = json.dumps(s.to_dict()) + "\n"
                        f.write(line)
                return True
            except Exception as e:
                logger.error("JsonlTraceExporter failed to write spans: %s", e)
                return False


class OpenTelemetryDictExporter(TraceExporter):
    """Formats spans into standard OpenTelemetry-compatible ResourceSpans dictionaries."""

    def __init__(self, service_name: str = "project-aura"):
        self.service_name = service_name
        self._exported_payloads: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def export(self, spans: Sequence[SpanRecord]) -> bool:
        """Convert spans into OTel dictionary structure."""
        formatted = self.format_spans(spans)
        with self._lock:
            self._exported_payloads.append(formatted)
        return True

    def format_spans(self, spans: Sequence[SpanRecord]) -> dict[str, Any]:
        """Convert a batch of SpanRecords to an OpenTelemetry ResourceSpans dict."""
        scope_spans = []
        for s in spans:
            # Map attributes to OTel key-value pairs
            otel_attrs = [{"key": str(k), "value": {"stringValue": str(v)}} for k, v in s.attributes.items()]
            otel_events = [
                {
                    "timeUnixNano": int(e.timestamp * 1e9),
                    "name": e.name,
                    "attributes": [{"key": str(k), "value": {"stringValue": str(v)}} for k, v in e.attributes.items()],
                }
                for e in s.events
            ]
            scope_spans.append({
                "traceId": s.trace_id,
                "spanId": s.span_id,
                "parentSpanId": s.parent_span_id or "",
                "name": s.name,
                "kind": s.kind.value,
                "startTimeUnixNano": int(s.start_time * 1e9),
                "endTimeUnixNano": int(s.end_time * 1e9),
                "attributes": otel_attrs,
                "events": otel_events,
                "status": {
                    "code": 1 if s.status == SpanStatus.OK else (2 if s.status == SpanStatus.ERROR else 0),
                    "message": s.status_message,
                },
            })

        return {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": self.service_name}},
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "aura.tracer", "version": "1.0.0"},
                            "spans": scope_spans,
                        }
                    ],
                }
            ]
        }

    def get_exported(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._exported_payloads)

    def clear(self) -> None:
        with self._lock:
            self._exported_payloads.clear()


class CausalExecutionGraph:
    """Reconstructs and analyzes the causal DAG of execution spans for a trace."""

    def __init__(self, spans: Sequence[SpanRecord]):
        self.spans: list[SpanRecord] = list(spans)
        self.span_map: dict[str, SpanRecord] = {s.span_id: s for s in self.spans}
        self.children_map: dict[str, list[str]] = defaultdict(list)
        self.parent_map: dict[str, str | None] = {}
        self.root_span_ids: list[str] = []

        self._build_dag()

    def _build_dag(self) -> None:
        """Construct parent-child adjacency indices."""
        for s in self.spans:
            sid = s.span_id
            pid = s.parent_span_id
            self.parent_map[sid] = pid
            if pid and pid in self.span_map:
                self.children_map[pid].append(sid)
            else:
                self.root_span_ids.append(sid)

    def get_root_spans(self) -> list[SpanRecord]:
        """Return all root spans (spans without parents in this graph)."""
        return [self.span_map[sid] for sid in self.root_span_ids if sid in self.span_map]

    def get_children(self, span_id: str) -> list[SpanRecord]:
        """Return immediate children of a span."""
        child_ids = self.children_map.get(str(span_id).strip(), [])
        return [self.span_map[cid] for cid in child_ids if cid in self.span_map]

    def get_parent(self, span_id: str) -> SpanRecord | None:
        """Return parent span if present in graph."""
        pid = self.parent_map.get(str(span_id).strip())
        return self.span_map.get(pid) if pid else None

    def get_critical_path(self) -> list[SpanRecord]:
        """Compute the critical path (longest duration path from root to leaf)."""
        if not self.spans:
            return []

        # Find leaf nodes
        leaf_ids = [s.span_id for s in self.spans if not self.children_map.get(s.span_id)]
        if not leaf_ids:
            leaf_ids = [s.span_id for s in self.spans]

        best_path: list[str] = []
        best_duration: float = -1.0

        def dfs(curr_id: str, path: list[str], dur: float):
            nonlocal best_path, best_duration
            span = self.span_map.get(curr_id)
            if span is None:
                return
            new_path = path + [curr_id]
            new_dur = dur + span.duration_ms

            children = self.children_map.get(curr_id, [])
            if not children:
                if new_dur > best_duration:
                    best_duration = new_dur
                    best_path = new_path
            else:
                for child_id in children:
                    dfs(child_id, new_path, new_dur)

        for root_id in self.root_span_ids:
            dfs(root_id, [], 0.0)

        return [self.span_map[sid] for sid in best_path if sid in self.span_map]

    @property
    def total_duration_ms(self) -> float:
        """Total wall latency bounded by start and end timestamps."""
        if not self.spans:
            return 0.0
        earliest_start = min(s.start_time for s in self.spans)
        latest_end = max(s.end_time for s in self.spans)
        return max(0.0, round((latest_end - earliest_start) * 1000.0, 3))

    @property
    def total_tokens(self) -> int:
        """Total tokens recorded across all spans."""
        return sum(int(s.resource_usage.get("tokens", 0)) for s in self.spans)

    @property
    def total_tool_calls(self) -> int:
        """Total tool calls recorded across all spans."""
        return sum(int(s.resource_usage.get("tool_calls", 0)) for s in self.spans)

    def get_spans_by_kind(self) -> dict[str, list[SpanRecord]]:
        """Group spans by their SpanKind."""
        by_kind: dict[str, list[SpanRecord]] = defaultdict(list)
        for s in self.spans:
            by_kind[s.kind.value].append(s)
        return dict(by_kind)

    def has_cycles(self) -> bool:
        """Detect if graph contains any cyclic parent-child links."""
        visited: set[str] = set()
        rec_stack: set[str] = set()

        def is_cyclic(curr: str) -> bool:
            visited.add(curr)
            rec_stack.add(curr)
            for child in self.children_map.get(curr, []):
                if child not in visited:
                    if is_cyclic(child):
                        return True
                elif child in rec_stack:
                    return True
            rec_stack.remove(curr)
            return False

        for sid in self.span_map:
            if sid not in visited:
                if is_cyclic(sid):
                    return True
        return False

    def to_dict(self) -> dict[str, Any]:
        """Serialize causal execution graph summary."""
        return {
            "span_count": len(self.spans),
            "root_spans": self.root_span_ids,
            "total_duration_ms": self.total_duration_ms,
            "total_tokens": self.total_tokens,
            "total_tool_calls": self.total_tool_calls,
            "critical_path": [s.span_id for s in self.get_critical_path()],
            "spans_by_kind": {k: [s.span_id for s in v] for k, v in self.get_spans_by_kind().items()},
            "has_cycles": self.has_cycles(),
        }
