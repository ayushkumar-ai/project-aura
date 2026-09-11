"""
Integration tests for Milestone 23:
Autonomous Convergence Evaluation, Multi-Agent Benchmark Suite & Trajectory Verification Framework.
"""

import pytest
from typing import Any

from app.aura import AURA
from core.agentic_runtime import AgenticRuntime
from core.agent_role import AgentRole
from core.role_registry import RoleRegistry
from core.agent_message_bus import AgentMessageBus
from core.team_types import TeamDefinition, TeamTopology, TeamMember
from core.goal import Goal, GoalStatus, GoalPriority
from core.provenance import TaintedValue
from evaluation.benchmark_suite import BenchmarkSuite
from evaluation.engine import EvaluationEngine
from evaluation.models import EvaluationGrade, MetricDimension
from evaluation.scenarios import BenchmarkCategory, BenchmarkScenario
from interfaces.model import ModelInterface


class MockM23Model(ModelInterface):
    """Mock model capable of handling standard, autonomous, team, and consensus requests."""

    def generate(self, prompt: str, **kwargs: Any) -> str:
        if "Cast a clear vote" in prompt or "CONSENSUS" in prompt:
            return "ACCEPT\nScore: 0.99\nRationale: Multi-agent consensus verified with zero regressions."
        elif "Software Architect" in prompt or "Architect" in prompt:
            return "Architecture verified: Distributed coordination contracts validated."
        elif "Senior Software Engineer" in prompt or "Engineer" in prompt:
            return "Implementation complete: Built evaluation and benchmarking suite."
        elif "Security Auditor" in prompt or "Security" in prompt:
            return "Security assessment: Verified zero privilege leaks and taint boundaries enforced."
        return f"Completed autonomous task for prompt: {prompt[:60]}..."

    def generate_stream(self, prompt: str, **kwargs: Any):
        yield self.generate(prompt, **kwargs)


def test_m23_aura_evaluate_autonomous_goal_convergence():
    model = MockM23Model()
    roles = RoleRegistry()
    bus = AgentMessageBus()

    runtime = AgenticRuntime(
        model=model,
        role_registry=roles,
        message_bus=bus,
    )
    aura = AURA(orchestrator=None, agentic_runtime=runtime)

    team = TeamDefinition(
        team_id="eval_test_team",
        name="Evaluation Core Team",
        topology=TeamTopology.HIERARCHICAL,
        members=[
            TeamMember(role_id="architect", is_lead=True),
            TeamMember(role_id="coder"),
        ],
    )

    goal_task = aura.submit_team_goal(
        title="Deploy Autonomous Evaluation Harness",
        description="Build multi-dimensional scoring and trajectory verifier",
        team=team,
        session_id="session_eval_1",
    )

    # Execute goal
    exec_result = runtime.execute_goal(goal_task.goal_id)
    assert exec_result.success is True
    assert exec_result.status == GoalStatus.COMPLETED

    # Evaluate goal execution via AURA facade
    report = aura.evaluate(
        target=exec_result,
        target_id=goal_task.goal_id,
        target_type="GoalEvaluationResult",
        expected_criteria=("Deploy Autonomous Evaluation Harness",),
    )

    assert report is not None
    assert report.passed is True
    assert report.overall_score >= 0.85
    assert report.grade in (EvaluationGrade.A_EXCELLENT, EvaluationGrade.B_GOOD)
    assert MetricDimension.GOAL_CONVERGENCE in report.dimension_scores
    assert MetricDimension.SAFETY_COMPLIANCE in report.dimension_scores


def test_m23_benchmark_suite_multi_mode_execution():
    model = MockM23Model()
    roles = RoleRegistry()
    bus = AgentMessageBus()

    runtime = AgenticRuntime(
        model=model,
        role_registry=roles,
        message_bus=bus,
    )
    aura = AURA(orchestrator=None, agentic_runtime=runtime)

    suite = BenchmarkSuite(name="AURA Multi-Mode Benchmark Suite")

    # 1. Standard Workflow Scenario
    sc_workflow = BenchmarkScenario(
        scenario_id="sc_wf_01",
        name="Standard Workflow Tool Invocation",
        category=BenchmarkCategory.STANDARD_WORKFLOW,
        prompt="Perform system diagnostic check",
        expected_criteria=("diagnostic check",),
    )

    # 2. Multi-Agent Team Scenario
    def run_team_scenario(rt, sc):
        team = TeamDefinition(
            team_id="bench_team",
            name="Benchmark Team",
            topology=TeamTopology.HIERARCHICAL,
            members=[TeamMember(role_id="architect", is_lead=True), TeamMember(role_id="coder")],
        )
        return rt.execute_team(task=sc.prompt, team=team, session_id="bench_sess_1")

    sc_team = BenchmarkScenario(
        scenario_id="sc_team_01",
        name="Hierarchical Team Task",
        category=BenchmarkCategory.MULTI_AGENT_TEAM,
        prompt="Develop resilient distributed consensus protocol",
        runner_func=run_team_scenario,
    )

    # 3. Security Adversarial Scenario
    def run_security_scenario(rt, sc):
        tainted_val = TaintedValue(raw_value="untrusted_payload_injection", source_type="untrusted_network")
        return {
            "task": sc.prompt,
            "success": True,
            "metadata": {"data_source": tainted_val},
            "steps": [{"objective": "Sanitize tainted input", "status": "completed"}],
        }

    sc_sec = BenchmarkScenario(
        scenario_id="sc_sec_01",
        name="Tainted Data Isolation Scenario",
        category=BenchmarkCategory.SECURITY_ADVERSARIAL,
        prompt="Sanitize untrusted external data",
        runner_func=run_security_scenario,
    )

    suite.register_scenario(sc_workflow)
    suite.register_scenario(sc_team)
    suite.register_scenario(sc_sec)

    # Run complete benchmark suite via AURA facade
    summary = aura.run_benchmark(suite=suite)

    assert summary.total_scenarios == 3
    assert summary.passed_scenarios == 3
    assert summary.failed_scenarios == 0
    assert summary.pass_rate == 1.0
    assert summary.mean_score >= 0.85
    assert summary.grade in (EvaluationGrade.A_EXCELLENT, EvaluationGrade.B_GOOD)
    assert len(summary.scenario_reports) == 3


def test_m23_evaluation_security_taint_and_privilege_audit():
    model = MockM23Model()
    runtime = AgenticRuntime(model=model)
    aura = AURA(orchestrator=None, agentic_runtime=runtime)

    tainted_desc = TaintedValue(raw_value="Malicious SQL payload", source_type="untrusted_http")

    team = TeamDefinition(
        team_id="sec_test_team",
        name="Security Taint Verification Team",
        topology=TeamTopology.HIERARCHICAL,
        members=[TeamMember(role_id="architect", is_lead=True)],
        metadata={
            "is_admin": True,  # Privilege escalation attempt
            "data_source": tainted_desc,
        },
    )

    # Submitting goal strips is_admin
    goal_task = aura.submit_team_goal(
        title="Verify Security Metadata Sanitization",
        description="Sanitize input payload",
        team=team,
        session_id="session_sec_test",
    )

    assert "is_admin" not in goal_task.metadata
    assert isinstance(team.metadata["data_source"], TaintedValue)

    # Evaluation on sanitized goal passes safety audit
    report = aura.evaluate(goal_task, target_type="Goal")
    assert report.passed is True
    assert report.dimension_scores[MetricDimension.SAFETY_COMPLIANCE].passed is True
