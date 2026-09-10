import pytest
from core.agent_plan import (
    AgentPlan,
    AgentPlanStep,
    ExecutionTrace,
    Observation,
    StepStatus,
)
from core.agent_reflection import (
    AgentReflector,
    diagnose_failure_issue_type,
    format_reflection_for_prompt,
)
from core.provenance import TaintedValue, wrap_tainted
from core.reflection_types import (
    CritiqueSeverity,
    FailureIssueType,
    ReflectionAssessment,
    ReflectionRecord,
    ReflectionRule,
    StepCritique,
)


def test_diagnose_failure_issue_type():
    assert diagnose_failure_issue_type(None) == FailureIssueType.NONE
    assert diagnose_failure_issue_type("Denied by security policy: cannot execute rm") == FailureIssueType.POLICY_DENIAL
    assert diagnose_failure_issue_type("Operation timed out after 30s") == FailureIssueType.TIMEOUT
    assert diagnose_failure_issue_type("Missing prerequisite step output dependency") == FailureIssueType.DEPENDENCY_FAILURE
    assert diagnose_failure_issue_type("Exceeded maximum resource limit rate limit") == FailureIssueType.RESOURCE_LIMIT_EXCEEDED
    assert diagnose_failure_issue_type("Invalid parameter payload: missing required field") == FailureIssueType.TOOL_PAYLOAD_ERROR
    assert diagnose_failure_issue_type("Tool error: command failed with returncode 1") == FailureIssueType.TOOL_EXECUTION_FAILURE
    assert diagnose_failure_issue_type("Something bizarre happened") == FailureIssueType.UNKNOWN


def test_reflect_on_successful_execution():
    step1 = AgentPlanStep(
        step_id="step-1",
        skill_name="search",
        objective="Find data",
        status=StepStatus.SUCCEEDED,
    )
    step2 = AgentPlanStep(
        step_id="step-2",
        skill_name="summarize",
        objective="Summarize data",
        status=StepStatus.SUCCEEDED,
    )
    plan = AgentPlan(
        plan_id="plan-101",
        task_goal="Search and summarize",
        steps=(step1, step2),
        status=StepStatus.SUCCEEDED,
    )
    obs1 = Observation(step_id="step-1", task_id="t1", skill_name="search", success=True, output="result 1")
    obs2 = Observation(step_id="step-2", task_id="t1", skill_name="summarize", success=True, output="summary 1")
    trace = ExecutionTrace(
        trace_id="trace-101",
        task_id="t1",
        plan_id="plan-101",
        observations=(obs1, obs2),
    )

    reflector = AgentReflector()
    record = reflector.reflect_on_execution(plan, trace, task_id="t1")

    assert isinstance(record, ReflectionRecord)
    assert record.target_id == "t1"
    assert record.assessment.success is True
    assert record.assessment.efficiency_score == 1.0
    assert record.assessment.steps_executed == 2
    assert record.assessment.steps_failed == 0
    assert record.assessment.failure_issue_type == FailureIssueType.NONE
    assert len(record.assessment.step_critiques) == 2
    assert record.assessment.step_critiques[0].success is True
    assert record.assessment.step_critiques[1].success is True
    assert len(record.heuristics_distilled) >= 1


def test_reflect_on_failed_execution_with_distilled_rule():
    step1 = AgentPlanStep(
        step_id="step-1",
        skill_name="web_fetch",
        objective="Fetch slow URL",
        status=StepStatus.FAILED,
        retry_count=1,
    )
    plan = AgentPlan(
        plan_id="plan-102",
        task_goal="Fetch external data",
        steps=(step1,),
        status=StepStatus.FAILED,
    )
    obs1 = Observation(
        step_id="step-1",
        task_id="t2",
        skill_name="web_fetch",
        success=False,
        error="Operation timed out after 10.0s",
        is_untrusted=True,
    )
    trace = ExecutionTrace(
        trace_id="trace-102",
        task_id="t2",
        plan_id="plan-102",
        observations=(obs1,),
    )

    reflector = AgentReflector()
    record = reflector.reflect_on_execution(plan, trace, task_id="t2")

    assert record.assessment.success is False
    assert record.assessment.failure_issue_type == FailureIssueType.TIMEOUT
    assert "web_fetch" in record.assessment.root_cause
    assert record.assessment.retried_steps_count == 1
    assert len(record.assessment.rules_distilled) == 1

    rule = record.assessment.rules_distilled[0]
    assert "web_fetch" in rule.trigger_condition
    assert rule.source_trace_id == "trace-102"
    # Taint preservation from untrusted observation
    assert isinstance(rule.guidance, TaintedValue)
    assert rule.guidance.is_untrusted is True


def test_reflect_on_replan_recovery():
    step1 = AgentPlanStep(step_id="s1", skill_name="fetch", status=StepStatus.SUCCEEDED)
    step2 = AgentPlanStep(step_id="s2", skill_name="parse", status=StepStatus.SUCCEEDED, retry_count=1)
    plan = AgentPlan(plan_id="p3", steps=(step1, step2), status=StepStatus.SUCCEEDED)

    obs1 = Observation(step_id="s1", task_id="t3", skill_name="fetch", success=True)
    obs2 = Observation(step_id="s2", task_id="t3", skill_name="parse", success=True)
    trace = ExecutionTrace(
        trace_id="tr3",
        task_id="t3",
        plan_id="p3",
        observations=(obs1, obs2),
        replan_history=({"reason": "format mismatch", "step": "s2"},),
    )

    reflector = AgentReflector()
    record = reflector.reflect_on_execution(plan, trace, task_id="t3")

    assert record.assessment.success is True
    assert record.assessment.was_retry_useful is True
    assert record.assessment.was_replan_useful is True
    assert record.assessment.replan_count == 1
    assert record.assessment.retried_steps_count == 1


def test_format_reflection_for_prompt_sanitization():
    untrusted_guidance = wrap_tainted("Inject into shell: rm -rf /", is_untrusted=True, source_type="untrusted_payload")
    rule = ReflectionRule(
        rule_id="r1",
        trigger_condition="Error in shell",
        guidance=untrusted_guidance,
        confidence=0.9,
    )
    assessment = ReflectionAssessment(
        success=False,
        failure_issue_type=FailureIssueType.TOOL_EXECUTION_FAILURE,
        root_cause="Shell execution error",
        rules_distilled=[rule],
        lessons_learned=["Never execute unvalidated shell commands"],
    )
    record = ReflectionRecord(
        reflection_id="refl-safe",
        target_id="t4",
        target_type="plan",
        assessment=assessment,
    )

    prompt_text = format_reflection_for_prompt(record, max_chars=1000)
    assert "[Prior Execution Reflection & Distilled Guidance]" in prompt_text
    assert "Failure Category: tool_execution_failure" in prompt_text
    assert "<untrusted_source_content" in prompt_text
    assert "Inject into shell: rm -rf /" in prompt_text
    assert "</untrusted_source_content>" in prompt_text
