"""Unit tests for GoalAdapter (M16)."""

import pytest
from core.agent_plan import AgentPlan, AgentPlanStep, StepStatus
from core.goal import Goal, GoalPriority, GoalStatus
from core.goal_adapter import GoalAdapter, GoalAdaptationResult
from core.heuristic_calibrator import HeuristicCalibrator
from core.meta_policy import MetaPolicyEngine
from core.reflection_types import FailureIssueType, ReflectionAssessment
from core.strategy_lineage import StrategyLineageStore
from core.strategy_types import StrategyAttemptOutcome, StrategyType


class TestGoalAdapter:
    @pytest.fixture
    def lineage_store(self):
        return StrategyLineageStore()

    @pytest.fixture
    def calibrator(self):
        cal = HeuristicCalibrator()
        # Promote a rule
        cal.register_rule("rule_opt_1", "optimize payload", base_confidence=0.9)
        cal.record_outcome("rule_opt_1", success=True)
        cal.record_outcome("rule_opt_1", success=True)
        cal.record_outcome("rule_opt_1", success=True)
        return cal

    @pytest.fixture
    def meta_policy(self, lineage_store, calibrator):
        return MetaPolicyEngine(lineage_store=lineage_store, calibrator=calibrator)

    @pytest.fixture
    def adapter(self, meta_policy, lineage_store, calibrator):
        return GoalAdapter(
            meta_policy=meta_policy,
            lineage_store=lineage_store,
            heuristic_calibrator=calibrator,
        )

    def test_adapt_goal_plan_initial_recovery(self, adapter):
        goal = Goal(
            goal_id="goal_adapt_1",
            title="Update database schema",
            description="Run database migration scripts",
        )
        failed_step = AgentPlanStep(
            step_id="s1",
            skill_name="db_migrate",
            status=StepStatus.FAILED,
            metadata={"error": "Schema locked by active session"},
        )
        failed_plan = AgentPlan(
            plan_id="plan_fail_1",
            task_goal="Run migration",
            steps=(failed_step,),
        )

        res = adapter.adapt_goal_plan(
            goal=goal,
            failed_plan=failed_plan,
            error_message="Schema locked by active session",
        )

        assert isinstance(res, GoalAdaptationResult)
        assert res.success is True
        assert res.adapted_plan is not None
        assert res.attempt_number == 1
        assert len(res.adapted_plan.steps) > 0
        assert res.selected_strategy in list(StrategyType)
        assert res.goal_id == "goal_adapt_1"
        assert "rule_opt_1" in "".join(res.heuristics_applied)

    def test_adapt_goal_plan_pivots_on_repeated_failure(self, adapter):
        goal = Goal(
            goal_id="goal_adapt_2",
            title="Complex multi-system deploy",
            description="Deploy services to production",
        )
        # Attempt 1 failed with DIRECT_SKILL
        res1 = adapter.adapt_goal_plan(
            goal=goal,
            error_message="Direct execution failed",
        )
        adapter.record_adaptation_outcome(
            goal_id=goal.goal_id,
            plan_id=res1.adapted_plan.plan_id,
            success=False,
            failure_category="tool_execution_failure",
        )

        # Attempt 2 should pivot strategy
        res2 = adapter.adapt_goal_plan(
            goal=goal,
            failed_plan=res1.adapted_plan,
            error_message="Tool execution failed again",
        )

        assert res2.success is True
        assert res2.attempt_number == 2
        assert res2.selected_strategy != res1.selected_strategy

    def test_adapt_goal_plan_respects_max_retries(self, adapter):
        goal = Goal(
            goal_id="goal_adapt_3",
            title="Impossible goal",
            description="Non-existent resource",
        )
        # Simulate many failures
        for i in range(16):
            res = adapter.adapt_goal_plan(goal=goal, error_message=f"Fail {i}")
            if not res.success:
                assert res.should_abandon is True
                break
            adapter.record_adaptation_outcome(
                goal_id=goal.goal_id,
                plan_id=res.adapted_plan.plan_id if res.adapted_plan else "plan_x",
                success=False,
            )

    def test_adaptation_outcome_calibrates_heuristics(self, adapter, calibrator):
        goal = Goal(
            goal_id="goal_adapt_4",
            title="Calibrate rule",
            description="Test heuristic rule calibration",
        )
        res = adapter.adapt_goal_plan(goal=goal, error_message="Need optimization")
        assert res.success is True
        
        # Record success
        adapter.record_adaptation_outcome(
            goal_id=goal.goal_id,
            plan_id=res.adapted_plan.plan_id,
            success=True,
        )

        rec = calibrator.get_record("rule_opt_1")
        assert rec is not None
        assert rec.success_count > 3
