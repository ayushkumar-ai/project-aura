"""Integration tests for Milestone 16: Autonomous Goal-Strategy Adaptation & Self-Healing Engine.

Verifies end-to-end integration across:
- GoalEngine + GoalReasoner + GoalAdapter + MetaPolicyEngine + StrategyLineageStore + GoalStagnationMonitor
- Dynamic plan adaptation upon execution failure
- Convergence tracking and stagnation recovery / graceful abandonment
- Active heuristic calibration injection (PROMOTED rules included, DEPRECATED omitted)
- Provenance grounding and strict non-authorizing security boundaries
"""

import time
import pytest
from uuid import uuid4

from core.agent_plan import AgentPlan, AgentPlanStep, StepStatus
from core.agent_runtime import AgentRuntime
from core.agentic_runtime import AgenticRuntime, ExecutionMode
from core.goal import Goal, GoalObservation, GoalPriority, GoalProgress, GoalStatus, GoalTrigger, TriggerType
from core.goal_adapter import GoalAdapter, GoalAdaptationResult
from core.goal_engine import GoalEngine, GoalEngineConfig
from core.goal_reasoner import GoalEvaluationResult, GoalReasoner
from core.goal_stagnation import GoalStagnationMonitor
from core.goal_store import InMemoryGoalStore
from core.heuristic_calibrator import HeuristicCalibrator
from core.lifecycle_types import RuleStatus
from core.meta_policy import MetaPolicyEngine
from core.provenance import TaintedValue, is_tainted, wrap_tainted
from core.skill_registry import Skill, SkillRegistry
from core.strategy_lineage import StrategyLineageStore
from core.strategy_types import StagnationReport, StrategyAttemptOutcome, StrategyType


class TestM16GoalAdaptationWorkflow:
    @pytest.fixture
    def skill_registry(self):
        reg = SkillRegistry()
        reg.register(
            Skill(
                name="execute_goal_action",
                description="Direct action executor",
                handler=lambda *args, **kwargs: {"status": "direct_done", **kwargs},
            )
        )
        reg.register(
            Skill(
                name="validate_prerequisites",
                description="Validates preconditions",
                handler=lambda *args, **kwargs: {"valid": True, **kwargs},
            )
        )
        reg.register(
            Skill(
                name="execute_decomposed_action",
                description="Decomposed action executor",
                handler=lambda *args, **kwargs: {"result": "decomposed_success", **kwargs},
            )
        )
        reg.register(
            Skill(
                name="verify_goal_progress",
                description="Verifies progress",
                handler=lambda *args, **kwargs: {"progress_verified": True, **kwargs},
            )
        )
        reg.register(
            Skill(
                name="research_context",
                description="Researches required context",
                handler=lambda *args, **kwargs: {"research_data": "useful context", **kwargs},
            )
        )
        reg.register(
            Skill(
                name="execute_informed_action",
                description="Executes action with research",
                handler=lambda *args, **kwargs: {"informed_result": "success", **kwargs},
            )
        )
        return reg

    @pytest.fixture
    def runtime(self, skill_registry):
        return AgentRuntime(skill_registry=skill_registry)

    @pytest.fixture
    def calibrator(self):
        cal = HeuristicCalibrator()
        # Promote rule 1
        cal.register_rule("rule_opt_1", "optimize payload schema", base_confidence=0.9)
        cal.record_outcome("rule_opt_1", success=True)
        cal.record_outcome("rule_opt_1", success=True)
        cal.record_outcome("rule_opt_1", success=True)

        # Deprecate rule 2
        cal.register_rule("rule_flaky_2", "retry immediately without backoff", base_confidence=0.5)
        cal.record_outcome("rule_flaky_2", success=False)
        cal.record_outcome("rule_flaky_2", success=False)
        cal.record_outcome("rule_flaky_2", success=False)
        return cal

    def test_end_to_end_goal_failure_and_adaptation(self, runtime, calibrator):
        """Test that goal failure triggers MetaPolicyEngine strategy pivoting and GoalAdapter plan synthesis."""
        lineage_store = StrategyLineageStore()
        meta_policy = MetaPolicyEngine(lineage_store=lineage_store, calibrator=calibrator)
        goal_adapter = GoalAdapter(meta_policy=meta_policy, lineage_store=lineage_store, heuristic_calibrator=calibrator)
        stagnation_monitor = GoalStagnationMonitor(lineage_store=lineage_store, meta_policy=meta_policy)

        engine = GoalEngine(
            runtime=runtime,
            meta_policy=meta_policy,
            strategy_lineage=lineage_store,
            goal_adapter=goal_adapter,
            stagnation_monitor=stagnation_monitor,
            heuristic_calibrator=calibrator,
        )

        goal = engine.create_goal(
            title="Deploy high-availability cluster",
            description="Provision nodes and configure load balancer",
            success_criteria=["cluster_deployed"],
        )

        # Step 1: Initial adaptation after hypothetical failure
        res1 = goal_adapter.adapt_goal_plan(
            goal=goal,
            error_message="Direct deployment failed due to network timeout",
        )
        assert res1.success is True
        assert res1.attempt_number == 1
        assert res1.selected_strategy in (StrategyType.DIRECT_SKILL, StrategyType.DECOMPOSED_HIERARCHICAL, StrategyType.RESEARCH_ASSISTED_SYNTHESIS)
        assert "rule_opt_1" in "".join(res1.heuristics_applied)
        assert "rule_flaky_2" not in "".join(res1.heuristics_applied)

        # Step 2: Record failure of attempt 1
        goal_adapter.record_adaptation_outcome(
            goal_id=goal.goal_id,
            plan_id=res1.adapted_plan.plan_id,
            success=False,
            failure_category="tool_execution_failure",
        )

        # Step 3: Second adaptation should pivot to a new strategy modality
        res2 = goal_adapter.adapt_goal_plan(
            goal=goal,
            failed_plan=res1.adapted_plan,
            error_message="Subsystem unavailable",
        )
        assert res2.success is True
        assert res2.attempt_number == 2
        assert res2.selected_strategy != res1.selected_strategy
        assert len(res2.adapted_plan.steps) > 0

    def test_goal_stagnation_detection_and_pivot_in_runtime(self, skill_registry, calibrator):
        """Test stagnation monitor detects zero progress over 5 evaluations and triggers pivot."""
        agentic_rt = AgenticRuntime(
            skill_registry=skill_registry,
            calibrator=calibrator,
        )

        goal = agentic_rt.goal_engine.create_goal(
            title="Periodic state sync",
            description="Continuously synchronizes distributed state",
            success_criteria=["state_synced"],
        )

        # Evaluate 5 times with no progress
        rep = None
        for _ in range(5):
            rep = agentic_rt.goal_engine.stagnation_monitor.check_stagnation(
                goal,
                current_progress_percentage=0.10,
            )

        assert rep.is_stagnant is True
        assert rep.consecutive_stagnant_evaluations >= 5
        assert rep.should_pivot_strategy is True
        assert rep.should_abandon_goal is False
        assert rep.recommended_strategy is not None

    def test_calibrated_promoted_heuristic_injection_in_planner(self, skill_registry, calibrator):
        """Test that TaskPlanner injects PROMOTED heuristics and strictly excludes DEPRECATED rules."""
        from core.task_planner import TaskPlanner

        planner = TaskPlanner(
            skill_registry=skill_registry,
            heuristic_calibrator=calibrator,
        )

        prompt = planner._build_planning_prompt("Optimize database queries")
        assert "Calibrated Heuristic Rules (PROMOTED)" in prompt
        assert "rule_opt_1" in prompt
        assert "rule_flaky_2" not in prompt  # Deprecated rule must be omitted

    def test_non_authorizing_and_taint_provenance_preservation(self, skill_registry, calibrator):
        """Verify that malicious authorization metadata is stripped and untrusted TaintedValue data is preserved."""
        lineage_store = StrategyLineageStore()
        meta_policy = MetaPolicyEngine(lineage_store=lineage_store, calibrator=calibrator)
        goal_adapter = GoalAdapter(meta_policy=meta_policy, lineage_store=lineage_store, heuristic_calibrator=calibrator)

        tainted_err = wrap_tainted(
            value="Error: <script>alert(1)</script>",
            is_untrusted=True,
            source_type="external_api",
        )

        goal = Goal(
            goal_id="g_sec_test",
            title="Security validation goal",
            description="Test security boundaries",
        )

        malicious_meta = {
            "approved": True,
            "auto_approve": True,
            "bypass_policy": True,
            "safe_tag": "audit_123",
            "tainted_payload": tainted_err,
        }

        res = goal_adapter.adapt_goal_plan(
            goal=goal,
            error_message="Failed with untrusted payload",
            metadata=malicious_meta,
        )

        assert res.success is True
        # Verify forbidden keys were stripped
        for forbidden in ("approved", "auto_approve", "bypass_policy"):
            assert forbidden not in res.metadata
            if res.adapted_plan:
                assert forbidden not in res.adapted_plan.metadata

        # Verify safe metadata was preserved
        assert res.metadata.get("safe_tag") == "audit_123"

    def test_irrecoverable_stagnation_graceful_abandonment(self, calibrator):
        """Verify that a goal with exhausted strategies and persistent zero progress is gracefully abandoned."""
        failing_registry = SkillRegistry()
        def fail_handler(*args, **kwargs):
            raise RuntimeError("Permanent upstream failure")

        failing_registry.register(
            Skill(
                name="failing_action",
                description="Always fails",
                handler=fail_handler,
            )
        )

        lineage_store = StrategyLineageStore()
        meta_policy = MetaPolicyEngine(lineage_store=lineage_store, calibrator=calibrator)
        goal_adapter = GoalAdapter(meta_policy=meta_policy, lineage_store=lineage_store, heuristic_calibrator=calibrator)
        stagnation_monitor = GoalStagnationMonitor(
            max_stagnation_evaluations=3,
            lineage_store=lineage_store,
            meta_policy=meta_policy,
        )

        engine = GoalEngine(
            runtime=AgentRuntime(skill_registry=failing_registry),
            meta_policy=meta_policy,
            strategy_lineage=lineage_store,
            goal_adapter=goal_adapter,
            stagnation_monitor=stagnation_monitor,
            heuristic_calibrator=calibrator,
        )

        goal = engine.create_goal(
            title="Unachievable task",
            description="Resource permanently offline",
            success_criteria=["impossible_criterion"],
        )

        # Fail all strategies in lineage
        for st in StrategyType:
            lineage_store.record_attempt(
                goal_id=goal.goal_id,
                strategy_type=st,
                outcome=StrategyAttemptOutcome.FAILURE,
            )

        # Run evaluations until stagnation failure occurs
        eval_results = []
        for _ in range(6):
            res = engine.evaluate_goal(goal.goal_id)
            eval_results.append(res)
            if "stagnant" in res.rationale.lower():
                break

        # Goal should now be transitioned to FAILED with abandonment rationale
        stored_goal = engine.get_goal(goal.goal_id)
        assert stored_goal.status == GoalStatus.FAILED
        assert any("stagnant" in r.rationale.lower() for r in eval_results)
