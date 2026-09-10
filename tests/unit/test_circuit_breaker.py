import threading
import time
import pytest

from core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState


def test_circuit_breaker_closed_state():
    cfg = CircuitBreakerConfig(failure_threshold=3, recovery_timeout_seconds=5.0)
    cb = CircuitBreaker(name="test_cb", config=cfg)
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    cb.record_failure("error 1")
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    cb.record_success()
    # Failure count reset on success
    status = cb.get_status_dict()
    assert status["failure_count"] == 0


def test_circuit_breaker_trips_to_open():
    cfg = CircuitBreakerConfig(failure_threshold=2, recovery_timeout_seconds=10.0)
    cb = CircuitBreaker(name="test_cb", config=cfg)

    cb.record_failure("error 1")
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True

    cb.record_failure("error 2")
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute() is False


def test_circuit_breaker_recovery_to_half_open_and_closed():
    cfg = CircuitBreakerConfig(failure_threshold=2, recovery_timeout_seconds=1.0)
    cb = CircuitBreaker(name="test_cb", config=cfg)
    now = time.time()

    cb.record_failure("err1", current_time=now)
    cb.record_failure("err2", current_time=now)
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute(current_time=now) is False

    # Simulate elapsed cooldown
    later = now + 1.5
    assert cb.can_execute(current_time=later) is True
    assert cb.state == CircuitState.HALF_OPEN

    # In HALF_OPEN, second parallel request rejected (canary in-flight)
    assert cb.can_execute(current_time=later) is False

    # Canary succeeds -> transitions to CLOSED
    cb.record_success(current_time=later)
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute(current_time=later) is True


def test_circuit_breaker_half_open_canary_failure():
    cfg = CircuitBreakerConfig(failure_threshold=2, recovery_timeout_seconds=1.0)
    cb = CircuitBreaker(name="test_cb", config=cfg)
    now = time.time()

    cb.record_failure("err1", current_time=now)
    cb.record_failure("err2", current_time=now)
    assert cb.state == CircuitState.OPEN

    later = now + 1.5
    assert cb.can_execute(current_time=later) is True
    assert cb.state == CircuitState.HALF_OPEN

    # Canary fails -> re-trips to OPEN
    cb.record_failure("canary failed", current_time=later)
    assert cb.state == CircuitState.OPEN
    assert cb.can_execute(current_time=later) is False


def test_circuit_breaker_thread_safety():
    cfg = CircuitBreakerConfig(failure_threshold=50, recovery_timeout_seconds=10.0)
    cb = CircuitBreaker(name="test_cb", config=cfg)
    errors = []

    def worker(worker_id: int):
        try:
            for _ in range(10):
                if cb.can_execute():
                    cb.record_failure(f"err from {worker_id}")
                else:
                    cb.record_success()
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    assert cb.state in (CircuitState.CLOSED, CircuitState.OPEN)
