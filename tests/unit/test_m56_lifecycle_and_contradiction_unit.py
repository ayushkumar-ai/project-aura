"""Unit tests for M56 Cognitive Memory Lifecycle, Decay, and Contradiction Resolution.

Validates invariants:
- M56-F04: Provenance Authority Ordering
- M56-F05: User Override Infallibility
- M56-F06: Atomic Supersession
- M56-F07: Vector Retrieval Exclusion
- M56-F09: Deterministic Temporal Decay
- M56-F10: Stale State Transition Threshold
"""

import time
import pytest
from core.cognitive_memory.contradiction import (
    ContradictionDetector,
    ContradictionResolver,
)
from core.cognitive_memory.lifecycle import (
    MemoryLifecycleConfig,
    MemoryLifecycleManager,
)
from core.cognitive_memory.types import (
    CognitiveMemory,
    CognitiveMemoryType,
    ContradictionStatus,
    LifecycleState,
    ProvenanceType,
    ResolutionStrategy,
)


class TestMemoryLifecycleAndDecay:
    """Test suite for lifecycle state machine and temporal decay calculations."""

    def test_explicit_user_memory_does_not_decay(self):
        """Invariant M56-F09: User explicit memories decay rate is zero (confidence constant)."""
        manager = MemoryLifecycleManager()
        base_time = 1000000.0
        mem = CognitiveMemory(
            memory_id="mem_user",
            tenant_id="tenant_alpha",
            confidence=1.0,
            provenance_type=ProvenanceType.USER_EXPLICIT,
            last_accessed_at=base_time,
        )
        # 1 year later (365 days)
        future_time = base_time + (365 * 86400.0)
        decayed = manager.calculate_decayed_confidence(mem, current_time=future_time)
        assert decayed == 1.0

        state = manager.evaluate_lifecycle_state(mem, current_time=future_time)
        assert state == LifecycleState.ACTIVE

    def test_inferred_memory_exponential_decay(self):
        """Invariant M56-F09 & M56-F10: Inferred memory decays and transitions to STALE."""
        cfg = MemoryLifecycleConfig(
            default_half_life_days=10.0,
            category_half_life_days={"semantic": 10.0},
            stale_confidence_threshold=0.3,
        )
        manager = MemoryLifecycleManager(config=cfg)

        base_time = 1000000.0
        mem = CognitiveMemory(
            memory_id="mem_inf",
            tenant_id="tenant_alpha",
            memory_type=CognitiveMemoryType.SEMANTIC,
            confidence=0.8,
            provenance_type=ProvenanceType.MODEL_INFERRED,
            last_accessed_at=base_time,
        )

        # After 1 half life (10 days): 0.8 * 0.5 = 0.40
        time_10d = base_time + (10 * 86400.0)
        decayed_10d = manager.calculate_decayed_confidence(mem, current_time=time_10d)
        assert abs(decayed_10d - 0.40) < 0.01
        assert manager.evaluate_lifecycle_state(mem, current_time=time_10d) == LifecycleState.ACTIVE

        # After 2 half lives (20 days): 0.8 * 0.25 = 0.20 -> below threshold 0.3 -> STALE
        time_20d = base_time + (20 * 86400.0)
        decayed_20d = manager.calculate_decayed_confidence(mem, current_time=time_20d)
        assert abs(decayed_20d - 0.20) < 0.01
        assert manager.evaluate_lifecycle_state(mem, current_time=time_20d) == LifecycleState.STALE

    def test_filter_active_memories_excludes_stale_and_superseded(self):
        """Invariant M56-F07: Superseded and stale memories excluded from active retrieval."""
        manager = MemoryLifecycleManager()
        now = time.time()

        active_mem = CognitiveMemory(
            memory_id="m_act",
            tenant_id="tenant_alpha",
            confidence=0.9,
            lifecycle_state=LifecycleState.ACTIVE,
            last_accessed_at=now,
        )
        superseded_mem = CognitiveMemory(
            memory_id="m_sup",
            tenant_id="tenant_alpha",
            confidence=0.9,
            lifecycle_state=LifecycleState.SUPERSEDED,
            last_accessed_at=now,
        )
        stale_mem = CognitiveMemory(
            memory_id="m_stale",
            tenant_id="tenant_alpha",
            confidence=0.1,  # Below threshold
            lifecycle_state=LifecycleState.STALE,
            last_accessed_at=now,
        )

        filtered = manager.filter_active_for_retrieval([active_mem, superseded_mem, stale_mem], current_time=now)
        assert len(filtered) == 1
        assert filtered[0].memory_id == "m_act"


class TestContradictionDetectionAndResolution:
    """Test suite for contradiction detection and provenance hierarchy resolution."""

    def test_key_collision_detection(self):
        detector = ContradictionDetector()
        existing = CognitiveMemory(
            memory_id="m1",
            tenant_id="tenant_alpha",
            key="pref:editor",
            content="vim",
            lifecycle_state=LifecycleState.ACTIVE,
        )
        candidate = CognitiveMemory(
            memory_id="m2",
            tenant_id="tenant_alpha",
            key="pref:editor",
            content="emacs",
            lifecycle_state=LifecycleState.ACTIVE,
        )

        conflicts = detector.detect_conflicts(candidate, [existing])
        assert len(conflicts) == 1
        assert conflicts[0][0].memory_id == "m1"

    def test_subject_predicate_collision_detection(self):
        detector = ContradictionDetector()
        existing = CognitiveMemory(
            memory_id="m1",
            tenant_id="tenant_alpha",
            metadata={"subject": "user", "predicate": "timezone", "object_value": "UTC"},
            lifecycle_state=LifecycleState.ACTIVE,
        )
        candidate = CognitiveMemory(
            memory_id="m2",
            tenant_id="tenant_alpha",
            metadata={"subject": "user", "predicate": "timezone", "object_value": "America/New_York"},
            lifecycle_state=LifecycleState.ACTIVE,
        )

        conflicts = detector.detect_conflicts(candidate, [existing])
        assert len(conflicts) == 1
        assert conflicts[0][0].memory_id == "m1"

    def test_user_override_authority(self):
        """Invariant M56-F05: Explicit user statement supersedes model-inferred fact."""
        resolver = ContradictionResolver()
        existing_inferred = CognitiveMemory(
            memory_id="m_inf",
            tenant_id="tenant_alpha",
            key="user_city",
            content="Seattle",
            confidence=0.7,
            provenance_type=ProvenanceType.MODEL_INFERRED,
            version=1,
        )
        candidate_user = CognitiveMemory(
            memory_id="m_usr",
            tenant_id="tenant_alpha",
            key="user_city",
            content="San Francisco",
            confidence=1.0,
            provenance_type=ProvenanceType.USER_EXPLICIT,
        )

        winner, loser, contradiction = resolver.resolve(existing_inferred, candidate_user)
        # Winner must be the candidate user statement
        assert winner.memory_id == "m_usr"
        assert winner.lifecycle_state == LifecycleState.ACTIVE
        assert winner.supersedes_id == "m_inf"
        assert winner.version == 2
        # Loser must be superseded
        assert loser.memory_id == "m_inf"
        assert loser.lifecycle_state == LifecycleState.SUPERSEDED
        assert contradiction.resolution_strategy == ResolutionStrategy.USER_OVERRIDE
        assert contradiction.resolution_status == ContradictionStatus.AUTO_RESOLVED

    def test_provenance_precedence_ordering(self):
        """Invariant M56-F04: tool_observed (rank 4) outranks model_inferred (rank 2)."""
        resolver = ContradictionResolver()
        existing_inferred = CognitiveMemory(
            memory_id="m_inf",
            tenant_id="tenant_alpha",
            key="os_version",
            content="Linux 5.15",
            provenance_type=ProvenanceType.MODEL_INFERRED,
            version=1,
        )
        candidate_tool = CognitiveMemory(
            memory_id="m_tool",
            tenant_id="tenant_alpha",
            key="os_version",
            content="Windows 11",
            provenance_type=ProvenanceType.TOOL_OBSERVED,
        )

        winner, loser, contradiction = resolver.resolve(existing_inferred, candidate_tool)
        assert winner.memory_id == "m_tool"
        assert winner.lifecycle_state == LifecycleState.ACTIVE
        assert loser.memory_id == "m_inf"
        assert loser.lifecycle_state == LifecycleState.SUPERSEDED
        assert contradiction.resolution_strategy == ResolutionStrategy.PROVENANCE_PRECEDENCE
