"""
Unit tests for Milestone 15 core/temporal_decay.py.
Tests mathematical decay curves, namespace half-life routing,
utility scoring, untrusted penalty, and lifecycle recommendations.
"""

import pytest
import math
import time
from core.lifecycle_types import (
    DecayConfig,
    DecayModel,
    LifecycleAction,
)
from core.memory_types import (
    MemoryEntry,
    MemoryTier,
    MemoryNamespace,
)
from core.temporal_decay import TemporalDecayEngine


class TestTemporalDecayMath:
    def test_exponential_decay_half_life(self):
        config = DecayConfig(
            decay_model=DecayModel.EXPONENTIAL,
            default_half_life_days=10.0,
            min_confidence_floor=0.01,
        )
        engine = TemporalDecayEngine(config=config)

        base_conf = 1.0
        # Exactly 10 days = 1 half-life -> conf should be approx 0.5
        ten_days_sec = 10.0 * 86400.0
        decayed = engine.decay_confidence(base_conf, ten_days_sec, namespace="general")
        assert math.isclose(decayed, 0.50, rel_tol=1e-3)

        # 20 days = 2 half-lives -> conf should be approx 0.25
        twenty_days_sec = 20.0 * 86400.0
        decayed_20 = engine.decay_confidence(base_conf, twenty_days_sec, namespace="general")
        assert math.isclose(decayed_20, 0.25, rel_tol=1e-3)

    def test_min_confidence_floor(self):
        config = DecayConfig(
            decay_model=DecayModel.EXPONENTIAL,
            default_half_life_days=1.0,
            min_confidence_floor=0.05,
        )
        engine = TemporalDecayEngine(config=config)
        # 100 days age -> exponential would be tiny, but floor is 0.05
        huge_age_sec = 100.0 * 86400.0
        decayed = engine.decay_confidence(1.0, huge_age_sec, namespace="general")
        assert decayed == 0.05

    def test_namespace_specific_half_lives(self):
        config = DecayConfig()
        engine = TemporalDecayEngine(config=config)

        # user_profile: 365 days half-life. After 30 days, should decay very little.
        thirty_days_sec = 30.0 * 86400.0
        conf_profile = engine.decay_confidence(1.0, thirty_days_sec, namespace="user_profile")
        assert conf_profile > 0.90

        # task_scratchpad: 1 day half-life. After 30 days, should hit floor (0.05).
        conf_scratchpad = engine.decay_confidence(1.0, thirty_days_sec, namespace="task_scratchpad")
        assert conf_scratchpad == 0.05

    def test_linear_decay_model(self):
        config = DecayConfig(
            decay_model=DecayModel.LINEAR,
            default_half_life_days=10.0,
            min_confidence_floor=0.01,
        )
        engine = TemporalDecayEngine(config=config)

        # Linear decay: factor = 1.0 - (age / (2 * half_life))
        # at 1 half life (10 days), factor = 1 - 0.5 = 0.5
        ten_days_sec = 10.0 * 86400.0
        decayed = engine.decay_confidence(1.0, ten_days_sec, namespace="general")
        assert math.isclose(decayed, 0.50, rel_tol=1e-3)

    def test_step_decay_model(self):
        config = DecayConfig(
            decay_model=DecayModel.STEP,
            default_half_life_days=10.0,
            min_confidence_floor=0.01,
        )
        engine = TemporalDecayEngine(config=config)

        # at 9 days (steps=0), factor = 1.0
        nine_days = 9.0 * 86400.0
        assert engine.decay_confidence(1.0, nine_days, namespace="general") == 1.0

        # at 10 days (steps=1), factor = 0.5
        ten_days = 10.0 * 86400.0
        assert engine.decay_confidence(1.0, ten_days, namespace="general") == 0.5

    def test_decay_disabled(self):
        config = DecayConfig(enabled=False)
        engine = TemporalDecayEngine(config=config)
        huge_age = 1000.0 * 86400.0
        assert engine.decay_confidence(0.95, huge_age, namespace="general") == 0.95


class TestMemoryUtilityEvaluation:
    def test_evaluate_entry_fresh_promoted(self):
        engine = TemporalDecayEngine()
        now = time.time()
        entry = MemoryEntry(
            key="user_name",
            value="Alice",
            tier=MemoryTier.SEMANTIC,
            namespace=MemoryNamespace.USER_PROFILE.value,
            confidence=0.98,
            created_at=now - 3600.0,
            updated_at=now - 60.0,
            metadata={"access_count": 8, "last_accessed_at": now - 60.0},
        )
        score = engine.evaluate_entry(entry, current_time=now)
        assert score.entry_id == entry.entry_id
        assert score.decayed_confidence > 0.95
        assert score.access_count == 8
        assert score.utility_score >= 0.85
        assert score.recommended_action == LifecycleAction.PROMOTE

    def test_evaluate_entry_expired(self):
        engine = TemporalDecayEngine()
        now = time.time()
        entry = MemoryEntry(
            key="temp_code",
            value="123456",
            tier=MemoryTier.WORKING,
            namespace=MemoryNamespace.TASK_SCRATCHPAD.value,
            confidence=1.0,
            created_at=now - 1000.0,
            updated_at=now - 1000.0,
            expires_at=now - 10.0,
        )
        score = engine.evaluate_entry(entry, current_time=now)
        assert score.recommended_action == LifecycleAction.EVICT

    def test_evaluate_entry_untrusted_penalty(self):
        engine = TemporalDecayEngine()
        now = time.time()
        entry_trusted = MemoryEntry(
            key="fact_trusted",
            value="python version 3.11",
            confidence=0.90,
            is_untrusted=False,
            created_at=now,
            updated_at=now,
        )
        entry_untrusted = MemoryEntry(
            key="fact_untrusted",
            value="untrusted web text",
            confidence=0.90,
            is_untrusted=True,
            created_at=now,
            updated_at=now,
        )
        score_trusted = engine.evaluate_entry(entry_trusted, current_time=now)
        score_untrusted = engine.evaluate_entry(entry_untrusted, current_time=now)

        assert score_trusted.utility_score > score_untrusted.utility_score
        assert math.isclose(score_trusted.utility_score - score_untrusted.utility_score, 0.20, rel_tol=1e-3)

    def test_evaluate_entries_batch(self):
        engine = TemporalDecayEngine()
        now = time.time()
        entries = [
            MemoryEntry(key=f"k_{i}", value=f"val_{i}", created_at=now, updated_at=now)
            for i in range(5)
        ]
        scores = engine.evaluate_entries(entries, current_time=now)
        assert len(scores) == 5
        assert all(s.recommended_action == LifecycleAction.RETAIN for s in scores)
