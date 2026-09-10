import pytest
from uuid import uuid4

from core.capability_registry import CapabilityRegistry, ModelCapability, ModelDescriptor
from core.models import AURAResponse
from core.provider_registry import ProviderRegistry
from core.resilient_router import (
    ModelRoutingExhaustedError,
    ResilientModelRouter,
    RoutingOutcome,
)
from interfaces.model import ModelInterface


class MockProvider(ModelInterface):
    def __init__(self, name: str, should_fail: bool = False, fail_count: int = 0):
        self.name = name
        self.should_fail = should_fail
        self.fail_count = fail_count
        self.call_count = 0

    def generate(self, prompt: str, request_id: str) -> AURAResponse:
        self.call_count += 1
        if self.should_fail:
            raise RuntimeError(f"Provider '{self.name}' simulated failure.")
        if self.call_count <= self.fail_count:
            raise RuntimeError(f"Provider '{self.name}' temporary failure.")
        return AURAResponse(
            request_id=request_id,
            content=f"Response from {self.name}",
            metadata={"provider": self.name},
        )


def _setup_router():
    cap_reg = CapabilityRegistry()
    prov_reg = ProviderRegistry()

    # Provider A (Primary)
    p_a = MockProvider("prov_a")
    prov_reg.register("prov_a", p_a)
    cap_reg.register(
        ModelDescriptor(
            model_id="model_a",
            provider_id="prov_a",
            capabilities=frozenset([ModelCapability.REASONING.value]),
        )
    )

    # Provider B (Secondary / Fallback)
    p_b = MockProvider("prov_b")
    prov_reg.register("prov_b", p_b)
    cap_reg.register(
        ModelDescriptor(
            model_id="model_b",
            provider_id="prov_b",
            capabilities=frozenset([ModelCapability.REASONING.value]),
        )
    )

    # Provider C (Tertiary)
    p_c = MockProvider("prov_c")
    prov_reg.register("prov_c", p_c)
    cap_reg.register(
        ModelDescriptor(
            model_id="model_c",
            provider_id="prov_c",
            capabilities=frozenset([ModelCapability.REASONING.value]),
        )
    )

    router = ResilientModelRouter(
        capability_registry=cap_reg,
        provider_registry=prov_reg,
        max_fallback_attempts=2,
    )
    return router, prov_reg, cap_reg, p_a, p_b, p_c


def test_primary_provider_selection_success():
    router, prov_reg, cap_reg, p_a, p_b, p_c = _setup_router()

    resp, telemetry = router.generate_with_fallback(prompt="Hello", request_id=uuid4())
    assert resp.content == "Response from prov_a"
    assert telemetry.outcome == RoutingOutcome.SUCCESS_PRIMARY
    assert telemetry.fallback_count == 0
    assert telemetry.successful_provider_id == "prov_a"
    assert p_a.call_count == 1
    assert p_b.call_count == 0


def test_fallback_on_primary_failure():
    router, prov_reg, cap_reg, p_a, p_b, p_c = _setup_router()
    p_a.should_fail = True

    resp, telemetry = router.generate_with_fallback(prompt="Hello", request_id=uuid4())
    assert resp.content == "Response from prov_b"
    assert telemetry.outcome == RoutingOutcome.SUCCESS_FALLBACK
    assert telemetry.fallback_count == 1
    assert telemetry.successful_provider_id == "prov_b"
    assert p_a.call_count == 1
    assert p_b.call_count == 1


def test_routing_exhausted_when_all_fail():
    router, prov_reg, cap_reg, p_a, p_b, p_c = _setup_router()
    p_a.should_fail = True
    p_b.should_fail = True
    p_c.should_fail = True

    with pytest.raises(ModelRoutingExhaustedError, match="All model provider candidates failed"):
        router.generate_with_fallback(prompt="Hello", request_id=uuid4())

    assert p_a.call_count == 1
    assert p_b.call_count == 1
    assert p_c.call_count == 1


def test_circuit_aware_routing_skips_open_circuits():
    router, prov_reg, cap_reg, p_a, p_b, p_c = _setup_router()

    # Trip circuit on prov_a by recording failures
    cb_a = router.health_tracker.get_or_create_circuit_breaker("prov_a")
    for _ in range(5):
        cb_a.record_failure("offline")

    # Router should select prov_b directly as primary candidate
    route_res = router.route()
    assert route_res.provider_id == "prov_b"


def test_fallback_disabled_behavior():
    router, prov_reg, cap_reg, p_a, p_b, p_c = _setup_router()
    router.fallback_enabled = False
    p_a.should_fail = True

    with pytest.raises(ModelRoutingExhaustedError):
        router.generate_with_fallback(prompt="Hello", request_id=uuid4())

    assert p_a.call_count == 1
    assert p_b.call_count == 0
