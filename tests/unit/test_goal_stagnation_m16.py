"""Unit tests for GoalStagnationMonitor (M16)."""

import pytest
import tempfile
from pathlib import Path

from core.goal import Goal, GoalProgress, GoalStatus
from core.goal_stagnation import GoalProgressEvaluation, GoalStagnationMonitor
from core.strategy_lineage import StrategyLineageStore
from core.strategy_types import StagnationReport, StrategyAttemptOutcome, StrategyType


class TestGoalStagnationMonitor:
    @pytest.fixture
    def lineage_store(self):
        return StrategyLineageStore()

    @pytest.fixture
    def monitor(self, lineage_store):
        return GoalStagnationMonitor(
            max_stagnation_evaluations=5,
            min_progress_delta=0.01,
            lineage_store=lineage_store,
        )

    def test_progress_recording_and_history(self, monitor):
        g = Goal(goal_id="g_stag_1", title="Test Progress Goal")
        e1 = monitor.record_progress("g_stag_1", 0.10)
        e2 = monitor.record_progress("g_stag_1", 0.25)

        assert e1.evaluation_index == 1
        assert e2.evaluation_index == 2
        history = monitor.get_progress_history("g_stag_1")
        assert len(history) == 2
        assert history[0].progress_percentage == 0.10
        assert history[1].progress_percentage == 0.25

    def test_stagnation_detection_over_five_evaluations(self, monitor):
        g = Goal(goal_id="g_stag_2", title="Stagnant Goal")
        # 1 initial evaluation
        rep1 = monitor.check_stagnation(g, current_progress_percentage=0.20)
        assert rep1.is_stagnant is False

        # 3 more evaluations at 0.20 (total 4)
        for _ in range(3):
            rep = monitor.check_stagnation(g, current_progress_percentage=0.20)
            assert rep.is_stagnant is False

        # 5th evaluation at 0.20 -> reaches 5 consecutive stagnant evaluations
        rep5 = monitor.check_stagnation(g, current_progress_percentage=0.20)
        assert rep5.is_stagnant is True
        assert rep5.consecutive_stagnant_evaluations >= 5
        assert rep5.should_pivot_strategy is True
        assert rep5.should_abandon_goal is False
        assert rep5.recommended_strategy is not None

    def test_progress_resets_stagnation(self, monitor):
        g = Goal(goal_id="g_stag_3", title="Recovering Goal")
        # 4 stagnant evaluations
        for _ in range(4):
            monitor.check_stagnation(g, current_progress_percentage=0.10)

        # Progress made
        rep = monitor.check_stagnation(g, current_progress_percentage=0.40)
        assert rep.is_stagnant is False
        assert rep.consecutive_stagnant_evaluations == 0
        assert rep.progress_delta > 0.10

    def test_irrecoverable_stagnation_triggers_abandonment(self, monitor, lineage_store):
        g = Goal(goal_id="g_stag_4", title="Irrecoverable Goal")
        # Record failures across all strategy types in lineage store
        for st in StrategyType:
            lineage_store.record_attempt(
                goal_id="g_stag_4",
                strategy_type=st,
                outcome=StrategyAttemptOutcome.FAILURE,
            )

        # Run evaluations until stagnation threshold
        rep = None
        for _ in range(6):
            rep = monitor.check_stagnation(g, current_progress_percentage=0.0)

        assert rep.is_stagnant is True
        assert rep.should_abandon_goal is True

    def test_persistence_roundtrip(self, lineage_store):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "stagnation.json"
            mon1 = GoalStagnationMonitor(
                max_stagnation_evaluations=3,
                persistence_path=path,
                lineage_store=lineage_store,
            )
            g = Goal(goal_id="g_persist", title="Persisted Stagnation")
            mon1.check_stagnation(g, current_progress_percentage=0.15)
            mon1.check_stagnation(g, current_progress_percentage=0.15)

            mon2 = GoalStagnationMonitor(
                persistence_path=path,
                lineage_store=lineage_store,
            )
            history = mon2.get_progress_history("g_persist")
            assert len(history) == 2
            assert history[0].progress_percentage == 0.15
