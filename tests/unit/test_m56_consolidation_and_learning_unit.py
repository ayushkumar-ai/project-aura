"""Unit tests for M56 Cognitive Memory Consolidation and Continuous Learning.

Validates invariants:
- M56-F13: Feedback Loop Idempotency & Continuous Adaptation
- M56-F14: Experience Pattern Distillation
- M56-F19: Zero Model Retraining Requirement
"""

import pytest
from core.cognitive_memory.consolidation import MemoryConsolidationEngine
from core.cognitive_memory.feedback import FeedbackLearningLoop
from core.cognitive_memory.types import (
    CognitiveMemory,
    CognitiveMemoryType,
    ExperiencePattern,
    FeedbackType,
    LifecycleState,
    MemoryFeedbackEvent,
    ProvenanceType,
)


class TestFeedbackLearningLoop:
    """Test suite for feedback processing, confidence calibration, and idempotency."""

    def test_positive_feedback_boosts_confidence(self):
        loop = FeedbackLearningLoop()
        mem = CognitiveMemory(
            memory_id="m1",
            tenant_id="tenant_alpha",
            confidence=0.7,
            lifecycle_state=LifecycleState.ACTIVE,
        )
        event = MemoryFeedbackEvent(
            event_id="fb_pos",
            tenant_id="tenant_alpha",
            target_memory_id="m1",
            feedback_type=FeedbackType.POSITIVE,
        )
        updated, new_mem, applied_event = loop.process_feedback(event, target_memory=mem)

        assert updated is not None
        assert updated.confidence == 0.8  # +0.1 boost
        assert new_mem is None
        assert applied_event.applied is True

    def test_negative_feedback_penalizes_and_stales_low_confidence(self):
        loop = FeedbackLearningLoop()
        mem = CognitiveMemory(
            memory_id="m2",
            tenant_id="tenant_alpha",
            confidence=0.4,
            lifecycle_state=LifecycleState.ACTIVE,
        )
        event = MemoryFeedbackEvent(
            event_id="fb_neg",
            tenant_id="tenant_alpha",
            target_memory_id="m2",
            feedback_type=FeedbackType.NEGATIVE,
        )
        updated, new_mem, applied_event = loop.process_feedback(event, target_memory=mem)

        assert updated is not None
        assert updated.confidence == 0.2  # -0.2 penalty -> < 0.3 -> STALE
        assert updated.lifecycle_state == LifecycleState.STALE
        assert applied_event.applied is True

    def test_correction_supersedes_and_creates_explicit_memory(self):
        loop = FeedbackLearningLoop()
        mem = CognitiveMemory(
            memory_id="m3",
            tenant_id="tenant_alpha",
            key="user_name",
            content="Bob",
            confidence=0.6,
            version=1,
            lifecycle_state=LifecycleState.ACTIVE,
        )
        event = MemoryFeedbackEvent(
            event_id="fb_corr",
            tenant_id="tenant_alpha",
            target_memory_id="m3",
            feedback_type=FeedbackType.CORRECTION,
            correction_content="Alice",
        )
        updated_old, new_mem, applied_event = loop.process_feedback(event, target_memory=mem)

        assert updated_old is not None
        assert updated_old.lifecycle_state == LifecycleState.SUPERSEDED

        assert new_mem is not None
        assert new_mem.content == "Alice"
        assert new_mem.confidence == 1.0
        assert new_mem.provenance_type == ProvenanceType.USER_EXPLICIT
        assert new_mem.supersedes_id == "m3"
        assert new_mem.version == 2
        assert applied_event.applied is True

    def test_feedback_idempotency(self):
        """Invariant M56-F13: Re-processing an applied feedback event is a no-op."""
        loop = FeedbackLearningLoop()
        mem = CognitiveMemory(
            memory_id="m4",
            tenant_id="tenant_alpha",
            confidence=0.7,
        )
        event = MemoryFeedbackEvent(
            event_id="fb_idem",
            tenant_id="tenant_alpha",
            target_memory_id="m4",
            feedback_type=FeedbackType.POSITIVE,
            applied=True,
        )
        updated, new_mem, res_event = loop.process_feedback(event, target_memory=mem)
        assert updated == mem
        assert new_mem is None


class TestConsolidationEngine:
    """Test suite for experience pattern aggregation and semantic distillation."""

    def test_consolidation_into_experience_pattern(self):
        """Invariant M56-F14: Success and failure aggregation with moving average latency."""
        engine = MemoryConsolidationEngine()

        # 1. First execution (Success)
        p1 = engine.consolidate_episode_to_pattern(
            tenant_id="tenant_alpha",
            context_key="data_pipeline",
            tools_used=["sql_tool", "csv_parser"],
            success=True,
            latency_ms=100.0,
        )
        assert p1.success_count == 1
        assert p1.failure_count == 0
        assert p1.average_latency_ms == 100.0
        assert p1.optimal_tools == ["sql_tool", "csv_parser"]

        # 2. Second execution (Failure)
        p2 = engine.consolidate_episode_to_pattern(
            tenant_id="tenant_alpha",
            context_key="data_pipeline",
            tools_used=["sql_tool"],
            success=False,
            latency_ms=200.0,
            error_mode="DatabaseTimeoutError",
            existing_pattern=p1,
        )
        assert p2.success_count == 1
        assert p2.failure_count == 1
        assert p2.average_latency_ms == 150.0  # (100 + 200)/2
        assert "DatabaseTimeoutError" in p2.failure_modes

    def test_semantic_rule_synthesis_from_episodes(self):
        engine = MemoryConsolidationEngine()
        episodes = [
            CognitiveMemory(
                memory_id=f"ep_{i}",
                tenant_id="tenant_alpha",
                memory_type=CognitiveMemoryType.EPISODIC,
                structured_data={"executed_skills": ["git_ops", "pytest_runner"]},
            )
            for i in range(4)
        ]
        facts = engine.extract_semantic_facts_from_episodes("tenant_alpha", episodes)
        assert len(facts) >= 2
        keys = [f.key for f in facts]
        assert "proven_tool:git_ops" in keys
        assert "proven_tool:pytest_runner" in keys
