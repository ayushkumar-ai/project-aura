import pytest

from core.capability_registry import (
    CapabilityRegistry,
    ModelCapability,
    ModelCapabilityRegistry,
    ModelDescriptor,
)


def test_model_descriptor_initialization_and_normalization():
    descriptor = ModelDescriptor(
        model_id="gpt-4o",
        provider_id="openai",
        capabilities={ModelCapability.REASONING, "coding", "  VISION  "},
        metadata={"context_window": "128k"},
    )

    assert descriptor.model_id == "gpt-4o"
    assert descriptor.provider_id == "openai"
    assert descriptor.capabilities == frozenset({"reasoning", "coding", "vision"})
    assert descriptor.metadata == {"context_window": "128k"}


def test_model_descriptor_supports_capability():
    descriptor = ModelDescriptor(
        model_id="claude-3-5-sonnet",
        provider_id="anthropic",
        capabilities={ModelCapability.CODING, ModelCapability.REASONING},
    )

    assert descriptor.supports(ModelCapability.CODING) is True
    assert descriptor.supports("coding") is True
    assert descriptor.supports("CODING") is True
    assert descriptor.supports(ModelCapability.VISION) is False
    assert descriptor.supports("unknown") is False
    assert descriptor.supports(None) is False


def test_model_descriptor_rejects_empty_ids():
    with pytest.raises(ValueError, match="Model ID must be a non-empty string"):
        ModelDescriptor(model_id="", provider_id="openai")

    with pytest.raises(ValueError, match="Model ID must be a non-empty string"):
        ModelDescriptor(model_id="   ", provider_id="openai")

    with pytest.raises(ValueError, match="Provider ID must be a non-empty string"):
        ModelDescriptor(model_id="gpt-4", provider_id="")

    with pytest.raises(ValueError, match="Provider ID must be a non-empty string"):
        ModelDescriptor(model_id="gpt-4", provider_id="   ")


def test_capability_registry_registers_and_retrieves_model():
    registry = CapabilityRegistry()
    descriptor = ModelDescriptor(
        model_id="fake-default",
        provider_id="fake",
        capabilities={ModelCapability.TOOL_USE},
    )

    registry.register_model(descriptor)

    assert registry.get_model("fake-default") is descriptor
    assert registry.has_model("fake-default") is True
    assert registry.has_model("unknown") is False


def test_capability_registry_rejects_duplicate_model():
    registry = CapabilityRegistry()
    descriptor = ModelDescriptor(model_id="model-1", provider_id="provider-a")

    registry.register_model(descriptor)

    with pytest.raises(ValueError, match="Model already registered"):
        registry.register_model(descriptor)


def test_capability_registry_rejects_invalid_descriptor():
    registry = CapabilityRegistry()

    with pytest.raises(TypeError, match="Descriptor must be an instance of ModelDescriptor"):
        registry.register_model("not_a_descriptor")


def test_capability_registry_raises_for_unknown_model():
    registry = CapabilityRegistry()

    with pytest.raises(KeyError, match="Unknown model"):
        registry.get_model("nonexistent")


def test_capability_registry_lists_models_and_filters_by_provider():
    registry = CapabilityRegistry()

    m1 = ModelDescriptor(model_id="gpt-4o", provider_id="openai", capabilities={ModelCapability.CODING})
    m2 = ModelDescriptor(model_id="gpt-4o-mini", provider_id="openai", capabilities={ModelCapability.TOOL_USE})
    m3 = ModelDescriptor(model_id="claude-3-5", provider_id="anthropic", capabilities={ModelCapability.CODING})

    registry.register(m1)
    registry.register(m2)
    registry.register(m3)

    assert registry.list_models() == [m1, m2, m3]
    assert registry.list_models(provider_id="openai") == [m1, m2]
    assert registry.list_models(provider_id="anthropic") == [m3]
    assert registry.list_models(provider_id="unknown") == []


def test_capability_registry_finds_models_by_capability():
    registry = CapabilityRegistry()

    coding_model = ModelDescriptor(
        model_id="coder",
        provider_id="prov1",
        capabilities={ModelCapability.CODING},
    )
    multimodal_model = ModelDescriptor(
        model_id="multimodal",
        provider_id="prov2",
        capabilities={ModelCapability.CODING, ModelCapability.VISION, ModelCapability.REASONING},
    )
    voice_model = ModelDescriptor(
        model_id="audio-agent",
        provider_id="prov3",
        capabilities={ModelCapability.AUDIO},
    )

    registry.register_model(coding_model)
    registry.register_model(multimodal_model)
    registry.register_model(voice_model)

    coding_results = registry.find_models_by_capability(ModelCapability.CODING)
    assert coding_results == [coding_model, multimodal_model]

    vision_results = registry.find_models_by_capability("vision")
    assert vision_results == [multimodal_model]

    audio_results = registry.find_models_by_capability(ModelCapability.AUDIO)
    assert audio_results == [voice_model]

    video_results = registry.find_models_by_capability(ModelCapability.VIDEO_GENERATION)
    assert video_results == []

    unknown_results = registry.find_models_by_capability("unsupported_cap")
    assert unknown_results == []


def test_capability_registry_get_capabilities_for_model():
    registry = CapabilityRegistry()
    m = ModelDescriptor(
        model_id="gpt-4o",
        provider_id="openai",
        capabilities={ModelCapability.CODING, ModelCapability.VISION},
    )
    registry.register(m)

    assert registry.get_capabilities_for_model("gpt-4o") == frozenset({"coding", "vision"})

    with pytest.raises(KeyError):
        registry.get_capabilities_for_model("unknown")


def test_model_capability_registry_alias():
    assert ModelCapabilityRegistry is CapabilityRegistry
    reg = ModelCapabilityRegistry()
    assert isinstance(reg, CapabilityRegistry)
