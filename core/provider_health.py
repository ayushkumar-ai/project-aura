import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState

logger = logging.getLogger("aura.provider_health")


class ProviderHealthStatus(str, Enum):
    """Aggregate health classification for a registered model provider."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    CIRCUIT_OPEN = "circuit_open"


@dataclass
class ProviderMetrics:
    """Telemetry metrics tracking availability, latency, and error rates of a provider endpoint."""

    provider_id: str
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    consecutive_failures: int = 0
    total_latency_seconds: float = 0.0
    moving_average_latency_seconds: float = 0.0
    last_success_timestamp: float | None = None
    last_failure_timestamp: float | None = None
    last_error: str | None = None
    circuit_state: CircuitState = CircuitState.CLOSED

    @property
    def failure_rate(self) -> float:
        """Calculate the overall failure rate for this provider."""
        if self.total_requests == 0:
            return 0.0
        return self.failed_requests / self.total_requests

    @property
    def health_status(self) -> ProviderHealthStatus:
        """Derive the current health classification based on failure rate and circuit state."""
        if self.circuit_state == CircuitState.OPEN:
            return ProviderHealthStatus.CIRCUIT_OPEN
        if self.consecutive_failures >= 3 or (self.total_requests >= 5 and self.failure_rate >= 0.5):
            return ProviderHealthStatus.UNHEALTHY
        if self.consecutive_failures > 0 or (self.total_requests >= 3 and self.failure_rate > 0.15):
            return ProviderHealthStatus.DEGRADED
        return ProviderHealthStatus.HEALTHY

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to a sanitized serializable dictionary without secrets."""
        return {
            "provider_id": self.provider_id,
            "health_status": self.health_status.value,
            "circuit_state": self.circuit_state.value,
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "consecutive_failures": self.consecutive_failures,
            "failure_rate": round(self.failure_rate, 4),
            "moving_average_latency_seconds": round(self.moving_average_latency_seconds, 4),
            "last_success_timestamp": self.last_success_timestamp,
            "last_failure_timestamp": self.last_failure_timestamp,
            "last_error": self.last_error,
        }


class ProviderHealthTracker:
    """Thread-safe provider health monitoring and circuit breaker registry."""

    def __init__(self, default_cb_config: CircuitBreakerConfig | None = None):
        self.default_cb_config = default_cb_config or CircuitBreakerConfig()
        self._lock = threading.RLock()
        self._metrics: dict[str, ProviderMetrics] = {}
        self._circuit_breakers: dict[str, CircuitBreaker] = {}

    def _normalize_id(self, provider_id: str) -> str:
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError("provider_id must be a non-empty string.")
        return provider_id.strip().lower()

    def get_or_create_circuit_breaker(
        self,
        provider_id: str,
        config: CircuitBreakerConfig | None = None,
    ) -> CircuitBreaker:
        """Get or initialize a CircuitBreaker for a specific provider ID."""
        norm_id = self._normalize_id(provider_id)
        with self._lock:
            if norm_id not in self._circuit_breakers:
                cb_cfg = config or self.default_cb_config
                self._circuit_breakers[norm_id] = CircuitBreaker(name=norm_id, config=cb_cfg)
            return self._circuit_breakers[norm_id]

    def _get_or_create_metrics(self, provider_id: str) -> ProviderMetrics:
        norm_id = self._normalize_id(provider_id)
        if norm_id not in self._metrics:
            self._metrics[norm_id] = ProviderMetrics(provider_id=norm_id)
        return self._metrics[norm_id]

    def record_call_success(
        self,
        provider_id: str,
        latency_seconds: float = 0.0,
        current_time: float | None = None,
    ) -> None:
        """Record a successful model call and update moving average latency."""
        norm_id = self._normalize_id(provider_id)
        now = current_time if current_time is not None else time.time()
        lat = max(0.0, float(latency_seconds))

        with self._lock:
            cb = self.get_or_create_circuit_breaker(norm_id)
            cb.record_success(now)

            m = self._get_or_create_metrics(norm_id)
            m.total_requests += 1
            m.successful_requests += 1
            m.consecutive_failures = 0
            m.total_latency_seconds += lat
            m.last_success_timestamp = now
            m.circuit_state = cb.state

            # Exponential moving average (alpha=0.2) or simple initial average
            if m.successful_requests == 1:
                m.moving_average_latency_seconds = lat
            else:
                alpha = 0.2
                m.moving_average_latency_seconds = (alpha * lat) + ((1.0 - alpha) * m.moving_average_latency_seconds)

    def record_call_failure(
        self,
        provider_id: str,
        error: Exception | str | None = None,
        latency_seconds: float = 0.0,
        current_time: float | None = None,
    ) -> None:
        """Record a failed model call, increment consecutive errors, and notify circuit breaker."""
        norm_id = self._normalize_id(provider_id)
        now = current_time if current_time is not None else time.time()
        lat = max(0.0, float(latency_seconds))
        err_msg = str(error) if error is not None else "Unknown model error"

        with self._lock:
            cb = self.get_or_create_circuit_breaker(norm_id)
            cb.record_failure(error=err_msg, current_time=now)

            m = self._get_or_create_metrics(norm_id)
            m.total_requests += 1
            m.failed_requests += 1
            m.consecutive_failures += 1
            m.total_latency_seconds += lat
            m.last_failure_timestamp = now
            m.last_error = err_msg
            m.circuit_state = cb.state

    def is_provider_healthy(self, provider_id: str) -> bool:
        """Check whether a provider is considered healthy and its circuit is not OPEN."""
        norm_id = self._normalize_id(provider_id)
        with self._lock:
            cb = self.get_or_create_circuit_breaker(norm_id)
            if not cb.can_execute():
                return False
            m = self._metrics.get(norm_id)
            if m is None:
                return True
            return m.health_status != ProviderHealthStatus.UNHEALTHY

    def get_metrics(self, provider_id: str) -> ProviderMetrics:
        """Retrieve metrics for a specific provider."""
        norm_id = self._normalize_id(provider_id)
        with self._lock:
            cb = self.get_or_create_circuit_breaker(norm_id)
            m = self._get_or_create_metrics(norm_id)
            m.circuit_state = cb.state
            return m

    def get_all_metrics(self) -> dict[str, ProviderMetrics]:
        """Retrieve all tracked provider metrics."""
        with self._lock:
            res = {}
            for pid, m in self._metrics.items():
                cb = self.get_or_create_circuit_breaker(pid)
                m.circuit_state = cb.state
                res[pid] = m
            return res

    def get_telemetry_snapshot(self) -> dict[str, Any]:
        """Generate a complete telemetry snapshot across all tracked providers."""
        with self._lock:
            return {
                "providers": {
                    pid: self.get_metrics(pid).to_dict()
                    for pid in list(self._metrics.keys())
                },
                "total_tracked_providers": len(self._metrics),
            }

    def get_all_telemetry(self) -> dict[str, Any]:
        """Alias for get_telemetry_snapshot."""
        return self.get_telemetry_snapshot()

    def reset(self, provider_id: str | None = None) -> None:
        """Reset metrics and circuit breakers."""
        with self._lock:
            if provider_id is not None:
                norm_id = self._normalize_id(provider_id)
                if norm_id in self._metrics:
                    del self._metrics[norm_id]
                if norm_id in self._circuit_breakers:
                    self._circuit_breakers[norm_id].reset()
            else:
                self._metrics.clear()
                for cb in self._circuit_breakers.values():
                    cb.reset()
