import pytest

from core.provider_registry import ProviderRegistry
from interfaces.model import ModelInterface
from providers.fake_model import FakeModelProvider
from providers.openai_model import OpenAIProvider


def test_provider_registry_registers_and_retrieves_provider():
    registry = ProviderRegistry()
    provider = FakeModelProvider()

    registry.register("fake", provider)

    assert registry.get("fake") is provider


def test_provider_registry_has_and_contains():
    registry = ProviderRegistry()
    provider = FakeModelProvider()

    registry.register("fake", provider)

    assert registry.has("fake") is True
    assert "fake" in registry
    assert registry.has("unknown") is False
    assert "unknown" not in registry
    assert registry.has("") is False
    assert registry.has(None) is False


def test_provider_registry_lists_registered_providers():
    registry = ProviderRegistry()
    registry.register("fake1", FakeModelProvider())
    registry.register("fake2", FakeModelProvider())

    assert registry.list_providers() == ["fake1", "fake2"]


def test_provider_registry_rejects_duplicate_registration():
    registry = ProviderRegistry()
    registry.register("fake", FakeModelProvider())

    with pytest.raises(ValueError, match="Provider already registered"):
        registry.register("fake", FakeModelProvider())


def test_provider_registry_rejects_empty_or_non_string_id():
    registry = ProviderRegistry()

    with pytest.raises(ValueError, match="Provider ID must be a non-empty string"):
        registry.register("", FakeModelProvider())

    with pytest.raises(ValueError, match="Provider ID must be a non-empty string"):
        registry.register("   ", FakeModelProvider())

    with pytest.raises(ValueError, match="Provider ID must be a non-empty string"):
        registry.register(None, FakeModelProvider())


def test_provider_registry_rejects_non_model_interface():
    registry = ProviderRegistry()

    with pytest.raises(TypeError, match="Provider must implement ModelInterface"):
        registry.register("invalid", "not_a_model")


def test_provider_registry_raises_key_error_for_unknown():
    registry = ProviderRegistry()

    with pytest.raises(KeyError, match="Unknown provider"):
        registry.get("unknown")

    with pytest.raises(KeyError, match="Unknown provider"):
        registry.get("")


def test_provider_registry_accepts_openai_provider():
    registry = ProviderRegistry()
    provider = OpenAIProvider(
        model_name="gpt-4o",
        api_key="test-key",
    )

    registry.register("openai", provider)

    assert registry.get("openai") is provider
