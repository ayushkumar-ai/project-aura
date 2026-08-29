from interfaces.model import ModelInterface
from providers.fake_model import FakeModelProvider
from providers.openai_model import OpenAIProvider


def create_model_provider(
    provider: str,
    model_name: str = "",
    api_key: str = "",
) -> ModelInterface:
    """Create an AURA model provider from configuration."""

    provider_name = provider.strip().lower()

    if provider_name in {"", "fake"}:
        return FakeModelProvider()


    if provider_name == "openai":
        return OpenAIProvider(
            model_name=model_name,
            api_key=api_key,
        )


    raise ValueError(
        f"Unsupported model provider: {provider}"
    )
