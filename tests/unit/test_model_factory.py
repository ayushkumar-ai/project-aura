import pytest

from providers.factory import create_model_provider
from providers.fake_model import FakeModelProvider
from providers.openai_model import OpenAIProvider


def test_create_model_provider_defaults_to_fake():
    provider = create_model_provider("")

    assert isinstance(provider, FakeModelProvider)


def test_create_model_provider_accepts_fake():
    provider = create_model_provider("fake")

    assert isinstance(provider, FakeModelProvider)


def test_create_model_provider_rejects_unsupported_provider():
    with pytest.raises(
        ValueError,
        match="Unsupported model provider",
    ):
        create_model_provider("unknown")


def test_create_model_provider_accepts_openai(monkeypatch):
    class FakeOpenAIProvider:
        pass

    def fake_init(self, model_name, api_key, client=None):
        self.model_name = model_name
        self.api_key = api_key

    monkeypatch.setattr(
        OpenAIProvider,
        "__init__",
        fake_init,
    )

    provider = create_model_provider(
        "openai",
        model_name="test-model",
        api_key="test-key",
    )

    assert isinstance(provider, OpenAIProvider)
    assert provider.model_name == "test-model"
    assert provider.api_key == "test-key"
