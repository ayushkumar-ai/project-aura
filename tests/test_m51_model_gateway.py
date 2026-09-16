"""M51 Test Suite — Multi-Provider Model Gateway & Intelligent Provider Routing.

Validates:
- Transparent ModelInterface drop-in compliance
- Deterministic priority routing and fallback cascades
- Failure classification: Transient vs Fatal (Fail-Closed)
- Policy Deny / Security invariant (0 fallback calls)
- Circuit Breaker integration (CLOSED, OPEN, HALF_OPEN canary)
- Metric registration and telemetry exposition
- Tracing span structure and secret scrubbing
- Thread safety and concurrency
- Integration with Orchestrator and AgenticRuntime
"""

from __future__ import annotations

import threading
import time
from uuid import UUID, uuid4
from unittest.mock import MagicMock

import pytest

from app.config import Settings
from core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
from core.metrics import get_metrics_registry
from core.model_gateway import (
    FailureClassification,
    FailureClassifier,
    ModelGateway,
    ProviderCatalog,
    ProviderRegistration,
)
from core.models import AURARequest, AURAResponse
from interfaces.model import ModelInterface
from providers.fake_model import FakeModelProvider
from providers.gateway_factory import create_model_gateway


class MockModelProvider(ModelInterface):
    """Configurable mock provider for testing gateway behaviors."""

    def __init__(
        self,
        provider_name: str = "mock",
        model_name: str = "mock-model",
        responses: list[AURAResponse | Exception] | None = None,
        default_content: str = "mock response",
    ):
        self.provider_name = provider_name
        self.model_name = model_name
        self.responses = list(responses) if responses else []
        self.default_content = default_content
        self.call_count = 0
        self.calls: list[tuple[str, UUID]] = []

    def generate(self, prompt: str, request_id: UUID) -> AURAResponse:
        self.call_count += 1
        self.calls.append((prompt, request_id))
        if self.responses:
            res = self.responses.pop(0)
            if isinstance(res, Exception):
                raise res
            return res
        return AURAResponse(
            request_id=request_id,
            content=f"{self.provider_name}: {self.default_content}",
            metadata={"provider": self.provider_name, "model": self.model_name, "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
        )


# ==============================================================================
# 1. Basic & Single Provider Tests
# ==============================================================================

def test_gateway_single_provider_success():
    mock_p = MockModelProvider("fake", "fake-v1", default_content="hello world")
    reg = ProviderRegistration(provider_id="fake", provider=mock_p, priority=10)
    gateway = ModelGateway([reg])

    req_id = uuid4()
    resp = gateway.generate("test prompt", req_id)

    assert resp.request_id == req_id
    assert "hello world" in resp.content
    assert resp.metadata["gateway_provider"] == "fake"
    assert resp.metadata["gateway_attempt_count"] == 1
    assert resp.metadata["gateway_fallback_occurred"] is False
    assert mock_p.call_count == 1


def test_gateway_empty_prompt_validation():
    mock_p = MockModelProvider("fake")
    reg = ProviderRegistration(provider_id="fake", provider=mock_p)
    gateway = ModelGateway([reg])

    with pytest.raises(ValueError, match="Prompt cannot be empty"):
        gateway.generate("", uuid4())
    with pytest.raises(ValueError, match="Prompt cannot be empty"):
        gateway.generate("   ", uuid4())


def test_gateway_no_providers_raises():
    gateway = ModelGateway([])
    with pytest.raises(RuntimeError, match="ModelGateway has no registered providers"):
        gateway.generate("hello", uuid4())


# ==============================================================================
# 2. Priority Routing & Fallback Cascades
# ==============================================================================

def test_gateway_priority_routing():
    p1 = MockModelProvider("primary", default_content="primary response")
    p2 = MockModelProvider("secondary", default_content="secondary response")

    reg1 = ProviderRegistration("primary", p1, priority=10)
    reg2 = ProviderRegistration("secondary", p2, priority=20)
    gateway = ModelGateway([reg2, reg1])  # passed unordered

    resp = gateway.generate("hi", uuid4())
    assert "primary" in resp.content
    assert p1.call_count == 1
    assert p2.call_count == 0


def test_gateway_transient_fallback_cascade():
    p1 = MockModelProvider("primary", responses=[RuntimeError("503 Service Unavailable: Server Overloaded")])
    p2 = MockModelProvider("secondary", default_content="secondary ok")

    reg1 = ProviderRegistration("primary", p1, priority=10)
    reg2 = ProviderRegistration("secondary", p2, priority=20)
    gateway = ModelGateway([reg1, reg2])

    req_id = uuid4()
    resp = gateway.generate("hello", req_id)

    assert "secondary ok" in resp.content
    assert resp.metadata["gateway_provider"] == "secondary"
    assert resp.metadata["gateway_attempt_count"] == 2
    assert resp.metadata["gateway_fallback_occurred"] is True
    assert resp.metadata["gateway_attempted_chain"] == ["primary", "secondary"]
    assert p1.call_count == 1
    assert p2.call_count == 1


def test_gateway_rate_limit_fallback_429():
    p1 = MockModelProvider("gemini", responses=[RuntimeError("RateLimitError: 429 ResourceExhausted quota exceeded")])
    p2 = MockModelProvider("groq", default_content="groq fast response")

    reg1 = ProviderRegistration("gemini", p1, priority=10)
    reg2 = ProviderRegistration("groq", p2, priority=20)
    gateway = ModelGateway([reg1, reg2])

    resp = gateway.generate("test prompt", uuid4())
    assert "groq fast response" in resp.content
    assert resp.metadata["gateway_provider"] == "groq"
    assert p1.call_count == 1
    assert p2.call_count == 1


# ==============================================================================
# 3. Fail-Closed Security & Policy Invariants
# ==============================================================================

def test_gateway_fatal_policy_deny_fail_closed():
    """CRITICAL SECURITY INVARIANT: Policy Deny must immediately fail closed with 0 fallbacks."""
    p1 = MockModelProvider("gemini", responses=[RuntimeError("Security violation: Prompt injection detected - Access denied by policy")])
    p2 = MockModelProvider("groq", default_content="should never be called")

    reg1 = ProviderRegistration("gemini", p1, priority=10)
    reg2 = ProviderRegistration("groq", p2, priority=20)
    gateway = ModelGateway([reg1, reg2])

    with pytest.raises(RuntimeError, match="Prompt injection detected"):
        gateway.generate("jailbreak attempt", uuid4())

    assert p1.call_count == 1
    assert p2.call_count == 0, "Secondary provider MUST NOT be called on policy violation!"


def test_gateway_auth_failure_fail_closed():
    """Auth failures (401 / Invalid API Key) must fail closed without cascading."""
    p1 = MockModelProvider("gemini", responses=[RuntimeError("AuthenticationError: 401 Invalid API key provided")])
    p2 = MockModelProvider("groq", default_content="should not run")

    reg1 = ProviderRegistration("gemini", p1, priority=10)
    reg2 = ProviderRegistration("groq", p2, priority=20)
    gateway = ModelGateway([reg1, reg2])

    with pytest.raises(RuntimeError, match="401 Invalid API key"):
        gateway.generate("hello", uuid4())

    assert p1.call_count == 1
    assert p2.call_count == 0


def test_gateway_ssrf_policy_fail_closed():
    """SSRF violations fail closed immediately."""
    p1 = MockModelProvider("generic", responses=[ValueError("SSRF violation: Access to cloud metadata endpoint '169.254.169.254' is strictly forbidden.")])
    p2 = MockModelProvider("fallback", default_content="should not run")

    reg1 = ProviderRegistration("generic", p1, priority=10)
    reg2 = ProviderRegistration("fallback", p2, priority=20)
    gateway = ModelGateway([reg1, reg2])

    with pytest.raises(ValueError, match="SSRF violation"):
        gateway.generate("prompt", uuid4())

    assert p1.call_count == 1
    assert p2.call_count == 0


# ==============================================================================
# 4. Circuit Breaker Integration
# ==============================================================================

def test_gateway_circuit_breaker_open_skips_provider():
    cb1 = CircuitBreaker(name="primary", config=CircuitBreakerConfig(failure_threshold=1, recovery_timeout_seconds=60.0))
    cb1.record_failure("500 Server error")
    assert cb1.state == CircuitState.OPEN

    p1 = MockModelProvider("primary", default_content="primary")
    p2 = MockModelProvider("secondary", default_content="secondary ok")

    reg1 = ProviderRegistration("primary", p1, priority=10, circuit_breaker=cb1)
    reg2 = ProviderRegistration("secondary", p2, priority=20)
    gateway = ModelGateway([reg1, reg2])

    resp = gateway.generate("test", uuid4())
    assert "secondary ok" in resp.content
    assert p1.call_count == 0, "Primary with OPEN circuit breaker must be skipped without invocation!"
    assert p2.call_count == 1


def test_gateway_circuit_breaker_half_open_recovery():
    cb1 = CircuitBreaker(name="primary", config=CircuitBreakerConfig(failure_threshold=1, recovery_timeout_seconds=0.01))
    cb1.record_failure("500 Server error")
    assert cb1.state == CircuitState.OPEN

    time.sleep(0.02)  # wait for recovery timeout
    assert cb1.state == CircuitState.HALF_OPEN

    p1 = MockModelProvider("primary", default_content="primary recovered")
    reg1 = ProviderRegistration("primary", p1, priority=10, circuit_breaker=cb1)
    gateway = ModelGateway([reg1])

    resp = gateway.generate("test", uuid4())
    assert "primary recovered" in resp.content
    assert cb1.state == CircuitState.CLOSED
    assert p1.call_count == 1


# ==============================================================================
# 5. Exhaustion & Limits
# ==============================================================================

def test_gateway_exhaustion_raises_runtime_error():
    p1 = MockModelProvider("p1", responses=[RuntimeError("500 Internal Error")])
    p2 = MockModelProvider("p2", responses=[RuntimeError("503 Service Unavailable")])

    reg1 = ProviderRegistration("p1", p1, priority=10)
    reg2 = ProviderRegistration("p2", p2, priority=20)
    gateway = ModelGateway([reg1, reg2])

    with pytest.raises(RuntimeError, match="ModelGateway failed: All configured providers"):
        gateway.generate("prompt", uuid4())

    assert p1.call_count == 1
    assert p2.call_count == 1


def test_gateway_disabled_fallback_flag():
    p1 = MockModelProvider("p1", responses=[RuntimeError("500 Internal Error")])
    p2 = MockModelProvider("p2", default_content="should not be reached")

    reg1 = ProviderRegistration("p1", p1, priority=10)
    reg2 = ProviderRegistration("p2", p2, priority=20)
    gateway = ModelGateway([reg1, reg2], fallback_enabled=False)

    with pytest.raises(RuntimeError, match="500 Internal Error"):
        gateway.generate("prompt", uuid4())

    assert p1.call_count == 1
    assert p2.call_count == 0


def test_gateway_max_fallback_attempts_limit():
    p1 = MockModelProvider("p1", responses=[RuntimeError("500 Error")])
    p2 = MockModelProvider("p2", responses=[RuntimeError("500 Error")])
    p3 = MockModelProvider("p3", default_content="p3")

    reg1 = ProviderRegistration("p1", p1, priority=10)
    reg2 = ProviderRegistration("p2", p2, priority=20)
    reg3 = ProviderRegistration("p3", p3, priority=30)
    gateway = ModelGateway([reg1, reg2, reg3], max_fallback_attempts=2)

    with pytest.raises(RuntimeError, match="ModelGateway failed"):
        gateway.generate("prompt", uuid4())

    assert p1.call_count == 1
    assert p2.call_count == 1
    assert p3.call_count == 0


# ==============================================================================
# 6. Telemetry, Metrics & Secret Scrubbing
# ==============================================================================

def test_gateway_secret_scrubbing():
    secret_key = "sk-proj-supersecret1234567890123456"
    p1 = MockModelProvider("generic", responses=[RuntimeError(f"Connection failed: {secret_key} and password: secretpassword")])
    p2 = MockModelProvider("fallback", default_content="ok")

    reg1 = ProviderRegistration("generic", p1, priority=10)
    reg2 = ProviderRegistration("fallback", p2, priority=20)
    gateway = ModelGateway([reg1, reg2])

    resp = gateway.generate("test", uuid4())
    assert resp.metadata["gateway_fallback_occurred"] is True

    # Validate no secret leak in metadata
    meta_str = str(resp.metadata)
    assert secret_key not in meta_str
    assert "secretpassword" not in meta_str


def test_gateway_metrics_emission():
    metrics = get_metrics_registry()
    initial_fallbacks = metrics.get_counter("aura_gateway_fallbacks_total").get(labels={"from_provider": "m1", "to_provider": "m2", "reason": "timeout"})

    p1 = MockModelProvider("m1", responses=[RuntimeError("504 Gateway Timeout")])
    p2 = MockModelProvider("m2", default_content="m2 response")

    reg1 = ProviderRegistration("m1", p1, priority=10)
    reg2 = ProviderRegistration("m2", p2, priority=20)
    gateway = ModelGateway([reg1, reg2])

    gateway.generate("test", uuid4())

    new_fallbacks = metrics.get_counter("aura_gateway_fallbacks_total").get(labels={"from_provider": "m1", "to_provider": "m2", "reason": "timeout"})
    assert new_fallbacks == initial_fallbacks + 1.0


def test_gateway_health_status_snapshot():
    p1 = MockModelProvider("p1")
    p2 = MockModelProvider("p2")
    reg1 = ProviderRegistration("p1", p1, priority=10)
    reg2 = ProviderRegistration("p2", p2, priority=20, is_fallback=True)
    gateway = ModelGateway([reg1, reg2])

    health = gateway.get_health_status()
    assert health["status"] == "healthy"
    assert health["total_providers"] == 2
    assert "p1" in health["providers"]
    assert "p2" in health["providers"]
    assert health["providers"]["p1"]["is_fallback"] is False
    assert health["providers"]["p2"]["is_fallback"] is True


# ==============================================================================
# 7. Failure Classifier Matrix
# ==============================================================================

def test_failure_classifier_comprehensive():
    classifier = FailureClassifier()

    # Policy Denials (Fatal)
    assert classifier.classify("Access denied by policy") == FailureClassification.POLICY_DENY
    assert classifier.classify("Prompt injection detected") == FailureClassification.POLICY_DENY
    assert classifier.classify("SSRF violation: Loopback forbidden") == FailureClassification.POLICY_DENY
    assert classifier.is_fallback_eligible(FailureClassification.POLICY_DENY) is False

    # Auth failures (Fatal)
    assert classifier.classify("401 Unauthorized") == FailureClassification.AUTH_FAILURE
    assert classifier.classify("Invalid API key") == FailureClassification.AUTH_FAILURE
    assert classifier.is_fallback_eligible(FailureClassification.AUTH_FAILURE) is False

    # Bad Request (Fatal)
    assert classifier.classify("400 Bad Request") == FailureClassification.FATAL
    assert classifier.is_fallback_eligible(FailureClassification.FATAL) is False

    # Rate limits (Transient / Fallback eligible)
    assert classifier.classify("429 Too Many Requests") == FailureClassification.RATE_LIMITED
    assert classifier.classify("ResourceExhausted: quota exceeded") == FailureClassification.RATE_LIMITED
    assert classifier.is_fallback_eligible(FailureClassification.RATE_LIMITED) is True

    # Timeouts (Transient / Fallback eligible)
    assert classifier.classify(TimeoutError("Connection timed out")) == FailureClassification.TIMEOUT
    assert classifier.is_fallback_eligible(FailureClassification.TIMEOUT) is True

    # 5xx Errors (Transient / Fallback eligible)
    assert classifier.classify("500 InternalServerError") == FailureClassification.TRANSIENT
    assert classifier.classify("502 Bad Gateway") == FailureClassification.TRANSIENT
    assert classifier.classify("503 Service Unavailable") == FailureClassification.TRANSIENT
    assert classifier.is_fallback_eligible(FailureClassification.TRANSIENT) is True


# ==============================================================================
# 8. Concurrency, Catalog Ordering & Token Preservation
# ==============================================================================

def test_gateway_catalog_ordering():
    catalog = ProviderCatalog()
    catalog.register(ProviderRegistration("groq", MockModelProvider("groq"), priority=30))
    catalog.register(ProviderRegistration("gemini", MockModelProvider("gemini"), priority=10))
    catalog.register(ProviderRegistration("openai", MockModelProvider("openai"), priority=20))

    ordered = catalog.list_providers()
    assert [p.provider_id for p in ordered] == ["gemini", "openai", "groq"]
    assert catalog.get_primary().provider_id == "gemini"


def test_gateway_concurrent_requests():
    p1 = MockModelProvider("p1", default_content="thread safe")
    reg = ProviderRegistration("p1", p1, priority=10)
    gateway = ModelGateway([reg])

    errors = []

    def worker():
        try:
            for _ in range(10):
                resp = gateway.generate("concurrent", uuid4())
                assert "thread safe" in resp.content
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert p1.call_count == 100


def test_gateway_factory_from_settings():
    cfg = Settings(
        aura_model_provider="fake",
        aura_model_fallback_providers="fake",
        aura_model_fallback_enabled=True,
    )
    gw = create_model_gateway(cfg)
    assert isinstance(gw, ModelGateway)
    resp = gw.generate("hello factory", uuid4())
    assert resp.content is not None


def test_gateway_reset_all_circuits():
    cb = CircuitBreaker("p1", config=CircuitBreakerConfig(failure_threshold=1))
    cb.record_failure("err")
    assert cb.state == CircuitState.OPEN

    reg = ProviderRegistration("p1", MockModelProvider("p1"), circuit_breaker=cb)
    gateway = ModelGateway([reg])
    assert gateway.catalog.get("p1").circuit_breaker.state == CircuitState.OPEN

    gateway.reset_all_circuits()
    assert gateway.catalog.get("p1").circuit_breaker.state == CircuitState.CLOSED


def test_gateway_preserves_usage_tokens():
    mock_p = MockModelProvider("fake", "fake-v1", default_content="usage test")
    reg = ProviderRegistration(provider_id="fake", provider=mock_p, priority=10)
    gateway = ModelGateway([reg])

    resp = gateway.generate("usage prompt", uuid4())
    assert "usage" in resp.metadata
    assert resp.metadata["usage"]["total_tokens"] == 15


# ==============================================================================
# 9. Runtime & Orchestrator Integration Tests
# ==============================================================================

def test_orchestrator_integration_with_gateway():
    from app.main import create_orchestrator
    cfg = Settings(
        aura_model_provider="fake",
        aura_model_fallback_providers="fake",
        aura_model_gateway_enabled=True,
    )
    orchestrator = create_orchestrator(config=cfg)
    assert isinstance(orchestrator.model, ModelInterface)
    
    req = AURARequest(user_input="Hello from test")
    resp = orchestrator.run(req)
    assert resp.content is not None
    assert "gateway_provider" in resp.metadata


def test_agentic_runtime_integration_with_gateway():
    from app.main import create_aura
    cfg = Settings(
        aura_model_provider="fake",
        aura_model_gateway_enabled=True,
    )
    aura = create_aura(agentic=True, config=cfg)
    resp = aura.run("Execute basic test instruction")
    assert resp.content is not None
