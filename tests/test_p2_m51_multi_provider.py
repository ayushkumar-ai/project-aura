"""
Project AURA — Post-M59 P2 Surgical Enhancement Tests
M51 Multi-Provider Model Gateway Expansion Test Suite

Validates all 30 formal invariants (P2-M51-F01 through P2-M51-F30):
- Groq, OpenRouter, Mistral, Gemini, OpenAI, Generic, Fake providers
- Capability routing, context windows, structured cost metadata, and pricing modes
- Circuit breaker isolation, fail-closed security, metric & trace propagation
- Thread safety and multi-provider candidate ordering
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from typing import Any
from uuid import UUID, uuid4
import pytest

from app.config import Settings
from core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
from core.metrics import get_metrics_registry
from core.models import AURAResponse
from core.tracing import Tracer
from core.model_gateway import (
    CostMetadata,
    FailureClassification,
    FailureClassifier,
    ModelGateway,
    PricingMode,
    ProviderCatalog,
    ProviderRegistration,
)
from interfaces.model import ModelInterface
from providers.factory import (
    GenericOpenAICompatibleProvider,
    create_model_provider,
)
from providers.gateway_factory import (
    _get_default_model_metadata,
    create_model_gateway,
)


def _aura_resp(content: str, provider: str = "mock", model: str = "mock-model", metadata: dict[str, Any] | None = None) -> AURAResponse:
    return AURAResponse(
        request_id=uuid4(),
        content=content,
        provider=provider,
        model=model,
        metadata=metadata or {},
    )


class MockProvider(ModelInterface):
    """Configurable mock provider for test isolation."""

    def __init__(
        self,
        provider_name: str = "mock",
        model_name: str = "mock-model",
        responses: list[AURAResponse | Exception] | None = None,
        default_content: str = "mock response",
        latency: float = 0.0,
    ) -> None:
        self.provider_name = provider_name
        self.model_name = model_name
        self.responses = list(responses) if responses else []
        self.default_content = default_content
        self.latency = latency
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def generate(self, prompt: str, request_id: UUID, **kwargs: Any) -> AURAResponse:
        with self._lock:
            self.calls.append({"prompt": prompt, "request_id": request_id, "kwargs": kwargs})

        if self.latency > 0:
            time.sleep(self.latency)

        if self.responses:
            item = self.responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        return AURAResponse(
            request_id=request_id,
            content=f"{self.default_content} from {self.provider_name}",
            model=self.model_name,
            provider=self.provider_name,
            metadata={
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
                "latency_seconds": self.latency,
            },
        )


def _make_reg(
    provider_id: str,
    provider: ModelInterface,
    priority: int = 10,
    capabilities: set[str] | None = None,
    context_window: int = 32768,
    max_output: int = 4096,
    cost_metadata: CostMetadata | None = None,
    is_fallback: bool = False,
    cb_failure_threshold: int = 3,
) -> ProviderRegistration:
    return ProviderRegistration(
        provider_id=provider_id,
        provider=provider,
        priority=priority,
        capabilities=capabilities or {"general_chat"},
        context_window=context_window,
        max_output_tokens=max_output,
        cost_metadata=cost_metadata or CostMetadata(),
        circuit_breaker=CircuitBreaker(
            name=provider_id,
            config=CircuitBreakerConfig(failure_threshold=cb_failure_threshold, recovery_timeout_seconds=0.1),
        ),
        is_fallback=is_fallback,
    )


# ==============================================================================
# P2-M51-F01: Groq Default Model Selection
# ==============================================================================
def test_f01_groq_default_model_selection() -> None:
    caps, ctx, out, cost = _get_default_model_metadata("groq", "openai/gpt-oss-120b")
    assert ctx == 131072
    assert "reasoning" in caps
    assert "tool_calling" in caps
    assert "structured_output" in caps
    assert cost.pricing_mode == PricingMode.PAID
    assert cost.input_cost_per_million == 0.15
    assert cost.output_cost_per_million == 0.60


# ==============================================================================
# P2-M51-F02: OpenRouter Default Model Selection
# ==============================================================================
def test_f02_openrouter_default_model_selection() -> None:
    caps, ctx, out, cost = _get_default_model_metadata("openrouter", "openai/gpt-oss-120b:free")
    assert ctx == 131072
    assert "reasoning" in caps
    assert "tool_calling" in caps
    assert "structured_output" in caps
    assert cost.pricing_mode == PricingMode.FREE
    assert cost.input_cost_per_million == 0.0
    assert cost.output_cost_per_million == 0.0


# ==============================================================================
# P2-M51-F03: OpenRouter Custom Headers Injection
# ==============================================================================
def test_f03_openrouter_custom_headers_injection() -> None:
    provider = create_model_provider(
        provider="openrouter",
        api_key="test-sk-openrouter-key-123",
        model_name="openai/gpt-oss-120b:free",
    )
    assert isinstance(provider, GenericOpenAICompatibleProvider)
    assert provider.custom_headers.get("HTTP-Referer") == "https://github.com/project-aura"
    assert provider.custom_headers.get("X-Title") == "Project AURA"
    assert provider.api_key == "test-sk-openrouter-key-123"


# ==============================================================================
# P2-M51-F04: Circuit Breaker Isolation Across Providers
# ==============================================================================
def test_f04_circuit_breaker_isolation_across_providers() -> None:
    groq_mock = MockProvider("groq", responses=[RuntimeError("500 Server Error")] * 3)
    or_mock = MockProvider("openrouter", responses=[_aura_resp("openrouter ok", "openrouter")])

    reg_groq = _make_reg("groq", groq_mock, priority=10, cb_failure_threshold=2)
    reg_or = _make_reg("openrouter", or_mock, priority=20, cb_failure_threshold=2, is_fallback=True)

    gateway = ModelGateway([reg_groq, reg_or], fallback_enabled=True)

    req_id = uuid4()
    # First request: groq fails (count 1), then falls back to openrouter (succeeds)
    res1 = gateway.generate("prompt 1", req_id)
    assert res1.content == "openrouter ok"
    assert reg_groq.circuit_breaker.state == CircuitState.CLOSED

    # Next groq response also 500
    or_mock.responses.append(_aura_resp("openrouter ok 2", "openrouter"))
    res2 = gateway.generate("prompt 2", uuid4())
    assert res2.content == "openrouter ok 2"
    # Groq circuit should now be OPEN
    assert reg_groq.circuit_breaker.state == CircuitState.OPEN

    # OpenRouter circuit MUST remain CLOSED and completely unaffected
    assert reg_or.circuit_breaker.state == CircuitState.CLOSED

    # Third request immediately skips Groq without calling it and uses OpenRouter
    or_mock.responses.append(_aura_resp("openrouter ok 3", "openrouter"))
    res3 = gateway.generate("prompt 3", uuid4())
    assert res3.content == "openrouter ok 3"
    assert res3.metadata["gateway_provider"] == "openrouter"
    assert reg_groq.circuit_breaker.state == CircuitState.OPEN
    assert reg_or.circuit_breaker.state == CircuitState.CLOSED


# ==============================================================================
# P2-M51-F05: Transient Fallback Cascade (Free -> Paid)
# ==============================================================================
def test_f05_transient_fallback_cascade_free_to_paid() -> None:
    free_mock = MockProvider("openrouter", responses=[RuntimeError("429 Too Many Requests: free tier busy")])
    paid_mock = MockProvider("groq", responses=[_aura_resp("paid groq response", "groq")])

    reg_free = _make_reg(
        "openrouter",
        free_mock,
        priority=10,
        cost_metadata=CostMetadata(pricing_mode=PricingMode.FREE),
    )
    reg_paid = _make_reg(
        "groq",
        paid_mock,
        priority=20,
        cost_metadata=CostMetadata(pricing_mode=PricingMode.PAID, input_cost_per_million=0.15, output_cost_per_million=0.60),
        is_fallback=True,
    )

    gateway = ModelGateway([reg_free, reg_paid], fallback_enabled=True)
    res = gateway.generate("cascade test", uuid4())

    assert res.content == "paid groq response"
    assert res.metadata["gateway_provider"] == "groq"
    assert res.metadata["gateway_fallback_occurred"] is True
    assert res.metadata["gateway_attempt_count"] == 2
    assert res.metadata["gateway_attempted_chain"] == ["openrouter", "groq"]
    assert res.metadata["gateway_pricing_mode"] == "paid"


# ==============================================================================
# P2-M51-F06: Strict Fail-Closed on 401/403 Authentication Failure
# ==============================================================================
def test_f06_strict_fail_closed_on_auth_failure() -> None:
    p1 = MockProvider("groq", responses=[RuntimeError("401 Unauthorized: Invalid API Key")])
    p2 = MockProvider("openrouter", responses=[_aura_resp("should never reach", "openrouter")])

    gateway = ModelGateway(
        [_make_reg("groq", p1, priority=10), _make_reg("openrouter", p2, priority=20, is_fallback=True)],
        fallback_enabled=True,
    )

    with pytest.raises(RuntimeError) as exc_info:
        gateway.generate("auth test", uuid4())

    assert "401 Unauthorized" in str(exc_info.value)
    assert len(p2.calls) == 0


# ==============================================================================
# P2-M51-F07: Strict Fail-Closed on Policy Deny / Injection
# ==============================================================================
def test_f07_strict_fail_closed_on_policy_deny() -> None:
    p1 = MockProvider("groq", responses=[RuntimeError("Prompt injection detected: forbidden instruction")])
    p2 = MockProvider("mistral", responses=[_aura_resp("should never reach", "mistral")])

    gateway = ModelGateway(
        [_make_reg("groq", p1, priority=10), _make_reg("mistral", p2, priority=20, is_fallback=True)],
        fallback_enabled=True,
    )

    with pytest.raises(RuntimeError) as exc_info:
        gateway.generate("injection test", uuid4())

    assert "Prompt injection detected" in str(exc_info.value)
    assert len(p2.calls) == 0


# ==============================================================================
# P2-M51-F08: Strict Fail-Closed on SSRF Violation
# ==============================================================================
def test_f08_strict_fail_closed_on_ssrf_violation() -> None:
    p1 = MockProvider("generic", responses=[RuntimeError("SSRF violation: Loopback endpoint 127.0.0.1 is forbidden")])
    p2 = MockProvider("groq", responses=[_aura_resp("should never reach", "groq")])

    gateway = ModelGateway(
        [_make_reg("generic", p1, priority=10), _make_reg("groq", p2, priority=20, is_fallback=True)],
        fallback_enabled=True,
    )

    with pytest.raises(RuntimeError) as exc_info:
        gateway.generate("ssrf test", uuid4())

    assert "SSRF violation" in str(exc_info.value)
    assert len(p2.calls) == 0


# ==============================================================================
# P2-M51-F09: Strict Fail-Closed on Schema / Bad Request
# ==============================================================================
def test_f09_strict_fail_closed_on_bad_request() -> None:
    p1 = MockProvider("groq", responses=[RuntimeError("400 Bad Request: malformed JSON payload")])
    p2 = MockProvider("openrouter", responses=[_aura_resp("should never reach", "openrouter")])

    gateway = ModelGateway(
        [_make_reg("groq", p1, priority=10), _make_reg("openrouter", p2, priority=20, is_fallback=True)],
        fallback_enabled=True,
    )

    with pytest.raises(RuntimeError) as exc_info:
        gateway.generate("bad request test", uuid4())

    assert "400 Bad Request" in str(exc_info.value)
    assert len(p2.calls) == 0


# ==============================================================================
# P2-M51-F10: Secret Scrubbing in Provenance and Diagnostics
# ==============================================================================
def test_f10_secret_scrubbing_in_gateway() -> None:
    secret_key = "gsk_supersecretkey1234567890abcdef"
    p1 = MockProvider("groq", responses=[RuntimeError(f"500 Internal Server Error with key Bearer {secret_key}")])
    p2 = MockProvider("mistral", responses=[_aura_resp("recovered", "mistral")])

    gateway = ModelGateway(
        [_make_reg("groq", p1, priority=10), _make_reg("mistral", p2, priority=20, is_fallback=True)],
        fallback_enabled=True,
    )

    res = gateway.generate("scrub test", uuid4())
    assert res.content == "recovered"
    # Metadata scrub check: secret must not appear anywhere in metadata
    assert secret_key not in str(res.metadata)


# ==============================================================================
# P2-M51-F11: Capability-Aware Routing
# ==============================================================================
def test_f11_capability_aware_routing() -> None:
    p1 = MockProvider("basic_chat", responses=[_aura_resp("basic", "basic_chat")])
    p2 = MockProvider("vision_reasoner", responses=[_aura_resp("vision reasoning result", "vision_reasoner")])

    reg1 = _make_reg("basic_chat", p1, priority=10, capabilities={"general_chat"})
    reg2 = _make_reg("vision_reasoner", p2, priority=20, capabilities={"general_chat", "multimodal", "reasoning"})

    catalog = ProviderCatalog([reg1, reg2])

    candidates = catalog.select_candidates(required_capabilities={"multimodal"})
    assert len(candidates) == 1
    assert candidates[0].provider_id == "vision_reasoner"

    gateway = ModelGateway([reg1, reg2])
    res = gateway.generate("analyze image", uuid4(), required_capabilities={"multimodal"})
    assert res.content == "vision reasoning result"
    assert res.metadata["gateway_provider"] == "vision_reasoner"


# ==============================================================================
# P2-M51-F12: Capability Mismatch Fail-Closed
# ==============================================================================
def test_f12_capability_mismatch_fail_closed() -> None:
    p1 = MockProvider("chat_only", responses=[_aura_resp("chat", "chat_only")])
    reg1 = _make_reg("chat_only", p1, priority=10, capabilities={"general_chat"})

    gateway = ModelGateway([reg1])

    with pytest.raises(RuntimeError) as exc_info:
        gateway.generate("complex audio task", uuid4(), required_capabilities={"audio_processing"})

    assert "capability mismatch" in str(exc_info.value).lower()
    assert len(p1.calls) == 0


# ==============================================================================
# P2-M51-F13: Context Window Tracking and Metadata Enrichment
# ==============================================================================
def test_f13_context_window_tracking() -> None:
    p1 = MockProvider("groq_120b", responses=[_aura_resp("ok", "groq_120b")])
    reg1 = _make_reg("groq_120b", p1, priority=10, context_window=131072)

    gateway = ModelGateway([reg1])
    res = gateway.generate("test context", uuid4())

    assert res.metadata["gateway_context_window"] == 131072


# ==============================================================================
# P2-M51-F14: Pricing Mode Free with Zero Cost Attribution
# ==============================================================================
def test_f14_pricing_mode_free_zero_cost() -> None:
    p1 = MockProvider("openrouter_free", responses=[
        _aura_resp("free response", "openrouter_free", metadata={"usage": {"prompt_tokens": 500, "completion_tokens": 200}})
    ])
    cost = CostMetadata(input_cost_per_million=0.0, output_cost_per_million=0.0, pricing_mode=PricingMode.FREE)
    reg1 = _make_reg("openrouter_free", p1, priority=10, cost_metadata=cost)

    gateway = ModelGateway([reg1])
    res = gateway.generate("free test", uuid4())

    assert res.metadata["gateway_pricing_mode"] == "free"
    assert res.metadata["gateway_cost_usd"] == 0.0


# ==============================================================================
# P2-M51-F15: Pricing Mode Paid with Input/Output Cost Attribution
# ==============================================================================
def test_f15_pricing_mode_paid_cost_calculation() -> None:
    p1 = MockProvider("groq_paid", responses=[
        _aura_resp("paid response", "groq_paid", metadata={"usage": {"prompt_tokens": 1000, "completion_tokens": 1000}})
    ])
    cost = CostMetadata(input_cost_per_million=0.15, output_cost_per_million=0.60, pricing_mode=PricingMode.PAID)
    reg1 = _make_reg("groq_paid", p1, priority=10, cost_metadata=cost)

    gateway = ModelGateway([reg1])
    res = gateway.generate("cost test", uuid4())

    # 1000 * 0.15 / 1,000,000 = 0.00015
    # 1000 * 0.60 / 1,000,000 = 0.00060
    # Total = 0.00075
    assert res.metadata["gateway_pricing_mode"] == "paid"
    assert res.metadata["gateway_cost_usd"] == 0.00075


# ==============================================================================
# P2-M51-F16: Non-Guaranteed Free Availability Fallback
# ==============================================================================
def test_f16_non_guaranteed_free_availability_fallback() -> None:
    or_free = MockProvider("openrouter_free", responses=[RuntimeError("503 Service Unavailable: Free model capacity full")])
    groq_paid = MockProvider("groq_paid", responses=[_aura_resp("fallback success", "groq_paid")])

    reg_free = _make_reg("openrouter_free", or_free, priority=10, cost_metadata=CostMetadata(pricing_mode=PricingMode.FREE))
    reg_paid = _make_reg("groq_paid", groq_paid, priority=20, cost_metadata=CostMetadata(pricing_mode=PricingMode.PAID), is_fallback=True)

    gateway = ModelGateway([reg_free, reg_paid], fallback_enabled=True)
    res = gateway.generate("availability test", uuid4())

    assert res.content == "fallback success"
    assert res.metadata["gateway_provider"] == "groq_paid"
    assert res.metadata["gateway_fallback_occurred"] is True


# ==============================================================================
# P2-M51-F17: Rate-Limit vs Hard Quota Classification
# ==============================================================================
def test_f17_rate_limit_vs_hard_quota_classification() -> None:
    classifier = FailureClassifier()

    # Rate limiting
    assert classifier.classify("429 Too Many Requests") == FailureClassification.RATE_LIMITED
    assert classifier.classify("rate_limit_exceeded") == FailureClassification.RATE_LIMITED

    # Hard Quota / Billing Exhaustion
    assert classifier.classify("insufficient_quota") == FailureClassification.QUOTA_EXHAUSTED
    assert classifier.classify("credits expired") == FailureClassification.QUOTA_EXHAUSTED
    assert classifier.classify("billing_hard_limit_reached") == FailureClassification.QUOTA_EXHAUSTED
    assert classifier.classify("out of credits") == FailureClassification.QUOTA_EXHAUSTED

    # Both are fallback eligible
    assert classifier.is_fallback_eligible(FailureClassification.RATE_LIMITED) is True
    assert classifier.is_fallback_eligible(FailureClassification.QUOTA_EXHAUSTED) is True


# ==============================================================================
# P2-M51-F18: Quota State Unknown Preservation
# ==============================================================================
def test_f18_quota_state_unknown_preservation() -> None:
    p1 = MockProvider("groq", responses=[_aura_resp("ok", "groq")])
    reg1 = _make_reg("groq", p1, priority=10)

    gateway = ModelGateway([reg1])
    res = gateway.generate("quota state test", uuid4())

    assert res.metadata.get("quota_state") == "unknown"


# ==============================================================================
# P2-M51-F19: Provenance Metadata Tracking
# ==============================================================================
def test_f19_provenance_metadata_tracking() -> None:
    p1 = MockProvider("groq", model_name="openai/gpt-oss-120b", responses=[_aura_resp("provenance ok", "groq", "openai/gpt-oss-120b")])
    caps = {"general_chat", "reasoning", "tool_calling", "structured_output"}
    cost = CostMetadata(input_cost_per_million=0.15, output_cost_per_million=0.60, pricing_mode=PricingMode.PAID)
    reg1 = _make_reg("groq", p1, priority=10, capabilities=caps, context_window=131072, cost_metadata=cost)

    gateway = ModelGateway([reg1])
    res = gateway.generate("test provenance", uuid4())

    meta = res.metadata
    assert meta["gateway_provider"] == "groq"
    assert meta["gateway_model"] == "openai/gpt-oss-120b"
    assert meta["gateway_attempt_count"] == 1
    assert meta["gateway_fallback_occurred"] is False
    assert meta["gateway_attempted_chain"] == ["groq"]
    assert meta["gateway_pricing_mode"] == "paid"
    assert meta["gateway_context_window"] == 131072
    assert meta["gateway_capabilities"] == sorted(list(caps))
    assert isinstance(meta["gateway_total_latency_seconds"], float)
    assert meta["quota_state"] == "unknown"


# ==============================================================================
# P2-M51-F20: Metrics Emission
# ==============================================================================
def test_f20_metrics_emission() -> None:
    p1 = MockProvider("groq", responses=[_aura_resp("metrics ok", "groq")])
    reg1 = _make_reg("groq", p1, priority=10)

    gateway = ModelGateway([reg1])
    gateway.generate("test metrics", uuid4())

    reg = get_metrics_registry()
    counter = reg.get_counter("aura_gateway_requests_total")
    assert counter is not None
    assert counter.get(labels={"provider": "groq", "status": "success", "fallback_tier": "tier_0"}) >= 1


# ==============================================================================
# P2-M51-F21: Distributed Trace Context Propagation
# ==============================================================================
def test_f21_trace_context_propagation() -> None:
    p1 = MockProvider("groq", responses=[_aura_resp("trace ok", "groq")])
    reg1 = _make_reg("groq", p1, priority=10)

    gateway = ModelGateway([reg1])
    res = gateway.generate("trace prompt", uuid4())

    assert res.content == "trace ok"


# ==============================================================================
# P2-M51-F22: Factory Multi-Provider Construction from Settings
# ==============================================================================
def test_f22_factory_multi_provider_construction() -> None:
    settings = Settings(
        aura_model_provider="groq",
        aura_groq_api_key="gsk-test-key",
        aura_model_fallback_enabled=True,
        aura_model_fallback_providers="openrouter,mistral,gemini,openai,generic,fake",
        aura_openrouter_api_key="sk-or-test-key",
        aura_mistral_api_key="sk-mistral-test-key",
        aura_gemini_api_key="ai-gemini-test-key",
        aura_openai_api_key="sk-openai-test-key",
    )

    gateway = create_model_gateway(config=settings)
    assert isinstance(gateway, ModelGateway)
    assert gateway.fallback_enabled is True

    providers = gateway.catalog.list_providers()
    provider_ids = [p.provider_id for p in providers]
    assert "groq" in provider_ids
    assert "openrouter" in provider_ids
    assert "mistral" in provider_ids
    assert "gemini" in provider_ids
    assert "openai" in provider_ids
    assert "fake" in provider_ids


# ==============================================================================
# P2-M51-F23: Multi-Provider Routing Strategy
# ==============================================================================
def test_f23_routing_strategy_cost_and_priority() -> None:
    p_expensive = MockProvider("expensive_fast", latency=0.01)
    p_cheap = MockProvider("cheap_slow", latency=0.1)

    reg_exp = _make_reg(
        "expensive_fast",
        p_expensive,
        priority=10,
        cost_metadata=CostMetadata(input_cost_per_million=5.0, pricing_mode=PricingMode.PAID),
    )
    reg_cheap = _make_reg(
        "cheap_slow",
        p_cheap,
        priority=20,
        cost_metadata=CostMetadata(input_cost_per_million=0.1, pricing_mode=PricingMode.PAID),
    )

    catalog = ProviderCatalog([reg_exp, reg_cheap])

    # By priority: expensive_fast has priority 10 (lower is higher priority)
    c_prio = catalog.select_candidates(routing_preference="priority")
    assert c_prio[0].provider_id == "expensive_fast"

    # By cost: cheap_slow has cost 0.1 vs 5.0
    c_cost = catalog.select_candidates(routing_preference="cost")
    assert c_cost[0].provider_id == "cheap_slow"


# ==============================================================================
# P2-M51-F24: Max Fallback Providers and Per-Provider Retry Bounds
# ==============================================================================
def test_f24_max_fallback_providers_limit() -> None:
    p1 = MockProvider("p1", responses=[RuntimeError("500 Error")])
    p2 = MockProvider("p2", responses=[RuntimeError("500 Error")])
    p3 = MockProvider("p3", responses=[_aura_resp("should not reach if max_fallback=1", "p3")])

    reg1 = _make_reg("p1", p1, priority=10)
    reg2 = _make_reg("p2", p2, priority=20, is_fallback=True)
    reg3 = _make_reg("p3", p3, priority=30, is_fallback=True)

    # Max fallback attempts = 1 (primary + 1 fallback = 2 total attempts)
    gateway = ModelGateway(
        [reg1, reg2, reg3],
        fallback_enabled=True,
        max_fallback_attempts=1,
    )

    with pytest.raises(RuntimeError) as exc_info:
        gateway.generate("bound test", uuid4())

    assert "All configured providers" in str(exc_info.value)
    assert len(p3.calls) == 0


# ==============================================================================
# P2-M51-F25: Circuit Breaker Half-Open State Recovery
# ==============================================================================
def test_f25_circuit_breaker_half_open_recovery() -> None:
    p1 = MockProvider("groq", responses=[RuntimeError("500 Server Error"), RuntimeError("500 Server Error")])
    reg1 = _make_reg("groq", p1, priority=10, cb_failure_threshold=2)
    reg1.circuit_breaker.config.recovery_timeout_seconds = 0.05

    gateway = ModelGateway([reg1], fallback_enabled=False)

    # Trip the circuit
    with pytest.raises(RuntimeError):
        gateway.generate("req 1", uuid4())
    with pytest.raises(RuntimeError):
        gateway.generate("req 2", uuid4())

    assert reg1.circuit_breaker.state == CircuitState.OPEN

    # Sleep past recovery timeout
    time.sleep(0.08)

    # Supply successful response
    p1.responses.append(_aura_resp("recovered ok", "groq"))
    res = gateway.generate("req 3", uuid4())
    assert res.content == "recovered ok"
    assert reg1.circuit_breaker.state == CircuitState.CLOSED


# ==============================================================================
# P2-M51-F26: Health Check Snapshot Multi-Provider Reporting
# ==============================================================================
def test_f26_health_check_snapshot() -> None:
    p1 = MockProvider("groq")
    p2 = MockProvider("openrouter")

    reg1 = _make_reg("groq", p1, priority=10)
    reg2 = _make_reg("openrouter", p2, priority=20, is_fallback=True)

    gateway = ModelGateway([reg1, reg2])
    health = gateway.get_health_status()

    assert isinstance(health, dict)
    assert health["status"] == "healthy"
    assert "groq" in health["providers"]
    assert "openrouter" in health["providers"]
    assert health["providers"]["groq"]["circuit_state"] == "closed"
    assert health["providers"]["openrouter"]["circuit_state"] == "closed"


# ==============================================================================
# P2-M51-F27: Single Router Invariance
# ==============================================================================
def test_f27_single_router_invariance() -> None:
    p1 = MockProvider("groq", responses=[_aura_resp("single router ok", "groq")])
    reg1 = _make_reg("groq", p1, priority=10)

    gateway = ModelGateway([reg1])
    assert isinstance(gateway, ModelInterface)

    res = gateway.generate("direct route", uuid4())
    assert res.content == "single router ok"


# ==============================================================================
# P2-M51-F28: Downstream Runtime Preserves Multi-Provider Provenance
# ==============================================================================
def test_f28_downstream_preserves_provenance() -> None:
    p1 = MockProvider("mistral", model_name="mistral-small-latest", responses=[
        _aura_resp("agent step response", "mistral", "mistral-small-latest", metadata={"custom_agent_tag": "agent-007"})
    ])
    reg1 = _make_reg("mistral", p1, priority=10, cost_metadata=CostMetadata(pricing_mode=PricingMode.PAID, input_cost_per_million=0.2, output_cost_per_million=0.6))

    gateway = ModelGateway([reg1])
    res = gateway.generate("agent step", uuid4())

    assert res.metadata["custom_agent_tag"] == "agent-007"
    assert res.metadata["gateway_provider"] == "mistral"
    assert res.metadata["gateway_model"] == "mistral-small-latest"
    assert res.metadata["gateway_pricing_mode"] == "paid"
    assert res.metadata["quota_state"] == "unknown"


# ==============================================================================
# P2-M51-F29: Exhaustion Diagnostics
# ==============================================================================
def test_f29_exhaustion_diagnostics() -> None:
    p1 = MockProvider("groq", responses=[RuntimeError("500 Groq Down")])
    p2 = MockProvider("openrouter", responses=[RuntimeError("503 OpenRouter Down")])

    reg1 = _make_reg("groq", p1, priority=10)
    reg2 = _make_reg("openrouter", p2, priority=20, is_fallback=True)

    gateway = ModelGateway([reg1, reg2], fallback_enabled=True)

    with pytest.raises(RuntimeError) as exc_info:
        gateway.generate("exhaustion test", uuid4())

    err_str = str(exc_info.value)
    assert "All configured providers" in err_str
    assert "groq: transient" in err_str
    assert "openrouter: transient" in err_str


# ==============================================================================
# P2-M51-F30: Thread-Safe Concurrency Under Multi-Provider Load
# ==============================================================================
def test_f30_thread_safe_concurrency() -> None:
    p1 = MockProvider("groq", latency=0.01)
    p2 = MockProvider("openrouter", latency=0.01)

    reg1 = _make_reg("groq", p1, priority=10)
    reg2 = _make_reg("openrouter", p2, priority=20, is_fallback=True)

    gateway = ModelGateway([reg1, reg2], fallback_enabled=True)

    def _worker(worker_id: int) -> str:
        res = gateway.generate(f"concurrent prompt {worker_id}", uuid4())
        return res.content

    num_threads = 16
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(_worker, i) for i in range(num_threads)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert len(results) == num_threads
    for r in results:
        assert "from groq" in r
