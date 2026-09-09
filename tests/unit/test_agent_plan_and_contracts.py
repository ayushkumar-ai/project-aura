import time
from uuid import uuid4
import pytest

from core.agent_plan import (
    AgentPlan,
    AgentPlanStep,
    ExecutionTrace,
    Observation,
    StepDependency,
    StepStatus,
    deserialize_agent_plan,
    deserialize_execution_trace,
    deserialize_observation,
    serialize_agent_plan,
    serialize_execution_trace,
    serialize_observation,
)
from core.capability_registry import ModelCapability
from core.model_router import TaskRequirements
from core.provenance import TaintedValue, wrap_tainted, is_tainted


def test_step_status_enum_and_helpers():
    assert StepStatus.PENDING == "pending"
    assert StepStatus.READY == "ready"
    assert StepStatus.RUNNING == "running"
    assert StepStatus.SUCCEEDED == "succeeded"
    assert StepStatus.FAILED == "failed"
    assert StepStatus.BLOCKED == "blocked"
    assert StepStatus.SKIPPED == "skipped"
    assert StepStatus.CANCELLED == "cancelled"
    assert StepStatus.PAUSED == "paused"

    assert StepStatus.SUCCEEDED.is_terminal() is True
    assert StepStatus.FAILED.is_terminal() is True
    assert StepStatus.BLOCKED.is_terminal() is True
    assert StepStatus.SKIPPED.is_terminal() is True
    assert StepStatus.CANCELLED.is_terminal() is True
    assert StepStatus.RUNNING.is_terminal() is False
    assert StepStatus.PENDING.is_terminal() is False

    assert StepStatus.SUCCEEDED.is_successful() is True
    assert StepStatus.FAILED.is_successful() is False
    assert StepStatus.FAILED.is_failure() is True
    assert StepStatus.BLOCKED.is_failure() is True
    assert StepStatus.SUCCEEDED.is_failure() is False


def test_step_dependency_contract():
    dep = StepDependency(step_id="step_1", required_status=StepStatus.SUCCEEDED)
    assert dep.step_id == "step_1"
    assert dep.required_status == StepStatus.SUCCEEDED
    assert dep.allow_failure is False

    with pytest.raises(ValueError):
        StepDependency(step_id="")

    with pytest.raises(TypeError):
        StepDependency(step_id="step_1", required_status=123)


def test_observation_immutability_and_taint_propagation():
    tainted_data = wrap_tainted("Sensitive web data", source_urls=("https://example.com/page",))
    obs = Observation(
        step_id="step_search",
        task_id="task_123",
        skill_name="web_search",
        tool_name="search_tool",
        success=True,
        output=tainted_data,
        execution_time_ms=120.5,
    )

    assert obs.step_id == "step_search"
    assert obs.task_id == "task_123"
    assert obs.skill_name == "web_search"
    assert obs.is_untrusted is True
    assert "https://example.com/page" in obs.source_urls
    assert obs.execution_time_ms == 120.5

    # Test callable output rejection
    with pytest.raises(ValueError):
        Observation(
            step_id="step_1",
            task_id="task_1",
            skill_name="test",
            output=lambda: 42,
        )


def test_agent_plan_step_immutability_and_validation():
    step = AgentPlanStep(
        step_id="step_1",
        skill_name="echo_skill",
        objective="Echo message",
        input_data={"message": "hello"},
        dependencies=("step_0",),
        task_requirements=TaskRequirements(required_capabilities=[ModelCapability.REASONING]),
        max_retries=3,
    )

    assert step.step_id == "step_1"
    assert step.skill_name == "echo_skill"
    assert step.status == StepStatus.PENDING
    assert step.dependencies == ("step_0",)
    assert step.max_retries == 3
    assert step.retry_count == 0

    # Callable rejection
    with pytest.raises(ValueError):
        AgentPlanStep(
            step_id="step_fail",
            skill_name="echo",
            input_data=lambda: {"msg": "bad"},
        )


def test_agent_plan_methods():
    step1 = AgentPlanStep(step_id="step_1", skill_name="skill_a")
    step2 = AgentPlanStep(step_id="step_2", skill_name="skill_b", dependencies=("step_1",))
    plan = AgentPlan(plan_id="plan_1", task_goal="Test Goal", steps=(step1, step2))

    assert plan.plan_id == "plan_1"
    assert plan.task_goal == "Test Goal"
    assert plan.has_step("step_1") is True
    assert plan.has_step("step_99") is False
    assert plan.get_step("step_1").step_id == "step_1"

    ready = plan.get_ready_steps()
    assert len(ready) == 1
    assert ready[0].step_id == "step_1"

    # Update step 1 to SUCCEEDED
    obs1 = Observation(step_id="step_1", task_id="t1", skill_name="skill_a", success=True, output="done1")
    plan2 = plan.with_step_update("step_1", status=StepStatus.SUCCEEDED, result=obs1)

    assert plan2.get_step("step_1").status == StepStatus.SUCCEEDED
    ready2 = plan2.get_ready_steps()
    assert len(ready2) == 1
    assert ready2[0].step_id == "step_2"

    assert plan2.is_completed() is False
    # Update step 2 to SUCCEEDED
    obs2 = Observation(step_id="step_2", task_id="t1", skill_name="skill_b", success=True, output="done2")
    plan3 = plan2.with_step_update("step_2", status=StepStatus.SUCCEEDED, result=obs2)
    assert plan3.is_completed() is True
    assert plan3.status == StepStatus.SUCCEEDED


def test_execution_trace_accumulation():
    trace = ExecutionTrace(task_id="t1", plan_id="p1")
    assert trace.tool_calls_count == 0
    assert trace.total_execution_time_ms == 0.0

    obs1 = Observation(step_id="s1", task_id="t1", skill_name="calc", tool_name="calculator", execution_time_ms=50.0)
    trace2 = trace.add_observation(obs1)
    assert len(trace2.observations) == 1
    assert trace2.tool_calls_count == 1
    assert trace2.total_execution_time_ms == 50.0

    trace3 = trace2.add_replan({"failed_step_id": "s2", "error": "timeout"})
    assert len(trace3.replan_history) == 1


def test_serialization_and_deserialization_roundtrip_with_taint():
    tainted_out = wrap_tainted("untrusted content", source_urls=("https://news.ycombinator.com",))
    obs = Observation(
        step_id="step_fetch",
        task_id="task_42",
        skill_name="research",
        tool_name="http_fetch",
        success=True,
        output=tainted_out,
        execution_time_ms=250.0,
    )
    step = AgentPlanStep(
        step_id="step_fetch",
        skill_name="research",
        objective="Fetch data",
        input_data={"url": "https://news.ycombinator.com"},
        status=StepStatus.SUCCEEDED,
        result=obs,
    )
    plan = AgentPlan(
        plan_id="plan_42",
        task_goal="Perform research",
        steps=(step,),
        status=StepStatus.SUCCEEDED,
    )
    trace = ExecutionTrace(
        task_id="task_42",
        plan_id="plan_42",
        observations=(obs,),
        replan_history=({"attempt": 1},),
        tool_calls_count=1,
        total_execution_time_ms=250.0,
    )

    # 1. Observation roundtrip
    s_obs = serialize_observation(obs)
    d_obs = deserialize_observation(s_obs)
    assert d_obs.step_id == obs.step_id
    assert is_tainted(d_obs.output) is True
    assert d_obs.output.raw_value == "untrusted content"
    assert "https://news.ycombinator.com" in d_obs.source_urls

    # 2. Plan roundtrip
    s_plan = serialize_agent_plan(plan)
    d_plan = deserialize_agent_plan(s_plan)
    assert d_plan.plan_id == plan.plan_id
    assert d_plan.status == StepStatus.SUCCEEDED
    assert len(d_plan.steps) == 1
    assert d_plan.steps[0].step_id == "step_fetch"
    assert is_tainted(d_plan.steps[0].result.output) is True

    # 3. Trace roundtrip
    s_trace = serialize_execution_trace(trace)
    d_trace = deserialize_execution_trace(s_trace)
    assert d_trace.task_id == trace.task_id
    assert len(d_trace.observations) == 1
    assert is_tainted(d_trace.observations[0].output) is True
    assert len(d_trace.replan_history) == 1



def test_metadata_sanitization_and_forbidden_keys():
    meta = {
        "user_note": "important",
        "approved": True,
        "is_approved": True,
        "auto_approve": True,
        "permission": "all",
        "authorized": True,
        "callable_func": lambda: True,
    }
    step = AgentPlanStep(
        step_id="step_1",
        skill_name="echo_skill",
        metadata=meta,
    )
    # Ensure forbidden keys and callables are sanitized out
    assert "approved" not in step.metadata
    assert "is_approved" not in step.metadata
    assert "auto_approve" not in step.metadata
    assert "permission" not in step.metadata
    assert "authorized" not in step.metadata
    assert "callable_func" not in step.metadata
    assert step.metadata["user_note"] == "important"


def test_task_planner_agent_plan_validation_and_ordering():
    from core.skill_registry import Skill, SkillRegistry
    from core.task_planner import TaskPlanner

    skill_reg = SkillRegistry()
    skill_reg.register(Skill(name="skill_a"))
    skill_reg.register(Skill(name="skill_b"))
    skill_reg.register(Skill(name="skill_c"))

    planner = TaskPlanner(skill_registry=skill_reg)

    # Valid dependency DAG: A -> B -> C
    s_a = AgentPlanStep(step_id="A", skill_name="skill_a")
    s_b = AgentPlanStep(step_id="B", skill_name="skill_b", dependencies=("A",))
    s_c = AgentPlanStep(step_id="C", skill_name="skill_c", dependencies=("B",))

    plan = planner.create_agent_plan(steps=[s_c, s_b, s_a], task_goal="DAG test")
    order = planner.get_agent_execution_order(plan)
    assert [s.step_id for s in order] == ["A", "B", "C"]

    # Test unknown skill error
    s_unknown = AgentPlanStep(step_id="X", skill_name="unknown_skill")
    with pytest.raises(KeyError):
        planner.create_agent_plan(steps=[s_unknown])

    # Test duplicate step ID error
    s_dup = AgentPlanStep(step_id="A", skill_name="skill_b")
    with pytest.raises(ValueError, match="Duplicate step ID"):
        planner.create_agent_plan(steps=[s_a, s_dup])

    # Test self-dependency error
    s_self = AgentPlanStep(step_id="A", skill_name="skill_a", dependencies=("A",))
    with pytest.raises(ValueError, match="cannot depend on itself"):
        planner.create_agent_plan(steps=[s_self])

    # Test nonexistent dependency error
    s_missing = AgentPlanStep(step_id="A", skill_name="skill_a", dependencies=("NONEXISTENT",))
    with pytest.raises(ValueError, match="nonexistent step"):
        planner.create_agent_plan(steps=[s_missing])

    # Test circular dependency error
    s_c1 = AgentPlanStep(step_id="A", skill_name="skill_a", dependencies=("B",))
    s_c2 = AgentPlanStep(step_id="B", skill_name="skill_b", dependencies=("A",))
    with pytest.raises(ValueError, match="Circular dependency"):
        planner.create_agent_plan(steps=[s_c1, s_c2])


def test_conversion_between_execution_plan_and_agent_plan():
    from core.task_planner import ExecutionPlan, PlanStep, TaskPlanner

    exec_step = PlanStep(
        step_id="step_1",
        skill_name="echo_skill",
        input_data={"text": "hello"},
        dependencies=(),
    )
    exec_plan = ExecutionPlan(steps=(exec_step,), plan_id="p123")

    agent_plan = TaskPlanner.convert_execution_plan_to_agent_plan(exec_plan, task_goal="convert test")
    assert agent_plan.plan_id == "p123"
    assert agent_plan.task_goal == "convert test"
    assert len(agent_plan.steps) == 1
    assert agent_plan.steps[0].step_id == "step_1"

    converted_back = TaskPlanner.convert_agent_plan_to_execution_plan(agent_plan)
    assert converted_back.plan_id == "p123"
    assert len(converted_back.steps) == 1
    assert converted_back.steps[0].step_id == "step_1"
