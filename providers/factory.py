from interfaces.model import ModelInterface
from providers.fake_model import FakeModelProvider


def create_model_provider(
    provider: str,
    model_name: str = "",
    api_key: str = "",
) -> ModelInterface:
    """Create an AURA model provider from configuration."""

    provider_name = provider.strip().lower()

    if provider_name in {"", "fake"}:
        return FakeModelProvider()

    raise ValueError(
        f"Unsupported model provider: {provider}"
    )
