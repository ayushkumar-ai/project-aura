from collections.abc import Iterable
from dataclasses import dataclass, field

from core.capability_registry import (
    CapabilityRegistry,
    ModelCapability,
    ModelDescriptor,
)
from core.provider_registry import ProviderRegistry
from interfaces.model import ModelInterface


@dataclass(frozen=True)
class TaskRequirements:
    """Represents task requirements for model capability routing."""

    required_capabilities: frozenset[str] = field(default_factory=frozenset)
    preferred_model: str | None = None
    preferred_provider: str | None = None

    def __init__(
        self,
        required_capabilities: (
            Iterable[ModelCapability | str] | ModelCapability | str | None
        ) = None,
        preferred_model: str | None = None,
        preferred_provider: str | None = None,
    ):
        caps: set[str] = set()
        if required_capabilities is not None:
            if isinstance(required_capabilities, (ModelCapability, str)):
                items = [required_capabilities]
            elif isinstance(required_capabilities, Iterable):
                items = list(required_capabilities)
            else:
                raise TypeError(
                    "required_capabilities must be an iterable or single capability."
                )

            for c in items:
                if isinstance(c, ModelCapability):
                    caps.add(c.value)
                elif isinstance(c, str):
                    s = c.strip().lower()
                    if s:
                        caps.add(s)
                else:
                    raise TypeError(f"Invalid capability type: {type(c)}")

        object.__setattr__(self, "required_capabilities", frozenset(caps))

        if preferred_model is not None:
            if not isinstance(preferred_model, str) or not preferred_model.strip():
                raise ValueError("preferred_model must be a non-empty string or None.")
            object.__setattr__(self, "preferred_model", preferred_model.strip())
        else:
            object.__setattr__(self, "preferred_model", None)

        if preferred_provider is not None:
            if not isinstance(preferred_provider, str) or not preferred_provider.strip():
                raise ValueError("preferred_provider must be a non-empty string or None.")
            object.__setattr__(self, "preferred_provider", preferred_provider.strip())
        else:
            object.__setattr__(self, "preferred_provider", None)


@dataclass(frozen=True)
class ModelRouteResult:
    """Represents the outcome of a model routing operation."""

    model_descriptor: ModelDescriptor
    provider: ModelInterface

    @property
    def descriptor(self) -> ModelDescriptor:
        """Alias for model_descriptor."""
        return self.model_descriptor

    @property
    def model_id(self) -> str:
        """ID of the selected model."""
        return self.model_descriptor.model_id

    @property
    def provider_id(self) -> str:
        """ID of the selected model's provider."""
        return self.model_descriptor.provider_id


class ModelRouter:
    """Routes task requirements to compatible registered models and providers."""

    def __init__(
        self,
        capability_registry: CapabilityRegistry,
        provider_registry: ProviderRegistry,
    ):
        if not isinstance(capability_registry, CapabilityRegistry):
            raise TypeError("capability_registry must be an instance of CapabilityRegistry.")
        if not isinstance(provider_registry, ProviderRegistry):
            raise TypeError("provider_registry must be an instance of ProviderRegistry.")

        self.capability_registry = capability_registry
        self.provider_registry = provider_registry

    def route(self, requirements: TaskRequirements | None = None) -> ModelRouteResult:
        """Select a model and provider matching the task requirements."""
        if requirements is None:
            requirements = TaskRequirements()
        elif not isinstance(requirements, TaskRequirements):
            raise TypeError("requirements must be an instance of TaskRequirements or None.")

        # 1. Handle explicitly preferred model
        if requirements.preferred_model is not None:
            model_id = requirements.preferred_model
            if not self.capability_registry.has_model(model_id):
                raise KeyError(
                    f"Preferred model '{model_id}' is not registered in CapabilityRegistry."
                )

            descriptor = self.capability_registry.get_model(model_id)

            # Validate preferred_provider match if specified
            if requirements.preferred_provider is not None:
                if (
                    descriptor.provider_id.strip().lower()
                    != requirements.preferred_provider.strip().lower()
                ):
                    raise ValueError(
                        f"Preferred model '{model_id}' belongs to provider '{descriptor.provider_id}', "
                        f"which does not match preferred provider '{requirements.preferred_provider}'."
                    )

            # Validate required capabilities
            missing_caps = [
                cap
                for cap in requirements.required_capabilities
                if not descriptor.supports(cap)
            ]
            if missing_caps:
                raise ValueError(
                    f"Preferred model '{model_id}' does not support required capabilities: {sorted(missing_caps)}"
                )

            # Resolve provider
            if not self.provider_registry.has(descriptor.provider_id):
                raise KeyError(
                    f"Provider '{descriptor.provider_id}' for model '{model_id}' is not registered in ProviderRegistry."
                )

            provider = self.provider_registry.get(descriptor.provider_id)
            return ModelRouteResult(model_descriptor=descriptor, provider=provider)

        # 2. Check preferred_provider if specified
        if requirements.preferred_provider is not None:
            provider_id = requirements.preferred_provider
            if not self.provider_registry.has(provider_id):
                raise KeyError(
                    f"Preferred provider '{provider_id}' is not registered in ProviderRegistry."
                )
            candidates = self.capability_registry.list_models(provider_id=provider_id)
        else:
            candidates = self.capability_registry.list_models()

        # 3. Filter candidates by required capabilities
        compatible_candidates = [
            m
            for m in candidates
            if all(m.supports(cap) for cap in requirements.required_capabilities)
        ]

        # 4. Filter out any candidate whose provider is not in provider_registry
        valid_candidates = [
            m
            for m in compatible_candidates
            if self.provider_registry.has(m.provider_id)
        ]

        if not valid_candidates:
            if requirements.preferred_provider is not None:
                raise ValueError(
                    f"No compatible model found for preferred provider '{requirements.preferred_provider}' "
                    f"matching required capabilities: {sorted(requirements.required_capabilities)}"
                )
            raise ValueError(
                f"No compatible model found matching required capabilities: {sorted(requirements.required_capabilities)}"
            )

        # 5. Deterministic tie-breaking:
        # Sort lexicographically by (provider_id, model_id)
        selected_model = sorted(
            valid_candidates,
            key=lambda m: (m.provider_id.strip().lower(), m.model_id.strip().lower()),
        )[0]

        provider = self.provider_registry.get(selected_model.provider_id)
        return ModelRouteResult(model_descriptor=selected_model, provider=provider)
