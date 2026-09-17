"""M56 — Abstract Cognitive Memory Repository Interface.

Defines repository contracts for multi-tier cognitive memory, contradictions,
user cognitive profiles, experience patterns, and feedback event persistence.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.cognitive_memory.types import (
    CognitiveMemory,
    CognitiveMemoryType,
    ContradictionStatus,
    ExperiencePattern,
    LifecycleState,
    MemoryContradiction,
    MemoryFeedbackEvent,
    ProvenanceType,
    ResolutionStrategy,
    UserCognitiveProfile,
)


class BaseCognitiveMemoryRepository(ABC):
    """Abstract repository contract for multi-tenant cognitive memory management."""

    @abstractmethod
    def record_memory(
        self,
        tenant_id: str,
        content: str,
        memory_type: CognitiveMemoryType | str = CognitiveMemoryType.SEMANTIC,
        category: str = "general",
        key: str = "",
        structured_data: dict[str, Any] | None = None,
        confidence: float = 1.0,
        provenance_type: ProvenanceType | str = ProvenanceType.SYSTEM_DERIVED,
        lifecycle_state: LifecycleState | str = LifecycleState.ACTIVE,
        tags: list[str] | tuple[str, ...] | None = None,
        source_urls: list[str] | tuple[str, ...] | None = None,
        metadata: dict[str, Any] | None = None,
        memory_id: str | None = None,
        auto_resolve_contradictions: bool = True,
    ) -> tuple[CognitiveMemory, MemoryContradiction | None]:
        """Persist a cognitive memory, detecting and resolving contradictions."""
        pass

    @abstractmethod
    def get_memory(self, memory_id: str, tenant_id: str) -> CognitiveMemory | None:
        """Fetch cognitive memory if owned by tenant_id."""
        pass

    @abstractmethod
    def query_memories(
        self,
        tenant_id: str,
        memory_type: CognitiveMemoryType | str | None = None,
        category: str | None = None,
        key: str | None = None,
        lifecycle_state: LifecycleState | str | None = LifecycleState.ACTIVE,
        min_confidence: float = 0.0,
        query: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> list[CognitiveMemory]:
        """Query cognitive memories owned by tenant_id with optional filters."""
        pass

    @abstractmethod
    def update_memory_state(
        self,
        memory_id: str,
        tenant_id: str,
        lifecycle_state: LifecycleState | str,
        confidence: float | None = None,
        reason: str = "",
    ) -> CognitiveMemory | None:
        """Update lifecycle state and confidence for a cognitive memory."""
        pass

    @abstractmethod
    def delete_memory(
        self,
        memory_id: str,
        tenant_id: str,
        hard_delete: bool = False,
    ) -> bool:
        """Soft delete (set lifecycle_state=deleted) or hard delete memory record."""
        pass

    @abstractmethod
    def clear_tenant_memories(self, tenant_id: str) -> int:
        """Hard purge all cognitive memories, profiles, patterns, and contradictions for tenant."""
        pass

    @abstractmethod
    def get_profile(self, tenant_id: str) -> UserCognitiveProfile | None:
        """Fetch user cognitive profile for tenant_id."""
        pass

    @abstractmethod
    def save_profile(self, profile: UserCognitiveProfile) -> UserCognitiveProfile:
        """Create or update user cognitive profile."""
        pass

    @abstractmethod
    def get_experience_pattern(self, tenant_id: str, context_key: str) -> ExperiencePattern | None:
        """Fetch aggregated experience pattern for tenant and context key."""
        pass

    @abstractmethod
    def save_experience_pattern(self, pattern: ExperiencePattern) -> ExperiencePattern:
        """Save or update an experience pattern."""
        pass

    @abstractmethod
    def list_experience_patterns(self, tenant_id: str, limit: int = 50) -> list[ExperiencePattern]:
        """List experience patterns for tenant."""
        pass

    @abstractmethod
    def record_feedback(self, feedback: MemoryFeedbackEvent) -> MemoryFeedbackEvent:
        """Persist a memory feedback event."""
        pass

    @abstractmethod
    def list_contradictions(
        self,
        tenant_id: str,
        status: ContradictionStatus | str | None = None,
        limit: int = 50,
    ) -> list[MemoryContradiction]:
        """List detected contradictions for tenant."""
        pass

    @abstractmethod
    def resolve_contradiction(
        self,
        contradiction_id: str,
        tenant_id: str,
        resolution_strategy: ResolutionStrategy | str,
        resolved_by: str,
        winning_memory_id: str,
    ) -> bool:
        """Manually resolve a contradiction."""
        pass
