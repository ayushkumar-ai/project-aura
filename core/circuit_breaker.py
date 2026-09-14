import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.config import settings

logger = logging.getLogger("aura.circuit_breaker")


class CircuitState(str, Enum):
    """Lifecycle states of a provider circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    """Configuration parameters governing circuit breaker tripping and recovery."""

    failure_threshold: int = 5
    recovery_timeout_seconds: float = 30.0
    half_open_success_threshold: int = 1

    def __post_init__(self):
        if not isinstance(self.failure_threshold, int) or self.failure_threshold < 1:
            raise ValueError("failure_threshold must be an integer >= 1.")
        self.recovery_timeout_seconds = float(self.recovery_timeout_seconds)
        if self.recovery_timeout_seconds <= 0:
            raise ValueError("recovery_timeout_seconds must be a positive float.")
        if not isinstance(self.half_open_success_threshold, int) or self.half_open_success_threshold < 1:
            raise ValueError("half_open_success_threshold must be an integer >= 1.")


class CircuitBreaker:
    """Thread-safe circuit breaker protecting downstream model endpoints from cascading failures."""

    def __init__(
        self,
        name: str = "default",
        config: CircuitBreakerConfig | None = None,
    ):
        self.name = str(name).strip() if name else "default"
        self.config = (
            config
            if config is not None
            else CircuitBreakerConfig(
                failure_threshold=int(getattr(settings, "aura_circuit_breaker_failure_threshold", 5)),
                recovery_timeout_seconds=float(
                    getattr(settings, "aura_circuit_breaker_recovery_timeout_seconds", 30.0)
                ),
            )
        )
        self._lock = threading.RLock()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._half_open_success_count = 0
        self._last_failure_time: float | None = None
        self._last_state_change_time: float = time.time()
        self._in_flight_canary = False

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._evaluate_state(time.time())
            return self._state

    def _sync_metric(self) -> None:
        """Update Prometheus gauge metric for circuit breaker state."""
        try:
            from core.metrics import get_metrics_registry
            metrics = get_metrics_registry()
            val = 0.0
            if self._state == CircuitState.HALF_OPEN:
                val = 1.0
            elif self._state == CircuitState.OPEN:
                val = 2.0
            metrics.get_gauge("aura_circuit_breaker_state").set(val, labels={"provider": self.name})
        except Exception:
            pass

    def _evaluate_state(self, now: float) -> None:
        """Evaluate whether an OPEN circuit should transition to HALF_OPEN after recovery timeout."""
        if self._state == CircuitState.OPEN:
            elapsed = now - self._last_state_change_time
            if elapsed >= self.config.recovery_timeout_seconds:
                logger.info(
                    "Circuit breaker '%s' recovery timeout (%.1fs) elapsed. Transitioning OPEN -> HALF_OPEN.",
                    self.name,
                    elapsed,
                )
                self._state = CircuitState.HALF_OPEN
                self._last_state_change_time = now
                self._half_open_success_count = 0
                self._in_flight_canary = False
                self._sync_metric()

    def can_execute(self, current_time: float | None = None) -> bool:
        """Check whether a request is permitted through the circuit breaker."""
        now = current_time if current_time is not None else time.time()
        with self._lock:
            self._evaluate_state(now)

            if self._state == CircuitState.CLOSED:
                return True
            elif self._state == CircuitState.OPEN:
                return False
            elif self._state == CircuitState.HALF_OPEN:
                # In HALF_OPEN, allow only a single canary probe
                if not self._in_flight_canary:
                    self._in_flight_canary = True
                    return True
                return False

            return False

    def record_success(self, current_time: float | None = None) -> None:
        """Record a successful provider call, resetting failures or closing half-open circuit."""
        now = current_time if current_time is not None else time.time()
        with self._lock:
            self._evaluate_state(now)

            if self._state == CircuitState.CLOSED:
                self._failure_count = 0
            elif self._state == CircuitState.HALF_OPEN:
                self._half_open_success_count += 1
                self._in_flight_canary = False
                if self._half_open_success_count >= self.config.half_open_success_threshold:
                    logger.info(
                        "Circuit breaker '%s' canary probe succeeded. Transitioning HALF_OPEN -> CLOSED.",
                        self.name,
                    )
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._half_open_success_count = 0
                    self._last_state_change_time = now
                    self._sync_metric()

    def record_failure(
        self,
        error: Exception | str | None = None,
        current_time: float | None = None,
    ) -> None:
        """Record a failed provider call, potentially tripping circuit to OPEN."""
        now = current_time if current_time is not None else time.time()
        with self._lock:
            self._evaluate_state(now)
            self._last_failure_time = now

            if self._state == CircuitState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self.config.failure_threshold:
                    logger.warning(
                        "Circuit breaker '%s' tripped! Failure count (%d >= %d). Transitioning CLOSED -> OPEN. Error: %s",
                        self.name,
                        self._failure_count,
                        self.config.failure_threshold,
                        error,
                    )
                    self._state = CircuitState.OPEN
                    self._last_state_change_time = now
                    self._sync_metric()
            elif self._state == CircuitState.HALF_OPEN:
                logger.warning(
                    "Circuit breaker '%s' canary probe failed. Re-tripping HALF_OPEN -> OPEN. Error: %s",
                    self.name,
                    error,
                )
                self._state = CircuitState.OPEN
                self._last_state_change_time = now
                self._in_flight_canary = False
                self._sync_metric()

    def reset(self) -> None:
        """Force reset the circuit breaker to CLOSED state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._half_open_success_count = 0
            self._in_flight_canary = False
            self._last_state_change_time = time.time()
            self._sync_metric()


    def get_status_dict(self) -> dict[str, Any]:
        """Return a serializable status snapshot of this circuit breaker."""
        with self._lock:
            self._evaluate_state(time.time())
            return {
                "name": self.name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "failure_threshold": self.config.failure_threshold,
                "recovery_timeout_seconds": self.config.recovery_timeout_seconds,
                "last_failure_time": self._last_failure_time,
                "last_state_change_time": self._last_state_change_time,
            }
