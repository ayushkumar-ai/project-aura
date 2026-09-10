"""
Unit tests for Milestone 16 core/strategy_lineage.py.
Tests strategy attempt tracking, bounded history enforcement,
strategy exclusion logic, oscillation prevention, and persistence.
"""

import pytest
import time
import tempfile
from pathlib import Path
from core.strategy_types import (
    StrategyType,
    StrategyAttemptOutcome,
)
from core.strategy_lineage import StrategyLineageStore


class TestStrategyLineageStore:
    def test_record_attempt_and_retrieval(self):
        store = StrategyLineageStore(max_history_per_goal=5, max_strategy_retries=3)
        att1 = store.record_attempt(
            goal_id="goal_1",
            strategy_type=StrategyType.DIRECT_SKILL,
            outcome=StrategyAttemptOutcome.FAILURE,
            failure_category="timeout",
            execution_cost=0.5,
            rationale="Initial execution attempt",
        )
        assert att1.goal_id == "goal_1"
        assert att1.attempt_number == 1
        assert att1.strategy_type == StrategyType.DIRECT_SKILL
        assert att1.outcome == StrategyAttemptOutcome.FAILURE

        rec = store.get_record("goal_1")
        assert rec is not None
        assert rec.active_strategy == StrategyType.DIRECT_SKILL
        assert len(rec.attempts) == 1
        assert StrategyType.DIRECT_SKILL in rec.failed_strategy_types

    def test_bounded_history_enforcement(self):
        store = StrategyLineageStore(max_history_per_goal=3)
        for i in range(5):
            store.record_attempt(
                goal_id="goal_bounded",
                strategy_type=StrategyType.DIRECT_SKILL,
                outcome=StrategyAttemptOutcome.FAILURE,
                rationale=f"Attempt {i+1}",
            )

        rec = store.get_record("goal_bounded")
        assert len(rec.attempts) == 3
        # Should keep the last 3 attempts (numbers 3, 4, 5)
        assert [a.attempt_number for a in rec.attempts] == [3, 4, 5]

    def test_strategy_exclusion_and_oscillation_prevention(self):
        store = StrategyLineageStore(max_strategy_retries=3)
        # Attempt 1 fails
        store.record_attempt(
            goal_id="g_osc",
            strategy_type=StrategyType.DIRECT_SKILL,
            outcome=StrategyAttemptOutcome.FAILURE,
        )

        # Strategy is excluded without new observation evidence
        assert store.is_strategy_excluded("g_osc", StrategyType.DIRECT_SKILL, has_new_observation_evidence=False) is True
        # Strategy is permitted if new observation evidence exists
        assert store.is_strategy_excluded("g_osc", StrategyType.DIRECT_SKILL, has_new_observation_evidence=True) is False

        # Attempt 2 fails with evidence
        store.record_attempt(
            goal_id="g_osc",
            strategy_type=StrategyType.DIRECT_SKILL,
            outcome=StrategyAttemptOutcome.FAILURE,
        )
        # Attempt 3 fails with evidence
        store.record_attempt(
            goal_id="g_osc",
            strategy_type=StrategyType.DIRECT_SKILL,
            outcome=StrategyAttemptOutcome.FAILURE,
        )

        # Now reached max_strategy_retries (3), permanently excluded even with evidence
        assert store.is_strategy_excluded("g_osc", StrategyType.DIRECT_SKILL, has_new_observation_evidence=True) is True

    def test_consecutive_failures_and_pivots(self):
        store = StrategyLineageStore()
        store.record_attempt("g_piv", StrategyType.DIRECT_SKILL, StrategyAttemptOutcome.FAILURE)
        assert store.get_consecutive_failures("g_piv") == 1

        store.record_attempt("g_piv", StrategyType.RESEARCH_ASSISTED_SYNTHESIS, StrategyAttemptOutcome.FAILURE)
        assert store.get_consecutive_failures("g_piv") == 2
        rec = store.get_record("g_piv")
        assert rec.total_strategy_pivots == 1

        store.record_attempt("g_piv", StrategyType.DECOMPOSED_HIERARCHICAL, StrategyAttemptOutcome.SUCCESS)
        assert store.get_consecutive_failures("g_piv") == 0
        rec_after = store.get_record("g_piv")
        assert rec_after.total_strategy_pivots == 2
        assert StrategyType.DECOMPOSED_HIERARCHICAL in rec_after.successful_strategy_types

    def test_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "lineage_state.json"
            store1 = StrategyLineageStore(persistence_path=file_path)
            store1.record_attempt("g_persist", StrategyType.DIRECT_SKILL, StrategyAttemptOutcome.FAILURE)
            store1.record_attempt("g_persist", StrategyType.FALLBACK_TOOL_ROUTING, StrategyAttemptOutcome.SUCCESS)
            store1.save_to_file()

            assert file_path.exists()

            store2 = StrategyLineageStore(persistence_path=file_path)
            store2.load_from_file()
            rec = store2.get_record("g_persist")
            assert rec is not None
            assert len(rec.attempts) == 2
            assert rec.active_strategy == StrategyType.FALLBACK_TOOL_ROUTING
