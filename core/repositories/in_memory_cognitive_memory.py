"""M56 — In-Memory Cognitive Memory Repository Implementation.

Provides high-performance, thread-safe in-memory cognitive memory, contradiction
detection, profile management, and continuous learning pattern persistence for testing.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any
from uuid import uuid4

from core.cognitive_memory.contradiction import (
    ContradictionDetector,
    ContradictionResolver,
)
from core.cognitive_memory.lifecycle import MemoryLifecycleManager
from core.cognitive_memory.types import (
    CognitiveMemory,
    CognitiveMemoryType,
    ContradictionStatus,
    ExperiencePattern,
    FeedbackType,
    LifecycleState,
    MemoryContradiction,
    MemoryFeedbackEvent,
    ProvenanceType,
    ResolutionStrategy,
    UserCognitiveProfile,
)
from core.repositories.base_cognitive_memory import BaseCognitiveMemoryRepository

logger = logging.getLogger("aura.repositories.in_memory_cognitive_memory")


class InMemoryCognitiveMemoryRepository(BaseCognitiveMemoryRepository):
    """In-memory implementation of BaseCognitiveMemoryRepository with thread-safety."""

    def __init__(
        self,
        lifecycle_manager: MemoryLifecycleManager | None = None,
        contradiction_detector: ContradictionDetector | None = None,
        contradiction_resolver: ContradictionResolver | None = None,
    ):
        self._lock = threading.RLock()
        self.lifecycle_manager = lifecycle_manager or MemoryLifecycleManager()
        self.detector = contradiction_detector or ContradictionDetector()
        self.resolver = contradiction_resolver or ContradictionResolver()

        # Data stores partitioned strictly by tenant_id
        self._memories: dict[str, dict[str, CognitiveMemory]] = {}
        self._contradictions: dict[str, dict[str, MemoryContradiction]] = {}
        self._profiles: dict[str, UserCognitiveProfile] = {}
        self._patterns: dict[str, dict[str, ExperiencePattern]] = {}
        self._feedback: dict[str, dict[str, MemoryFeedbackEvent]] = {}

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
        with self._lock:
            tenant_mems = self._memories.setdefault(tenant_id, {})
            tenant_contras = self._contradictions.setdefault(tenant_id, {})

            candidate = CognitiveMemory(
                memory_id=memory_id or str(uuid4()),
                tenant_id=tenant_id,
                memory_type=memory_type if isinstance(memory_type, CognitiveMemoryType) else CognitiveMemoryType(memory_type),
                category=category,
                key=key,
                content=content,
                structured_data=structured_data or {},
                confidence=confidence,
                provenance_type=provenance_type if isinstance(provenance_type, ProvenanceType) else ProvenanceType(provenance_type),
                lifecycle_state=lifecycle_state if isinstance(lifecycle_state, LifecycleState) else LifecycleState(lifecycle_state),
                tags=tuple(tags) if tags else (),
                source_urls=tuple(source_urls) if source_urls else (),
                metadata=metadata or {},
            )

            # Contradiction detection
            contradiction_record: MemoryContradiction | None = None
            if auto_resolve_contradictions and candidate.lifecycle_state == LifecycleState.ACTIVE:
                existing_active = list(tenant_mems.values())
                conflicts = self.detector.detect_conflicts(candidate, existing_active)
                if conflicts:
                    existing_clash, reason = conflicts[0]
                    retained, superseded, contra = self.resolver.resolve(existing_clash, candidate)
                    
                    # Update states in storage
                    tenant_mems[retained.memory_id] = retained
                    tenant_mems[superseded.memory_id] = superseded
                    tenant_contras[contra.contradiction_id] = contra
                    return retained, contra

            tenant_mems[candidate.memory_id] = candidate
            return candidate, contradiction_record

    def get_memory(self, memory_id: str, tenant_id: str) -> CognitiveMemory | None:
        with self._lock:
            tenant_mems = self._memories.get(tenant_id, {})
            mem = tenant_mems.get(memory_id)
            if mem is not None:
                # Update access metrics (Invariant M56-F23)
                d = mem.to_dict()
                d["access_count"] = mem.access_count + 1
                d["last_accessed_at"] = time.time()
                updated = CognitiveMemory.from_dict(d)
                tenant_mems[memory_id] = updated
                return updated
            return None

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
        with self._lock:
            tenant_mems = self._memories.get(tenant_id, {})
            target_type = memory_type.value if isinstance(memory_type, CognitiveMemoryType) else memory_type
            target_state = lifecycle_state.value if isinstance(lifecycle_state, LifecycleState) else lifecycle_state

            results: list[CognitiveMemory] = []
            for mem in tenant_mems.values():
                if target_type and mem.memory_type.value != target_type:
                    continue
                if category and mem.category.lower() != category.lower():
                    continue
                if key and mem.key.lower() != key.lower():
                    continue
                if target_state and mem.lifecycle_state.value != target_state:
                    continue
                if mem.confidence < min_confidence:
                    continue
                if query and query.strip():
                    q_lower = query.lower()
                    if q_lower not in mem.content.lower() and q_lower not in mem.key.lower():
                        continue
                results.append(mem)

            results.sort(key=lambda m: m.created_at, reverse=True)
            return results[offset : offset + limit]

    def update_memory_state(
        self,
        memory_id: str,
        tenant_id: str,
        lifecycle_state: LifecycleState | str,
        confidence: float | None = None,
        reason: str = "",
    ) -> CognitiveMemory | None:
        with self._lock:
            tenant_mems = self._memories.get(tenant_id, {})
            mem = tenant_mems.get(memory_id)
            if not mem:
                return None
            target_st = lifecycle_state if isinstance(lifecycle_state, LifecycleState) else LifecycleState(lifecycle_state)
            updated = self.lifecycle_manager.transition_state(mem, target_st, reason=reason)
            if confidence is not None:
                d = updated.to_dict()
                d["confidence"] = max(0.0, min(1.0, float(confidence)))
                updated = CognitiveMemory.from_dict(d)
            tenant_mems[memory_id] = updated
            return updated

    def delete_memory(
        self,
        memory_id: str,
        tenant_id: str,
        hard_delete: bool = False,
    ) -> bool:
        with self._lock:
            tenant_mems = self._memories.get(tenant_id, {})
            if memory_id not in tenant_mems:
                return False
            if hard_delete:
                del tenant_mems[memory_id]
            else:
                mem = tenant_mems[memory_id]
                tenant_mems[memory_id] = self.lifecycle_manager.transition_state(
                    mem, LifecycleState.DELETED, reason="soft_delete"
                )
            return True

    def clear_tenant_memories(self, tenant_id: str) -> int:
        with self._lock:
            count = len(self._memories.get(tenant_id, {}))
            self._memories.pop(tenant_id, None)
            self._contradictions.pop(tenant_id, None)
            self._profiles.pop(tenant_id, None)
            self._patterns.pop(tenant_id, None)
            self._feedback.pop(tenant_id, None)
            return count

    def get_profile(self, tenant_id: str) -> UserCognitiveProfile | None:
        with self._lock:
            return self._profiles.get(tenant_id)

    def save_profile(self, profile: UserCognitiveProfile) -> UserCognitiveProfile:
        with self._lock:
            existing = self._profiles.get(profile.tenant_id)
            new_version = (existing.version + 1) if existing else profile.version
            d = profile.to_dict()
            d["version"] = new_version
            d["updated_at"] = time.time()
            saved = UserCognitiveProfile.from_dict(d)
            self._profiles[profile.tenant_id] = saved
            return saved

    def get_experience_pattern(self, tenant_id: str, context_key: str) -> ExperiencePattern | None:
        with self._lock:
            tenant_pats = self._patterns.get(tenant_id, {})
            return tenant_pats.get(context_key.strip().lower())

    def save_experience_pattern(self, pattern: ExperiencePattern) -> ExperiencePattern:
        with self._lock:
            tenant_pats = self._patterns.setdefault(pattern.tenant_id, {})
            clean_key = pattern.context_key.strip().lower()
            d = pattern.to_dict()
            d["context_key"] = clean_key
            d["updated_at"] = time.time()
            saved = ExperiencePattern.from_dict(d)
            tenant_pats[clean_key] = saved
            return saved

    def list_experience_patterns(self, tenant_id: str, limit: int = 50) -> list[ExperiencePattern]:
        with self._lock:
            tenant_pats = self._patterns.get(tenant_id, {})
            pats = list(tenant_pats.values())
            pats.sort(key=lambda p: p.updated_at, reverse=True)
            return pats[:limit]

    def record_feedback(self, feedback: MemoryFeedbackEvent) -> MemoryFeedbackEvent:
        with self._lock:
            tenant_fb = self._feedback.setdefault(feedback.tenant_id, {})
            tenant_fb[feedback.event_id] = feedback
            return feedback

    def list_contradictions(
        self,
        tenant_id: str,
        status: ContradictionStatus | str | None = None,
        limit: int = 50,
    ) -> list[MemoryContradiction]:
        with self._lock:
            tenant_contras = self._contradictions.get(tenant_id, {})
            st_val = status.value if isinstance(status, ContradictionStatus) else status
            results = [
                c for c in tenant_contras.values()
                if not st_val or c.resolution_status.value == st_val
            ]
            results.sort(key=lambda c: c.detected_at, reverse=True)
            return results[:limit]

    def resolve_contradiction(
        self,
        contradiction_id: str,
        tenant_id: str,
        resolution_strategy: ResolutionStrategy | str,
        resolved_by: str,
        winning_memory_id: str,
    ) -> bool:
        with self._lock:
            tenant_contras = self._contradictions.get(tenant_id, {})
            contra = tenant_contras.get(contradiction_id)
            if not contra:
                return False

            strat = resolution_strategy if isinstance(resolution_strategy, ResolutionStrategy) else ResolutionStrategy(resolution_strategy)
            now = time.time()

            # Update contradiction record
            d_contra = contra.to_dict()
            d_contra["resolution_status"] = ContradictionStatus.RESOLVED.value
            d_contra["resolution_strategy"] = strat.value
            d_contra["resolved_by"] = resolved_by
            d_contra["resolved_at"] = now
            d_contra["resolution_details"] = {"manual_winner": winning_memory_id, "resolved_by": resolved_by}
            tenant_contras[contradiction_id] = MemoryContradiction.from_dict(d_contra)

            # Adjust memories
            tenant_mems = self._memories.get(tenant_id, {})
            loser_id = contra.memory_b_id if winning_memory_id == contra.memory_a_id else contra.memory_a_id
            if winning_memory_id in tenant_mems:
                tenant_mems[winning_memory_id] = self.lifecycle_manager.transition_state(
                    tenant_mems[winning_memory_id], LifecycleState.ACTIVE, reason="manual_resolution_winner"
                )
            if loser_id in tenant_mems:
                tenant_mems[loser_id] = self.lifecycle_manager.transition_state(
                    tenant_mems[loser_id], LifecycleState.SUPERSEDED, reason="manual_resolution_loser"
                )
            return True
