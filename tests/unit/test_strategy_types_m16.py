"""
Unit tests for Milestone 16 core/strategy_types.py.
Tests strategy contracts, enums, dataclasses, serialization,
taint preservation, and security metadata isolation.
"""

import pytest
import time
from core.provenance import TaintedValue
from core.strategy_types import (
    StrategyType,
    StrategyAttemptOutcome,
    StrategyAttempt,
    GoalStrategyRecord,
    StagnationReport,
    strip_forbidden_metadata_keys,
    _canonical_value,
    _restore_value,
)


class TestStrategyEnums:
    def test_strategy_type_values(self):
        assert StrategyType.DIRECT_SKILL.value == "direct_skill"
        assert StrategyType.DECOMPOSED_HIERARCHICAL.value == "decomposed_hierarchical"
        assert StrategyType.RESEARCH_ASSISTED_SYNTHESIS.value == "research_assisted_synthesis"
        assert StrategyType.FALLBACK_TOOL_ROUTING.value == "fallback_tool_routing"
        assert StrategyType.HUMAN_INTERACTIVE_CLARIFICATION.value == "human_interactive_clarification"

    def test_strategy_attempt_outcome_values(self):
        assert StrategyAttemptOutcome.SUCCESS.value == "success"
        assert StrategyAttemptOutcome.FAILURE.value == "failure"
        assert StrategyAttemptOutcome.PARTIAL.value == "partial"
        assert StrategyAttemptOutcome.PAUSED.value == "paused"
        assert StrategyAttemptOutcome.STAGNANT.value == "stagnant"
        assert StrategyAttemptOutcome.ABANDONED.value == "abandoned"


class TestStrategyAttempt:
    def test_strategy_attempt_creation_and_dict(self):
        now = time.time()
        attempt = StrategyAttempt(
            strategy_id="strat_001",
            goal_id="goal_100",
            strategy_type=StrategyType.DIRECT_SKILL,
            attempt_number=1,
            plan_id="plan_abc",
            outcome=StrategyAttemptOutcome.FAILURE,
            failure_category="timeout",
            execution_cost=1.25,
            rationale="Executed direct skill but timed out",
            created_at=now,
            completed_at=now + 5.0,
            metadata={"source": "agent_loop"},
        )
        assert attempt.strategy_id == "strat_001"
        assert attempt.goal_id == "goal_100"
        assert attempt.strategy_type == StrategyType.DIRECT_SKILL
        assert attempt.outcome == StrategyAttemptOutcome.FAILURE
        assert attempt.failure_category == "timeout"
        assert attempt.execution_cost == 1.25

        d = attempt.to_dict()
        assert d["strategy_id"] == "strat_001"
        assert d["strategy_type"] == "direct_skill"
        assert d["outcome"] == "failure"

        restored = StrategyAttempt.from_dict(d)
        assert restored.strategy_id == "strat_001"
        assert restored.strategy_type == StrategyType.DIRECT_SKILL
        assert restored.outcome == StrategyAttemptOutcome.FAILURE
        assert restored.execution_cost == 1.25

    def test_strategy_attempt_validation(self):
        with pytest.raises(ValueError):
            StrategyAttempt(strategy_id="", goal_id="goal_1", strategy_type=StrategyType.DIRECT_SKILL)
        with pytest.raises(ValueError):
            StrategyAttempt(strategy_id="strat_1", goal_id="", strategy_type=StrategyType.DIRECT_SKILL)
        with pytest.raises(ValueError):
            StrategyAttempt(strategy_id="strat_1", goal_id="goal_1", strategy_type=StrategyType.DIRECT_SKILL, attempt_number=0)


class TestGoalStrategyRecord:
    def test_goal_strategy_record_roundtrip(self):
        attempt1 = StrategyAttempt(
            strategy_id="s1",
            goal_id="g1",
            strategy_type=StrategyType.DIRECT_SKILL,
            outcome=StrategyAttemptOutcome.FAILURE,
            failure_category="policy_denial",
        )
        attempt2 = StrategyAttempt(
            strategy_id="s2",
            goal_id="g1",
            strategy_type=StrategyType.DECOMPOSED_HIERARCHICAL,
            attempt_number=2,
            outcome=StrategyAttemptOutcome.SUCCESS,
        )
        rec = GoalStrategyRecord(
            goal_id="g1",
            active_strategy=StrategyType.DECOMPOSED_HIERARCHICAL,
            attempts=(attempt1, attempt2),
            failed_strategy_types=(StrategyType.DIRECT_SKILL,),
            successful_strategy_types=(StrategyType.DECOMPOSED_HIERARCHICAL,),
            total_strategy_pivots=1,
            metadata={"domain": "research"},
        )
        assert rec.goal_id == "g1"
        assert rec.active_strategy == StrategyType.DECOMPOSED_HIERARCHICAL
        assert len(rec.attempts) == 2
        assert len(rec.failed_strategy_types) == 1
        assert len(rec.successful_strategy_types) == 1

        d = rec.to_dict()
        assert d["goal_id"] == "g1"
        assert d["active_strategy"] == "decomposed_hierarchical"
        assert len(d["attempts"]) == 2

        restored = GoalStrategyRecord.from_dict(d)
        assert restored.goal_id == "g1"
        assert restored.active_strategy == StrategyType.DECOMPOSED_HIERARCHICAL
        assert len(restored.attempts) == 2
        assert restored.total_strategy_pivots == 1


class TestStagnationReport:
    def test_stagnation_report_roundtrip(self):
        report = StagnationReport(
            report_id="stag_001",
            goal_id="goal_200",
            consecutive_stagnant_evaluations=5,
            progress_delta=0.0,
            current_progress_percentage=0.40,
            is_stagnant=True,
            should_pivot_strategy=True,
            should_abandon_goal=False,
            diagnostic_summary="Progress unchanged for 5 ticks",
            recommended_strategy=StrategyType.FALLBACK_TOOL_ROUTING,
        )
        assert report.report_id == "stag_001"
        assert report.goal_id == "goal_200"
        assert report.consecutive_stagnant_evaluations == 5
        assert report.should_pivot_strategy is True
        assert report.recommended_strategy == StrategyType.FALLBACK_TOOL_ROUTING

        d = report.to_dict()
        assert d["report_id"] == "stag_001"
        assert d["recommended_strategy"] == "fallback_tool_routing"

        restored = StagnationReport.from_dict(d)
        assert restored.report_id == "stag_001"
        assert restored.recommended_strategy == StrategyType.FALLBACK_TOOL_ROUTING
        assert restored.consecutive_stagnant_evaluations == 5


class TestSecurityAndTaintInvariants:
    def test_strip_forbidden_metadata_keys(self):
        meta = {
            "approved": True,
            "approval_status": "authorized",
            "auto_approve": True,
            "permission": "admin",
            "authorized": True,
            "bypass_policy": True,
            "role_override": "root",
            "system_override": True,
            "safe_tag": "diagnostics",
            "confidence": 0.95,
        }
        cleaned = strip_forbidden_metadata_keys(meta)
        assert "approved" not in cleaned
        assert "approval_status" not in cleaned
        assert "auto_approve" not in cleaned
        assert "permission" not in cleaned
        assert "authorized" not in cleaned
        assert "bypass_policy" not in cleaned
        assert "role_override" not in cleaned
        assert "system_override" not in cleaned
        assert cleaned["safe_tag"] == "diagnostics"
        assert cleaned["confidence"] == 0.95

    def test_tainted_value_preservation(self):
        tainted = TaintedValue(
            raw_value="untrusted payload from external site",
            is_untrusted=True,
            source_urls=("https://external.org",),
        )
        canonical = _canonical_value(tainted)
        assert canonical["__tainted__"] is True
        assert canonical["raw_value"] == "untrusted payload from external site"
        assert canonical["is_untrusted"] is True

        restored = _restore_value(canonical)
        assert isinstance(restored, TaintedValue)
        assert restored.raw_value == "untrusted payload from external site"
        assert restored.is_untrusted is True
