import pytest

from providers.factory import create_model_provider
from providers.fake_model import FakeModelProvider


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
