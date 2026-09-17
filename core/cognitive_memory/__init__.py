"""M56 — Advanced Cognitive Memory, Continuous Learning & Personalization Package.

Provides multi-tier cognitive memory, provenance hierarchy tracking, contradiction resolution,
dynamic contextual personalization, and continuous learning feedback loops.
"""

from core.cognitive_memory.types import (
    PROVENANCE_AUTHORITY,
    CognitiveMemory,
    CognitiveMemoryType,
    ContradictionStatus,
    ExperiencePattern,
    FeedbackType,
    LifecycleState,
    MemoryContradiction,
    MemoryFeedbackEvent,
    PersonalizationContext,
    ProvenanceType,
    ResolutionStrategy,
    ScoredMemoryResult,
    UserCognitiveProfile,
    scrub_sensitive_content,
)
from core.cognitive_memory.lifecycle import (
    MemoryLifecycleConfig,
    MemoryLifecycleManager,
)
from core.cognitive_memory.contradiction import (
    ContradictionDetector,
    ContradictionResolver,
)
from core.cognitive_memory.consolidation import (
    MemoryConsolidationEngine,
)
from core.cognitive_memory.personalization import (
    PersonalizationEngine,
)
from core.cognitive_memory.feedback import (
    FeedbackLearningLoop,
)

__all__ = [
    "CognitiveMemory",
    "CognitiveMemoryType",
    "ProvenanceType",
    "LifecycleState",
    "ContradictionStatus",
    "ResolutionStrategy",
    "FeedbackType",
    "PROVENANCE_AUTHORITY",
    "MemoryContradiction",
    "UserCognitiveProfile",
    "ExperiencePattern",
    "MemoryFeedbackEvent",
    "ScoredMemoryResult",
    "PersonalizationContext",
    "scrub_sensitive_content",
    "MemoryLifecycleConfig",
    "MemoryLifecycleManager",
    "ContradictionDetector",
    "ContradictionResolver",
    "MemoryConsolidationEngine",
    "PersonalizationEngine",
    "FeedbackLearningLoop",
]
