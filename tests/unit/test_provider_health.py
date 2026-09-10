import time
import pytest

from core.circuit_breaker import CircuitBreakerConfig, CircuitState
from core.provider_health import (
    ProviderHealthStatus,
    ProviderHealthTracker,
    ProviderMetrics,
)


def test_provider_health_metrics_success_and_latency():
    tracker = ProviderHealthTracker()
    now = time.time()

    tracker.record_call_success("openai_primary", latency_seconds=0.5, current_time=now)
    m = tracker.get_metrics("openai_primary")

    assert m.total_requests == 1
    assert m.successful_requests == 1
    assert m.failed_requests == 0
    assert m.consecutive_failures == 0
    assert m.moving_average_latency_seconds == 0.5
    assert m.health_status == ProviderHealthStatus.HEALTHY
    assert tracker.is_provider_healthy("openai_primary") is True

    # Second call
    tracker.record_call_success("openai_primary", latency_seconds=1.0, current_time=now + 1)
    m2 = tracker.get_metrics("openai_primary")
    assert m2.total_requests == 2
    assert m2.successful_requests == 2
    # Moving average (0.2 * 1.0 + 0.8 * 0.5 = 0.6)
    assert abs(m2.moving_average_latency_seconds - 0.6) < 1e-4


def test_provider_health_metrics_failure_and_status():
    cb_cfg = CircuitBreakerConfig(failure_threshold=3, recovery_timeout_seconds=10.0)
    tracker = ProviderHealthTracker(default_cb_config=cb_cfg)

    tracker.record_call_failure("fallback_provider", error="HTTP 500 Server Error", latency_seconds=0.2)
    m = tracker.get_metrics("fallback_provider")
    assert m.total_requests == 1
    assert m.failed_requests == 1
    assert m.consecutive_failures == 1
    assert m.last_error == "HTTP 500 Server Error"
    assert m.health_status == ProviderHealthStatus.DEGRADED

    tracker.record_call_failure("fallback_provider", error="HTTP 503 Service Unavailable")
    tracker.record_call_failure("fallback_provider", error="Connection reset")
    m3 = tracker.get_metrics("fallback_provider")
    assert m3.consecutive_failures == 3
    assert m3.circuit_state == CircuitState.OPEN
    assert m3.health_status == ProviderHealthStatus.CIRCUIT_OPEN
    assert tracker.is_provider_healthy("fallback_provider") is False


def test_provider_health_telemetry_snapshot_and_secret_isolation():
    tracker = ProviderHealthTracker()
    tracker.record_call_success("provider_a", latency_seconds=0.1)
    tracker.record_call_failure("provider_b", error="TimeoutError")

    snapshot = tracker.get_telemetry_snapshot()
    assert snapshot["total_tracked_providers"] == 2
    assert "provider_a" in snapshot["providers"]
    assert "provider_b" in snapshot["providers"]

    # Verify no credentials leaked
    serialized = str(snapshot)
    assert "api_key" not in serialized.lower()
    assert "token" not in serialized.lower()
    assert "password" not in serialized.lower()
    assert "secret" not in serialized.lower()


def test_provider_health_tracker_reset():
    tracker = ProviderHealthTracker()
    tracker.record_call_failure("p1", error="err")
    assert tracker.get_metrics("p1").failed_requests == 1

    tracker.reset("p1")
    assert tracker.get_metrics("p1").failed_requests == 0
