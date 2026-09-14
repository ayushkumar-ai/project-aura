"""Thread-Safe Production Metrics Registry & Prometheus Exporter (M44).

Provides low-overhead, zero-external-dependency metrics primitives:
- Counter (monotonically increasing)
- Gauge (arbitrary instantaneous values)
- Histogram (cumulative bucket distributions, sums, counts)

Enforces low-cardinality label constraints and generates standard
Prometheus text exposition format (RFC 0014 / OpenMetrics compatible).
"""

from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

# Forbidden high-cardinality label keys to prevent memory leaks and TSDB cardinality explosion
FORBIDDEN_LABEL_KEYS = frozenset({
    "user_id",
    "request_id",
    "trace_id",
    "span_id",
    "prompt",
    "document_id",
    "memory_id",
    "error_message",
    "exception",
    "query",
    "raw_input",
    "auth_token",
    "api_key",
})

# Safe metric and label name pattern (Prometheus convention: [a-zA-Z_:][a-zA-Z0-9_:]*)
METRIC_NAME_REGEX = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")
LABEL_KEY_REGEX = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Default histogram upper bound buckets (seconds / linear scales)
DEFAULT_HISTOGRAM_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    float("inf"),
)

MAX_LABEL_SERIES_PER_METRIC = 500


class MetricType(str, Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"


def _sanitize_label_value(val: Any) -> str:
    """Sanitize label value to safe ASCII/UTF-8 string with escaped quotes/backslashes."""
    s = str(val).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    if len(s) > 128:
        return s[:125] + "..."
    return s


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    """Format sorted label pairs into standard Prometheus label string: {k1=\"v1\",k2=\"v2\"}."""
    if not labels:
        return ""
    items = [f'{k}="{_sanitize_label_value(v)}"' for k, v in labels]
    return "{" + ",".join(items) + "}"


class BaseMetric:
    """Base class for thread-safe metrics."""

    def __init__(
        self,
        name: str,
        description: str,
        metric_type: MetricType,
        allowed_label_keys: Sequence[str] | None = None,
    ) -> None:
        if not METRIC_NAME_REGEX.match(name):
            raise ValueError(f"Invalid metric name: '{name}'. Must match [a-zA-Z_:][a-zA-Z0-9_:]*")
        self.name = name
        self.description = description
        self.metric_type = metric_type
        self.allowed_label_keys = set(allowed_label_keys) if allowed_label_keys is not None else None
        self._lock = threading.RLock()

    def _normalize_labels(self, labels: dict[str, Any] | None) -> tuple[tuple[str, str], ...]:
        """Validate and normalize labels to sorted tuple of key-value pairs."""
        if not labels:
            return ()
        clean: list[tuple[str, str]] = []
        for k, v in labels.items():
            k_str = str(k).strip()
            if not LABEL_KEY_REGEX.match(k_str):
                continue
            if k_str.lower() in FORBIDDEN_LABEL_KEYS:
                # Disallow high-cardinality labels
                continue
            if self.allowed_label_keys is not None and k_str not in self.allowed_label_keys:
                continue
            clean.append((k_str, str(v).strip()))
        clean.sort(key=lambda x: x[0])
        return tuple(clean)


class Counter(BaseMetric):
    """Monotonically increasing cumulative metric counter."""

    def __init__(
        self,
        name: str,
        description: str,
        allowed_label_keys: Sequence[str] | None = None,
    ) -> None:
        super().__init__(name, description, MetricType.COUNTER, allowed_label_keys)
        self._values: dict[tuple[tuple[str, str], ...], float] = {}

    def inc(self, value: float = 1.0, labels: dict[str, Any] | None = None) -> None:
        """Increment counter by non-negative value."""
        if value < 0:
            raise ValueError(f"Counter increment value must be non-negative, got {value}")
        norm_labels = self._normalize_labels(labels)
        with self._lock:
            if len(self._values) >= MAX_LABEL_SERIES_PER_METRIC and norm_labels not in self._values:
                # Cap series growth
                return
            self._values[norm_labels] = self._values.get(norm_labels, 0.0) + float(value)

    def get(self, labels: dict[str, Any] | None = None) -> float:
        """Retrieve current counter value for labels."""
        norm_labels = self._normalize_labels(labels)
        with self._lock:
            return self._values.get(norm_labels, 0.0)

    def reset(self) -> None:
        """Reset all series in counter."""
        with self._lock:
            self._values.clear()

    def collect_prometheus(self) -> list[str]:
        """Render Prometheus exposition lines."""
        lines = [
            f"# HELP {self.name} {self.description}",
            f"# TYPE {self.name} counter",
        ]
        with self._lock:
            if not self._values:
                lines.append(f"{self.name} 0.0")
            else:
                for lbls, val in sorted(self._values.items(), key=lambda x: x[0]):
                    lbl_str = _format_labels(lbls)
                    lines.append(f"{self.name}{lbl_str} {val}")
        return lines


class Gauge(BaseMetric):
    """Instantaneous numerical value metric."""

    def __init__(
        self,
        name: str,
        description: str,
        allowed_label_keys: Sequence[str] | None = None,
    ) -> None:
        super().__init__(name, description, MetricType.GAUGE, allowed_label_keys)
        self._values: dict[tuple[tuple[str, str], ...], float] = {}

    def set(self, value: float, labels: dict[str, Any] | None = None) -> None:
        """Set gauge to specified float value."""
        norm_labels = self._normalize_labels(labels)
        with self._lock:
            if len(self._values) >= MAX_LABEL_SERIES_PER_METRIC and norm_labels not in self._values:
                return
            self._values[norm_labels] = float(value)

    def inc(self, value: float = 1.0, labels: dict[str, Any] | None = None) -> None:
        """Increment gauge value."""
        norm_labels = self._normalize_labels(labels)
        with self._lock:
            if len(self._values) >= MAX_LABEL_SERIES_PER_METRIC and norm_labels not in self._values:
                return
            self._values[norm_labels] = self._values.get(norm_labels, 0.0) + float(value)

    def dec(self, value: float = 1.0, labels: dict[str, Any] | None = None) -> None:
        """Decrement gauge value."""
        self.inc(-value, labels)

    def get(self, labels: dict[str, Any] | None = None) -> float:
        """Retrieve current gauge value."""
        norm_labels = self._normalize_labels(labels)
        with self._lock:
            return self._values.get(norm_labels, 0.0)

    def reset(self) -> None:
        with self._lock:
            self._values.clear()

    def collect_prometheus(self) -> list[str]:
        lines = [
            f"# HELP {self.name} {self.description}",
            f"# TYPE {self.name} gauge",
        ]
        with self._lock:
            if not self._values:
                lines.append(f"{self.name} 0.0")
            else:
                for lbls, val in sorted(self._values.items(), key=lambda x: x[0]):
                    lbl_str = _format_labels(lbls)
                    lines.append(f"{self.name}{lbl_str} {val}")
        return lines


class Histogram(BaseMetric):
    """Cumulative bucket distribution and summary metrics."""

    def __init__(
        self,
        name: str,
        description: str,
        buckets: Sequence[float] | None = None,
        allowed_label_keys: Sequence[str] | None = None,
    ) -> None:
        super().__init__(name, description, MetricType.HISTOGRAM, allowed_label_keys)
        raw_b = sorted(list(buckets or DEFAULT_HISTOGRAM_BUCKETS))
        if not any(math.isinf(b) for b in raw_b):
            raw_b.append(float("inf"))
        self.buckets = tuple(raw_b)
        # Series data: lbls -> {"sum": float, "count": int, "buckets": dict[float, int]}
        self._series: dict[tuple[tuple[str, str], ...], dict[str, Any]] = {}

    def observe(self, value: float, labels: dict[str, Any] | None = None) -> None:
        """Record observation value into histogram."""
        v = float(value)
        norm_labels = self._normalize_labels(labels)
        with self._lock:
            if norm_labels not in self._series:
                if len(self._series) >= MAX_LABEL_SERIES_PER_METRIC:
                    return
                self._series[norm_labels] = {
                    "sum": 0.0,
                    "count": 0,
                    "buckets": {b: 0 for b in self.buckets},
                }
            s = self._series[norm_labels]
            s["sum"] += v
            s["count"] += 1
            for b in self.buckets:
                if v <= b:
                    s["buckets"][b] += 1

    def get_stats(self, labels: dict[str, Any] | None = None) -> dict[str, Any]:
        """Retrieve sum, count, and bucket snapshot."""
        norm_labels = self._normalize_labels(labels)
        with self._lock:
            if norm_labels not in self._series:
                return {"sum": 0.0, "count": 0, "buckets": {b: 0 for b in self.buckets}}
            s = self._series[norm_labels]
            return {
                "sum": s["sum"],
                "count": s["count"],
                "buckets": dict(s["buckets"]),
            }

    def reset(self) -> None:
        with self._lock:
            self._series.clear()

    def collect_prometheus(self) -> list[str]:
        lines = [
            f"# HELP {self.name} {self.description}",
            f"# TYPE {self.name} histogram",
        ]
        with self._lock:
            if not self._series:
                # Emit empty histogram default series
                lines.append(f"{self.name}_count 0")
                lines.append(f"{self.name}_sum 0.0")
            else:
                for lbls, s in sorted(self._series.items(), key=lambda x: x[0]):
                    lbl_dict = dict(lbls)
                    # Bucket lines
                    for b in self.buckets:
                        b_str = "+Inf" if math.isinf(b) else str(b)
                        b_labels = dict(lbl_dict)
                        b_labels["le"] = b_str
                        sorted_b = tuple(sorted(b_labels.items(), key=lambda x: x[0]))
                        lbl_str = _format_labels(sorted_b)
                        lines.append(f"{self.name}_bucket{lbl_str} {s['buckets'][b]}")
                    
                    # Sum and Count lines
                    base_lbl_str = _format_labels(lbls)
                    lines.append(f"{self.name}_sum{base_lbl_str} {s['sum']}")
                    lines.append(f"{self.name}_count{base_lbl_str} {s['count']}")
        return lines


class MetricsRegistry:
    """Thread-safe central registry managing metric lifecycle, scrape exposition, and validation."""

    def __init__(self) -> None:
        self._metrics: dict[str, BaseMetric] = {}
        self._lock = threading.RLock()
        self._init_standard_metrics()

    def _init_standard_metrics(self) -> None:
        """Initialize all standard AURA production metrics."""
        # 1. HTTP Layer
        self.register_counter(
            "aura_http_requests_total",
            "Total HTTP requests handled by Project AURA API",
            allowed_labels=["method", "path", "status_code"],
        )
        self.register_histogram(
            "aura_http_request_duration_seconds",
            "HTTP request latency distribution",
            allowed_labels=["method", "path", "status_code"],
        )

        # 2. LLM Inference Layer
        self.register_counter(
            "aura_llm_requests_total",
            "Total LLM generation calls by provider, model, and status",
            allowed_labels=["provider", "model", "status"],
        )
        self.register_counter(
            "aura_llm_tokens_total",
            "Actual tokens consumed by provider and model (prompt/completion/total)",
            allowed_labels=["provider", "model", "type"],
        )
        self.register_histogram(
            "aura_llm_duration_seconds",
            "LLM inference latency distribution in seconds",
            allowed_labels=["provider", "model"],
        )

        # 3. Embedding Layer
        self.register_counter(
            "aura_embedding_requests_total",
            "Total embedding generation requests",
            allowed_labels=["provider", "model", "status"],
        )
        self.register_counter(
            "aura_embedding_vectors_total",
            "Total embedding vectors generated",
            allowed_labels=["provider", "model"],
        )
        self.register_histogram(
            "aura_embedding_duration_seconds",
            "Embedding generation latency distribution",
            allowed_labels=["provider", "model"],
        )

        # 4. RAG / Retrieval Layer
        self.register_counter(
            "aura_rag_retrievals_total",
            "Total RAG context assembly operations",
            allowed_labels=["status"],
        )
        self.register_histogram(
            "aura_rag_duration_seconds",
            "RAG phase duration in seconds",
            allowed_labels=["phase"],
        )

        # 5. Tool Execution Layer
        self.register_counter(
            "aura_tool_executions_total",
            "Total tool execution invocations",
            allowed_labels=["tool_name", "caller_role", "status"],
        )
        self.register_histogram(
            "aura_tool_duration_seconds",
            "Tool execution latency in seconds",
            allowed_labels=["tool_name"],
        )

        # 6. Database Layer
        self.register_gauge(
            "aura_db_pool_connections",
            "PostgreSQL connection pool status",
            allowed_labels=["state"],
        )
        self.register_counter(
            "aura_db_errors_total",
            "Database query, connection, and transaction errors",
            allowed_labels=["operation", "error_type"],
        )

        # 7. Security Audit Layer
        self.register_counter(
            "aura_security_events_total",
            "Security events (auth failures, denials, policy violations)",
            allowed_labels=["event_type", "outcome"],
        )

        # 8. Circuit Breaker & Quota Layer
        self.register_gauge(
            "aura_circuit_breaker_state",
            "Provider circuit breaker state (0=closed, 1=half-open, 2=open)",
            allowed_labels=["provider"],
        )
        self.register_counter(
            "aura_rate_limits_throttled_total",
            "Resource quota throttling and rate limit exhaustion events",
            allowed_labels=["resource"],
        )

    def register_counter(
        self,
        name: str,
        description: str,
        allowed_labels: Sequence[str] | None = None,
    ) -> Counter:
        with self._lock:
            if name in self._metrics:
                m = self._metrics[name]
                if isinstance(m, Counter):
                    return m
                raise TypeError(f"Metric '{name}' already registered as {m.metric_type}")
            c = Counter(name, description, allowed_labels)
            self._metrics[name] = c
            return c

    def register_gauge(
        self,
        name: str,
        description: str,
        allowed_labels: Sequence[str] | None = None,
    ) -> Gauge:
        with self._lock:
            if name in self._metrics:
                m = self._metrics[name]
                if isinstance(m, Gauge):
                    return m
                raise TypeError(f"Metric '{name}' already registered as {m.metric_type}")
            g = Gauge(name, description, allowed_labels)
            self._metrics[name] = g
            return g

    def register_histogram(
        self,
        name: str,
        description: str,
        buckets: Sequence[float] | None = None,
        allowed_labels: Sequence[str] | None = None,
    ) -> Histogram:
        with self._lock:
            if name in self._metrics:
                m = self._metrics[name]
                if isinstance(m, Histogram):
                    return m
                raise TypeError(f"Metric '{name}' already registered as {m.metric_type}")
            h = Histogram(name, description, buckets, allowed_labels)
            self._metrics[name] = h
            return h

    def get_metric(self, name: str) -> BaseMetric | None:
        with self._lock:
            return self._metrics.get(name)

    def get_counter(self, name: str) -> Counter:
        m = self.get_metric(name)
        if m is None or not isinstance(m, Counter):
            raise KeyError(f"Counter metric '{name}' not found")
        return m

    def get_gauge(self, name: str) -> Gauge:
        m = self.get_metric(name)
        if m is None or not isinstance(m, Gauge):
            raise KeyError(f"Gauge metric '{name}' not found")
        return m

    def get_histogram(self, name: str) -> Histogram:
        m = self.get_metric(name)
        if m is None or not isinstance(m, Histogram):
            raise KeyError(f"Histogram metric '{name}' not found")
        return m

    def reset_all(self) -> None:
        """Reset all metrics in registry (for testing)."""
        with self._lock:
            for m in self._metrics.values():
                m.reset()

    def to_prometheus_text(self) -> str:
        """Generate complete Prometheus exposition text format (RFC 0014)."""
        all_lines: list[str] = []
        with self._lock:
            for name, m in sorted(self._metrics.items(), key=lambda x: x[0]):
                all_lines.extend(m.collect_prometheus())
        return "\n".join(all_lines) + "\n"

    def to_dict(self) -> dict[str, Any]:
        """Generate sanitized operational summary dict."""
        out: dict[str, Any] = {}
        with self._lock:
            for name, m in self._metrics.items():
                if isinstance(m, (Counter, Gauge)):
                    out[name] = {
                        "type": m.metric_type.value,
                        "description": m.description,
                        "values": {
                            ",".join(f"{k}={v}" for k, v in lbls): val
                            for lbls, val in getattr(m, "_values", {}).items()
                        },
                    }
                elif isinstance(m, Histogram):
                    out[name] = {
                        "type": m.metric_type.value,
                        "description": m.description,
                        "series": {
                            ",".join(f"{k}={v}" for k, v in lbls): {
                                "sum": s["sum"],
                                "count": s["count"],
                            }
                            for lbls, s in getattr(m, "_series", {}).items()
                        },
                    }
        return out


# Global default metrics registry
_global_metrics_registry = MetricsRegistry()


def get_metrics_registry() -> MetricsRegistry:
    """Return the central singleton metrics registry."""
    return _global_metrics_registry
