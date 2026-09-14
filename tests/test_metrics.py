"""Tests for Thread-Safe Metrics Registry & Prometheus Exporter (M44)."""

import math
import threading
import pytest

from core.metrics import (
    Counter,
    Gauge,
    Histogram,
    MetricsRegistry,
    get_metrics_registry,
)


def test_counter_basic_increment():
    c = Counter("test_counter", "A test counter", allowed_label_keys=["method", "status"])
    c.inc(1.0, {"method": "GET", "status": "200"})
    c.inc(2.5, {"method": "GET", "status": "200"})
    c.inc(1.0, {"method": "POST", "status": "201"})

    assert c.get({"method": "GET", "status": "200"}) == 3.5
    assert c.get({"method": "POST", "status": "201"}) == 1.0
    assert c.get({"method": "DELETE", "status": "404"}) == 0.0


def test_counter_negative_increment_rejected():
    c = Counter("test_counter_neg", "Test non-negative constraint")
    with pytest.raises(ValueError):
        c.inc(-1.0)


def test_gauge_operations():
    g = Gauge("test_gauge", "A test gauge", allowed_label_keys=["state"])
    g.set(10.0, {"state": "active"})
    assert g.get({"state": "active"}) == 10.0

    g.inc(5.0, {"state": "active"})
    assert g.get({"state": "active"}) == 15.0

    g.dec(3.0, {"state": "active"})
    assert g.get({"state": "active"}) == 12.0


def test_histogram_observations_and_buckets():
    h = Histogram(
        "test_latency_seconds",
        "Test latency",
        buckets=[0.01, 0.05, 0.1, 0.5, 1.0],
        allowed_label_keys=["endpoint"],
    )
    h.observe(0.005, {"endpoint": "/v1/run"})
    h.observe(0.04, {"endpoint": "/v1/run"})
    h.observe(0.08, {"endpoint": "/v1/run"})
    h.observe(0.75, {"endpoint": "/v1/run"})

    stats = h.get_stats({"endpoint": "/v1/run"})
    assert stats["count"] == 4
    assert round(stats["sum"], 3) == round(0.005 + 0.04 + 0.08 + 0.75, 3)

    buckets = stats["buckets"]
    assert buckets[0.01] == 1  # 0.005
    assert buckets[0.05] == 2  # 0.005, 0.04
    assert buckets[0.1] == 3   # 0.005, 0.04, 0.08
    assert buckets[0.5] == 3
    assert buckets[1.0] == 4   # 0.005, 0.04, 0.08, 0.75
    assert buckets[float("inf")] == 4


def test_thread_safe_concurrent_increments():
    c = Counter("concurrent_counter", "Test thread safety")

    def worker():
        for _ in range(500):
            c.inc(1.0)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert c.get() == 5000.0


def test_low_cardinality_label_filtering():
    c = Counter("cardinality_test", "Test rejection of high-cardinality labels")
    # High-cardinality keys like user_id, request_id, prompt must be ignored
    c.inc(1.0, {"user_id": "usr_secret123", "request_id": "req_abc", "model": "gemini"})
    
    # Internal stored label tuple should NOT contain user_id or request_id
    stored_keys = [k for k, v in list(c._values.keys())[0]]
    assert "user_id" not in stored_keys
    assert "request_id" not in stored_keys


def test_prometheus_exposition_format():
    reg = MetricsRegistry()
    reg.reset_all()

    c = reg.get_counter("aura_http_requests_total")
    c.inc(5.0, {"method": "GET", "path": "/health", "status_code": "200"})

    g = reg.get_gauge("aura_circuit_breaker_state")
    g.set(0.0, {"provider": "gemini"})

    prom_text = reg.to_prometheus_text()
    assert "# HELP aura_http_requests_total" in prom_text
    assert "# TYPE aura_http_requests_total counter" in prom_text
    assert 'aura_http_requests_total{method="GET",path="/health",status_code="200"} 5.0' in prom_text
    assert '# TYPE aura_circuit_breaker_state gauge' in prom_text
    assert 'aura_circuit_breaker_state{provider="gemini"} 0.0' in prom_text
