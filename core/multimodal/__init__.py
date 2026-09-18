"""M57 — Multimodal Processing & Rich Interaction Package."""

from core.multimodal.types import (
    ArtifactLifecycleState,
    JobStatus,
    MediaFormat,
    MultimodalArtifact,
    MultimodalCapabilityUsage,
    MultimodalDerivation,
    MultimodalLimitsConfig,
    MultimodalMediaType,
    MultimodalProcessingJob,
    MultimodalProvenance,
    MultimodalResult,
    SecurityClassification,
)
from core.multimodal.storage import (
    IObjectStorageService,
    LocalStorageService,
    InMemoryStorageService,
)
from core.multimodal.validation import MultimodalValidator
from core.multimodal.capabilities import (
    MultimodalCapability,
    MultimodalCapabilityRegistry,
)
from core.multimodal.trust import (
    AuthorityRank,
    wrap_untrusted_multimodal_data,
    sanitize_and_check_injection,
    validate_tool_dispatch_authority,
)
from core.multimodal.processor import MultimodalProcessor
from core.multimodal.integration import (
    MultimodalMemoryBridge,
    MultimodalDeletionCascade,
)

__all__ = [
    "ArtifactLifecycleState",
    "AuthorityRank",
    "IObjectStorageService",
    "InMemoryStorageService",
    "JobStatus",
    "LocalStorageService",
    "MediaFormat",
    "MultimodalArtifact",
    "MultimodalCapability",
    "MultimodalCapabilityRegistry",
    "MultimodalCapabilityUsage",
    "MultimodalDeletionCascade",
    "MultimodalDerivation",
    "MultimodalLimitsConfig",
    "MultimodalMediaType",
    "MultimodalMemoryBridge",
    "MultimodalProcessingJob",
    "MultimodalProcessor",
    "MultimodalProvenance",
    "MultimodalResult",
    "MultimodalValidator",
    "SecurityClassification",
    "sanitize_and_check_injection",
    "validate_tool_dispatch_authority",
    "wrap_untrusted_multimodal_data",
]
