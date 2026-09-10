"""
Unit tests for Milestone 16 core/meta_policy.py.
Tests MetaPolicyEngine strategy selection, failure mode adaptation,
heuristic rule integration, and strategy exclusion enforcement.
"""

import pytest
import time
from core.goal import Goal
from core.heuristic_calibrator import HeuristicCalibrator
from core.lifecycle_types import RuleStatus
from core.meta_policy import MetaPolicyEngine, MetaPolicyDecision
from core.strategy_lineage import StrategyLineageStore
from core.strategy_types import (
    StrategyAttemptOutcome,
    StrategyType,
)


class TestMetaPolicyEngine:
    def test_initial_strategy_selection_simple_goal(self):
        engine = MetaPolicyEngine()
        goal = Goal(title="Calculate sum", description="Add two numbers together")
        decision = engine.select_strategy(goal)
        assert decision.selected_strategy == StrategyType.DIRECT_SKILL
        assert decision.confidence > 0.80

    def test_initial_strategy_selection_research_goal(self):
        engine = MetaPolicyEngine()
        goal = Goal(title="Research AI trends", description="Find and compare recent papers on transformers")
        decision = engine.select_strategy(goal)
        assert decision.selected_strategy == StrategyType.RESEARCH_ASSISTED_SYNTHESIS

    def test_initial_strategy_selection_complex_goal(self):
        engine = MetaPolicyEngine()
        goal = Goal(
            title="Deploy system",
            description="Multi-stage deployment",
            success_criteria=("lint", "test", "build", "deploy"),
        )
        decision = engine.select_strategy(goal)
        assert decision.selected_strategy == StrategyType.DECOMPOSED_HIERARCHICAL

    def test_failure_recovery_pivoting(self):
        lineage = StrategyLineageStore()
        engine = MetaPolicyEngine(lineage_store=lineage)
        goal = Goal(title="Process data", description="Run dataset transformation")

        # Record failure of DIRECT_SKILL due to timeout
        lineage.record_attempt(
            goal_id=goal.goal_id,
            strategy_type=StrategyType.DIRECT_SKILL,
            outcome=StrategyAttemptOutcome.FAILURE,
            failure_category="timeout",
        )

        decision = engine.select_strategy(goal, failure_category="timeout")
        # Should pivot away from DIRECT_SKILL to FALLBACK_TOOL_ROUTING or DECOMPOSED_HIERARCHICAL
        assert decision.selected_strategy != StrategyType.DIRECT_SKILL
        assert decision.selected_strategy in (StrategyType.FALLBACK_TOOL_ROUTING, StrategyType.DECOMPOSED_HIERARCHICAL)
        assert StrategyType.DIRECT_SKILL in decision.excluded_strategies

    def test_promoted_heuristic_influence(self):
        calibrator = HeuristicCalibrator(min_trials_for_promotion=1)
        # Register and promote a rule recommending RESEARCH_ASSISTED_SYNTHESIS for query tasks
        calibrator.register_rule(
            rule_id="rule_query_research",
            trigger_condition="query benchmark",
            metadata={"recommended_strategy": "research_assisted_synthesis"},
        )
        calibrator.record_outcome("rule_query_research", success=True)
        assert calibrator.get_record("rule_query_research").status == RuleStatus.PROMOTED

        engine = MetaPolicyEngine(calibrator=calibrator)
        goal = Goal(title="Run query benchmark", description="Execute queries and measure latency")

        decision = engine.select_strategy(goal)
        assert decision.selected_strategy == StrategyType.RESEARCH_ASSISTED_SYNTHESIS
        assert "rule_query_research" in decision.applicable_heuristics

    def test_deprecated_heuristic_is_ignored(self):
        calibrator = HeuristicCalibrator(min_trials_for_promotion=1, deprecation_failure_rate=0.50)
        # Register and deprecate a bad rule recommending FALLBACK_TOOL_ROUTING
        calibrator.register_rule(
            rule_id="rule_bad_fallback",
            trigger_condition="simple echo",
            metadata={"recommended_strategy": "fallback_tool_routing"},
        )
        calibrator.record_outcome("rule_bad_fallback", success=False)
        assert calibrator.get_record("rule_bad_fallback").status == RuleStatus.DEPRECATED

        engine = MetaPolicyEngine(calibrator=calibrator)
        goal = Goal(title="simple echo task", description="Echo message")

        decision = engine.select_strategy(goal)
        # Should not use the deprecated heuristic
        assert "rule_bad_fallback" not in decision.applicable_heuristics
        assert decision.selected_strategy == StrategyType.DIRECT_SKILL

    def test_meta_policy_decision_roundtrip(self):
        decision = MetaPolicyDecision(
            selected_strategy=StrategyType.HUMAN_INTERACTIVE_CLARIFICATION,
            rationale="Policy denial encountered, requesting user guidance",
            confidence=0.90,
            excluded_strategies=(StrategyType.DIRECT_SKILL, StrategyType.FALLBACK_TOOL_ROUTING),
            applicable_heuristics=("rule_1", "rule_2"),
            metadata={"source": "test"},
        )
        d = decision.to_dict()
        assert d["selected_strategy"] == "human_interactive_clarification"
        assert len(d["excluded_strategies"]) == 2

        restored = MetaPolicyDecision.from_dict(d)
        assert restored.selected_strategy == StrategyType.HUMAN_INTERACTIVE_CLARIFICATION
        assert len(restored.excluded_strategies) == 2
        assert restored.confidence == 0.90
