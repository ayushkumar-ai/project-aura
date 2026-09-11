"""
Unit tests for Milestone 23 BenchmarkSuite and scenario runners.
"""

import pytest
from evaluation.benchmark_suite import BenchmarkSuite
from evaluation.engine import EvaluationEngine
from evaluation.models import EvaluationGrade
from evaluation.scenarios import BenchmarkCategory, BenchmarkScenario


def test_benchmark_suite_registration_and_listing():
    suite = BenchmarkSuite(name="Test Suite")

    sc1 = BenchmarkScenario(
        scenario_id="sc_calc_01",
        name="Calculator Basic Arithmetic",
        category=BenchmarkCategory.STANDARD_WORKFLOW,
        prompt="calculate 25 * 4",
        expected_criteria=("100",),
    )

    sc2 = BenchmarkScenario(
        scenario_id="sc_team_01",
        name="Team Consensus Deliberation",
        category=BenchmarkCategory.MULTI_AGENT_TEAM,
        prompt="Synthesize multi-agent security protocol",
    )

    suite.register_scenario(sc1)
    suite.register_scenario(sc2)

    # Re-registering duplicate scenario_id raises ValueError
    with pytest.raises(ValueError, match="already registered"):
        suite.register_scenario(sc1)

    assert len(suite.list_scenarios()) == 2
    assert len(suite.list_scenarios(category=BenchmarkCategory.STANDARD_WORKFLOW)) == 1
    assert suite.get_scenario("sc_calc_01") == sc1


def test_benchmark_suite_execution_and_scorecard():
    suite = BenchmarkSuite(name="Mock Execution Suite")

    class MockRuntime:
        def run_task(self, prompt):
            return {
                "task": prompt,
                "success": True,
                "status": "completed",
                "steps": [{"objective": f"Execute {prompt}", "status": "completed"}],
                "total_latency_seconds": 0.05,
            }

    sc = BenchmarkScenario(
        scenario_id="sc_mock_01",
        name="Mock Autonomous Task",
        category=BenchmarkCategory.AUTONOMOUS_AGENT,
        prompt="Perform automated validation",
        expected_criteria=("automated validation",),
    )
    suite.register_scenario(sc)

    summary = suite.run(runtime_or_aura=MockRuntime())

    assert summary.total_scenarios == 1
    assert summary.passed_scenarios == 1
    assert summary.failed_scenarios == 0
    assert summary.pass_rate == 1.0
    assert summary.mean_score >= 0.85
    assert summary.grade in (EvaluationGrade.A_EXCELLENT, EvaluationGrade.B_GOOD)
    assert "sc_mock_01" in summary.scenario_reports
