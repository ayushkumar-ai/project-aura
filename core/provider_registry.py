from interfaces.model import ModelInterface


class ProviderRegistry:
    """Registry for model providers in AURA."""

    def __init__(self):
        self._providers: dict[str, ModelInterface] = {}

    def register(self, provider_id: str, provider: ModelInterface) -> None:
        """Register a model provider under a unique ID."""
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError("Provider ID must be a non-empty string.")

        if not isinstance(provider, ModelInterface):
            raise TypeError("Provider must implement ModelInterface.")

        norm_id = provider_id.strip().lower()
        if norm_id in self._providers:
            raise ValueError(f"Provider already registered: {provider_id}")

        self._providers[norm_id] = provider

    def get(self, provider_id: str) -> ModelInterface:
        """Retrieve a registered provider by ID."""
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise KeyError(f"Unknown provider: {provider_id}")

        norm_id = provider_id.strip().lower()
        if norm_id not in self._providers:
            raise KeyError(f"Unknown provider: {provider_id}")

        return self._providers[norm_id]

    def has(self, provider_id: str) -> bool:
        """Check if a provider ID is registered."""
        if not isinstance(provider_id, str) or not provider_id.strip():
            return False
        return provider_id.strip().lower() in self._providers

    def __contains__(self, provider_id: str) -> bool:
        return self.has(provider_id)

    def list_providers(self) -> list[str]:
        """Return the IDs of all registered providers."""
        return list(self._providers.keys())
