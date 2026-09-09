from dataclasses import dataclass, field
from enum import Enum


class ModelCapability(str, Enum):
    """Standardized model capability vocabulary in AURA."""

    REASONING = "reasoning"
    CODING = "coding"
    VISION = "vision"
    AUDIO = "audio"
    TOOL_USE = "tool_use"
    LONG_CONTEXT = "long_context"
    IMAGE_GENERATION = "image_generation"
    VIDEO_GENERATION = "video_generation"


@dataclass(frozen=True)
class ModelDescriptor:
    """Represents a model and its declared capabilities."""

    model_id: str
    provider_id: str
    capabilities: frozenset[str] = field(default_factory=frozenset)
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ValueError("Model ID must be a non-empty string.")
        if not isinstance(self.provider_id, str) or not self.provider_id.strip():
            raise ValueError("Provider ID must be a non-empty string.")

        caps = set()
        for c in self.capabilities:
            if isinstance(c, ModelCapability):
                caps.add(c.value)
            elif isinstance(c, str) and c.strip():
                caps.add(c.strip().lower())
        object.__setattr__(self, "capabilities", frozenset(caps))

    def supports(self, capability: ModelCapability | str) -> bool:
        """Check if this model supports a given capability."""
        if isinstance(capability, ModelCapability):
            cap_str = capability.value
        elif isinstance(capability, str):
            cap_str = capability.strip().lower()
        else:
            return False
        return cap_str in self.capabilities


class CapabilityRegistry:
    """Registry for discovering models by capability across providers."""

    def __init__(self):
        self._models: dict[str, ModelDescriptor] = {}

    def register_model(
        self,
        descriptor: ModelDescriptor,
    ) -> None:
        """Register a model descriptor."""
        if not isinstance(descriptor, ModelDescriptor):
            raise TypeError("Descriptor must be an instance of ModelDescriptor.")

        key = descriptor.model_id.strip().lower()
        if key in self._models:
            raise ValueError(f"Model already registered: {descriptor.model_id}")

        self._models[key] = descriptor

    def register(
        self,
        descriptor: ModelDescriptor,
    ) -> None:
        """Alias for register_model."""
        self.register_model(descriptor)

    def get_model(self, model_id: str) -> ModelDescriptor:
        """Retrieve a model descriptor by ID."""
        if not isinstance(model_id, str) or not model_id.strip():
            raise KeyError(f"Unknown model: {model_id}")

        key = model_id.strip().lower()
        if key not in self._models:
            raise KeyError(f"Unknown model: {model_id}")

        return self._models[key]

    def has_model(self, model_id: str) -> bool:
        """Check if a model ID is registered."""
        if not isinstance(model_id, str) or not model_id.strip():
            return False
        return model_id.strip().lower() in self._models

    def list_models(self, provider_id: str | None = None) -> list[ModelDescriptor]:
        """List registered model descriptors, optionally filtered by provider."""
        if provider_id is None:
            return list(self._models.values())

        norm_provider = provider_id.strip().lower()
        return [
            m for m in self._models.values()
            if m.provider_id.strip().lower() == norm_provider
        ]

    def find_models_by_capability(
        self,
        capability: ModelCapability | str,
    ) -> list[ModelDescriptor]:
        """Find all models supporting the specified capability."""
        return [
            m for m in self._models.values()
            if m.supports(capability)
        ]

    def get_capabilities_for_model(self, model_id: str) -> frozenset[str]:
        """Get the capabilities of a registered model."""
        return self.get_model(model_id).capabilities


# Alias for explicit naming
ModelCapabilityRegistry = CapabilityRegistry
