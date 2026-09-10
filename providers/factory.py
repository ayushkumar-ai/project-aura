from interfaces.model import ModelInterface
from providers.fake_model import FakeModelProvider
from providers.generic_provider import GenericOpenAICompatibleProvider
from providers.openai_model import OpenAIProvider


def create_model_provider(
    provider: str,
    model_name: str = "",
    api_key: str = "",
    timeout: float | None = None,
    base_url: str = "",
    custom_headers: dict[str, str] | None = None,
    allow_local_endpoints: bool = True,
) -> ModelInterface:
    """Create an AURA model provider from configuration."""

    provider_name = provider.strip().lower()

    if provider_name in {"", "fake"}:
        return FakeModelProvider()

    if provider_name == "openai":
        if not model_name.strip():
            raise ValueError("OpenAI model name cannot be empty.")

        if not api_key.strip():
            raise ValueError("OpenAI API key cannot be empty.")

        if timeout is not None:
            return OpenAIProvider(
                model_name=model_name,
                api_key=api_key,
                timeout=timeout,
            )

        return OpenAIProvider(
            model_name=model_name,
            api_key=api_key,
        )

    if provider_name in {"generic", "openai_compatible", "local", "ollama", "vllm", "lmstudio"}:
        if not base_url.strip():
            raise ValueError(f"Base URL cannot be empty for provider '{provider}'.")
        if not model_name.strip():
            raise ValueError(f"Model name cannot be empty for provider '{provider}'.")

        return GenericOpenAICompatibleProvider(
            base_url=base_url,
            model_name=model_name,
            api_key=api_key,
            custom_headers=custom_headers,
            timeout=timeout,
            allow_local_endpoints=allow_local_endpoints,
        )

    raise ValueError(
        f"Unsupported model provider: {provider}"
    )
