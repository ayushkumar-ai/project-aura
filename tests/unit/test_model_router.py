import pytest

from core.capability_registry import (
    CapabilityRegistry,
    ModelCapability,
    ModelDescriptor,
)
from core.model_router import ModelRouteResult, ModelRouter, TaskRequirements
from core.provider_registry import ProviderRegistry
from interfaces.model import ModelInterface
from providers.fake_model import FakeModelProvider
from providers.openai_model import OpenAIProvider


@pytest.fixture
def setup_registries():
    cap_reg = CapabilityRegistry()
    prov_reg = ProviderRegistry()

    p_fake1 = FakeModelProvider()
    p_fake2 = FakeModelProvider()
    p_openai = OpenAIProvider(model_name="gpt-4o", api_key="test-key")

    prov_reg.register("fake1", p_fake1)
    prov_reg.register("fake2", p_fake2)
    prov_reg.register("openai", p_openai)

    # Models:
    # fake1: model-basic (reasoning)
    # fake1: model-code (reasoning, coding)
    # fake2: model-vision (vision, coding)
    # openai: gpt-4o (reasoning, coding, vision, tool_use)
    # openai: gpt-4o-mini (coding, tool_use)
    m_basic = ModelDescriptor(
        model_id="model-basic",
        provider_id="fake1",
        capabilities={ModelCapability.REASONING},
    )
    m_code = ModelDescriptor(
        model_id="model-code",
        provider_id="fake1",
        capabilities={ModelCapability.REASONING, ModelCapability.CODING},
    )
    m_vision = ModelDescriptor(
        model_id="model-vision",
        provider_id="fake2",
        capabilities={ModelCapability.VISION, ModelCapability.CODING},
    )
    m_gpt4o = ModelDescriptor(
        model_id="gpt-4o",
        provider_id="openai",
        capabilities={
            ModelCapability.REASONING,
            ModelCapability.CODING,
            ModelCapability.VISION,
            ModelCapability.TOOL_USE,
        },
    )
    m_mini = ModelDescriptor(
        model_id="gpt-4o-mini",
        provider_id="openai",
        capabilities={ModelCapability.CODING, ModelCapability.TOOL_USE},
    )

    for m in [m_basic, m_code, m_vision, m_gpt4o, m_mini]:
        cap_reg.register_model(m)

    return cap_reg, prov_reg, {
        "p_fake1": p_fake1,
        "p_fake2": p_fake2,
        "p_openai": p_openai,
        "m_basic": m_basic,
        "m_code": m_code,
        "m_vision": m_vision,
        "m_gpt4o": m_gpt4o,
        "m_mini": m_mini,
    }


def test_model_router_init_validation():
    cap_reg = CapabilityRegistry()
    prov_reg = ProviderRegistry()

    router = ModelRouter(cap_reg, prov_reg)
    assert router.capability_registry is cap_reg
    assert router.provider_registry is prov_reg

    with pytest.raises(TypeError, match="capability_registry"):
        ModelRouter("not_a_cap_reg", prov_reg)

    with pytest.raises(TypeError, match="provider_registry"):
        ModelRouter(cap_reg, "not_a_prov_reg")


def test_task_requirements_initialization_and_normalization():
    req1 = TaskRequirements(
        required_capabilities=[ModelCapability.CODING, "REASONING"],
        preferred_model=" gpt-4o ",
        preferred_provider=" OpenAI ",
    )
    assert req1.required_capabilities == frozenset({"coding", "reasoning"})
    assert req1.preferred_model == "gpt-4o"
    assert req1.preferred_provider == "OpenAI"

    # Single capability
    req2 = TaskRequirements(required_capabilities=ModelCapability.VISION)
    assert req2.required_capabilities == frozenset({"vision"})

    req3 = TaskRequirements(required_capabilities="audio")
    assert req3.required_capabilities == frozenset({"audio"})

    # Empty
    req_empty = TaskRequirements()
    assert req_empty.required_capabilities == frozenset()
    assert req_empty.preferred_model is None
    assert req_empty.preferred_provider is None

    # Invalid types
    with pytest.raises(TypeError):
        TaskRequirements(required_capabilities=123)

    with pytest.raises(TypeError):
        TaskRequirements(required_capabilities=[123])

    with pytest.raises(ValueError):
        TaskRequirements(preferred_model="")

    with pytest.raises(ValueError):
        TaskRequirements(preferred_provider="   ")


def test_model_route_result_properties(setup_registries):
    cap_reg, prov_reg, fixture_data = setup_registries
    descriptor = fixture_data["m_gpt4o"]
    provider = fixture_data["p_openai"]

    res = ModelRouteResult(model_descriptor=descriptor, provider=provider)
    assert res.descriptor is descriptor
    assert res.model_descriptor is descriptor
    assert res.provider is provider
    assert res.model_id == "gpt-4o"
    assert res.provider_id == "openai"


def test_route_single_capability(setup_registries):
    cap_reg, prov_reg, fixture_data = setup_registries
    router = ModelRouter(cap_reg, prov_reg)

    # Only fake2:model-vision and openai:gpt-4o have vision.
    # Deterministic tie-breaking sorts by (provider_id, model_id) -> fake2 < openai -> fake2:model-vision
    req = TaskRequirements(required_capabilities=[ModelCapability.VISION])
    result = router.route(req)

    assert result.model_id == "model-vision"
    assert result.provider_id == "fake2"
    assert result.provider is fixture_data["p_fake2"]


def test_route_multiple_capabilities(setup_registries):
    cap_reg, prov_reg, fixture_data = setup_registries
    router = ModelRouter(cap_reg, prov_reg)

    # Required: REASONING, VISION, TOOL_USE -> Only gpt-4o has all 3
    req = TaskRequirements(
        required_capabilities=[
            ModelCapability.REASONING,
            ModelCapability.VISION,
            ModelCapability.TOOL_USE,
        ]
    )
    result = router.route(req)

    assert result.model_id == "gpt-4o"
    assert result.provider_id == "openai"
    assert result.provider is fixture_data["p_openai"]


def test_route_no_compatible_model(setup_registries):
    cap_reg, prov_reg, _ = setup_registries
    router = ModelRouter(cap_reg, prov_reg)

    req = TaskRequirements(required_capabilities=[ModelCapability.AUDIO])
    with pytest.raises(ValueError, match="No compatible model found"):
        router.route(req)


def test_route_preferred_model_success(setup_registries):
    cap_reg, prov_reg, fixture_data = setup_registries
    router = ModelRouter(cap_reg, prov_reg)

    req = TaskRequirements(
        required_capabilities=[ModelCapability.CODING],
        preferred_model="model-code",
    )
    result = router.route(req)

    assert result.model_id == "model-code"
    assert result.provider_id == "fake1"
    assert result.provider is fixture_data["p_fake1"]


def test_route_preferred_model_lacking_capability(setup_registries):
    cap_reg, prov_reg, _ = setup_registries
    router = ModelRouter(cap_reg, prov_reg)

    req = TaskRequirements(
        required_capabilities=[ModelCapability.VISION],
        preferred_model="model-basic",  # Only has REASONING
    )
    with pytest.raises(ValueError, match="does not support required capabilities"):
        router.route(req)


def test_route_preferred_model_unknown(setup_registries):
    cap_reg, prov_reg, _ = setup_registries
    router = ModelRouter(cap_reg, prov_reg)

    req = TaskRequirements(preferred_model="nonexistent-model")
    with pytest.raises(KeyError, match="Preferred model 'nonexistent-model' is not registered"):
        router.route(req)


def test_route_preferred_model_mismatched_preferred_provider(setup_registries):
    cap_reg, prov_reg, _ = setup_registries
    router = ModelRouter(cap_reg, prov_reg)

    req = TaskRequirements(
        preferred_model="gpt-4o",  # Provider is openai
        preferred_provider="fake1",
    )
    with pytest.raises(ValueError, match="does not match preferred provider"):
        router.route(req)


def test_route_preferred_provider_success(setup_registries):
    cap_reg, prov_reg, fixture_data = setup_registries
    router = ModelRouter(cap_reg, prov_reg)

    # Both fake1 and openai have CODING models.
    # Specifying preferred_provider="openai" must pick from openai candidates.
    req = TaskRequirements(
        required_capabilities=[ModelCapability.CODING],
        preferred_provider="openai",
    )
    result = router.route(req)

    assert result.provider_id == "openai"
    # Deterministic tie-break among openai candidates with CODING (gpt-4o vs gpt-4o-mini) -> gpt-4o < gpt-4o-mini
    assert result.model_id == "gpt-4o"
    assert result.provider is fixture_data["p_openai"]


def test_route_preferred_provider_no_compatible_model(setup_registries):
    cap_reg, prov_reg, _ = setup_registries
    router = ModelRouter(cap_reg, prov_reg)

    req = TaskRequirements(
        required_capabilities=[ModelCapability.VISION],
        preferred_provider="fake1",  # fake1 only has basic (reasoning) and code (reasoning, coding)
    )
    with pytest.raises(ValueError, match="No compatible model found for preferred provider"):
        router.route(req)


def test_route_preferred_provider_unknown(setup_registries):
    cap_reg, prov_reg, _ = setup_registries
    router = ModelRouter(cap_reg, prov_reg)

    req = TaskRequirements(preferred_provider="unknown-provider")
    with pytest.raises(KeyError, match="Preferred provider 'unknown-provider' is not registered"):
        router.route(req)


def test_route_deterministic_tie_breaking():
    cap_reg = CapabilityRegistry()
    prov_reg = ProviderRegistry()

    p_b = FakeModelProvider()
    p_a = FakeModelProvider()

    prov_reg.register("provider-b", p_b)
    prov_reg.register("provider-a", p_a)

    # Register in reverse lexical order
    m_z = ModelDescriptor(model_id="z-model", provider_id="provider-a", capabilities={ModelCapability.CODING})
    m_a = ModelDescriptor(model_id="a-model", provider_id="provider-a", capabilities={ModelCapability.CODING})
    m_b = ModelDescriptor(model_id="b-model", provider_id="provider-b", capabilities={ModelCapability.CODING})

    cap_reg.register_model(m_z)
    cap_reg.register_model(m_b)
    cap_reg.register_model(m_a)

    router = ModelRouter(cap_reg, prov_reg)
    result = router.route(TaskRequirements(required_capabilities=[ModelCapability.CODING]))

    # (provider-a, a-model) is lexicographically first
    assert result.provider_id == "provider-a"
    assert result.model_id == "a-model"


def test_route_ignores_candidate_with_unregistered_provider():
    cap_reg = CapabilityRegistry()
    prov_reg = ProviderRegistry()

    # Register provider for prov-registered only
    p_reg = FakeModelProvider()
    prov_reg.register("prov-registered", p_reg)

    # Descriptor for un-registered provider
    m_unreg = ModelDescriptor(
        model_id="model-unreg",
        provider_id="prov-unregistered",
        capabilities={ModelCapability.CODING},
    )
    m_reg = ModelDescriptor(
        model_id="model-reg",
        provider_id="prov-registered",
        capabilities={ModelCapability.CODING},
    )

    cap_reg.register_model(m_unreg)
    cap_reg.register_model(m_reg)

    router = ModelRouter(cap_reg, prov_reg)
    result = router.route(TaskRequirements(required_capabilities=[ModelCapability.CODING]))

    assert result.provider_id == "prov-registered"
    assert result.model_id == "model-reg"


def test_route_preferred_model_with_unregistered_provider():
    cap_reg = CapabilityRegistry()
    prov_reg = ProviderRegistry()

    m_unreg = ModelDescriptor(
        model_id="model-unreg",
        provider_id="prov-unregistered",
        capabilities={ModelCapability.CODING},
    )
    cap_reg.register_model(m_unreg)

    router = ModelRouter(cap_reg, prov_reg)
    req = TaskRequirements(preferred_model="model-unreg")

    with pytest.raises(KeyError, match="is not registered in ProviderRegistry"):
        router.route(req)


def test_route_empty_requirements_selects_first_deterministic(setup_registries):
    cap_reg, prov_reg, fixture_data = setup_registries
    router = ModelRouter(cap_reg, prov_reg)

    # None or empty requirements
    result1 = router.route()
    result2 = router.route(TaskRequirements())

    # Lexicographically first among (fake1:model-basic, fake1:model-code, fake2:model-vision, openai:gpt-4o, openai:gpt-4o-mini)
    # is fake1:model-basic
    assert result1.provider_id == "fake1"
    assert result1.model_id == "model-basic"
    assert result2.provider_id == "fake1"
    assert result2.model_id == "model-basic"


def test_route_with_string_capabilities(setup_registries):
    cap_reg, prov_reg, _ = setup_registries
    router = ModelRouter(cap_reg, prov_reg)

    req = TaskRequirements(required_capabilities=["coding", "VISION"])
    result = router.route(req)

    assert result.model_id == "model-vision"
    assert result.provider_id == "fake2"


def test_route_invalid_requirements_type(setup_registries):
    cap_reg, prov_reg, _ = setup_registries
    router = ModelRouter(cap_reg, prov_reg)

    with pytest.raises(TypeError, match="requirements must be an instance of TaskRequirements"):
        router.route("invalid_type")
