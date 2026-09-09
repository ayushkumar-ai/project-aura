import pytest
from uuid import uuid4

from core.model_router import TaskRequirements
from core.skill_registry import Skill, SkillRegistry
from core.task_planner import ExecutionPlan, PlanStep, TaskPlanner


@pytest.fixture
def skill_registry():
    reg = SkillRegistry()
    reg.register(Skill(name="fetch_data", handler=lambda x: "data"))
    reg.register(Skill(name="process_data", handler=lambda x: "processed"))
    reg.register(Skill(name="report", handler=lambda x: "report"))
    return reg


def test_plan_step_creation_and_validation():
    step = PlanStep(
        step_id="step_1",
        skill_name="fetch_data",
        input_data={"url": "https://example.com"},
        dependencies=("step_0",),
        metadata={"priority": "high"},
    )
    assert step.step_id == "step_1"
    assert step.skill_name == "fetch_data"
    assert step.input_data == {"url": "https://example.com"}
    assert step.dependencies == ("step_0",)
    assert step.metadata == {"priority": "high"}


def test_plan_step_invalid_fields():
    with pytest.raises(ValueError, match="step_id must be a non-empty string"):
        PlanStep(step_id="", skill_name="skill")

    with pytest.raises(ValueError, match="skill_name must be a non-empty string"):
        PlanStep(step_id="step_1", skill_name="   ")

    with pytest.raises(ValueError, match="Dependency ID must be a non-empty string"):
        PlanStep(step_id="step_1", skill_name="skill", dependencies=("",))

    with pytest.raises(TypeError, match="dependencies must be a sequence"):
        PlanStep(step_id="step_1", skill_name="skill", dependencies=123)

    with pytest.raises(TypeError, match="task_requirements"):
        PlanStep(step_id="step_1", skill_name="skill", task_requirements="invalid")

    with pytest.raises(TypeError, match="metadata must be a dict"):
        PlanStep(step_id="step_1", skill_name="skill", metadata="invalid")


def test_execution_plan_creation_and_validation():
    s1 = PlanStep(step_id="s1", skill_name="fetch_data")
    s2 = PlanStep(step_id="s2", skill_name="process_data", dependencies=("s1",))

    plan = ExecutionPlan(steps=(s1, s2), plan_id="plan-123", metadata={"tag": "etl"})
    assert plan.plan_id == "plan-123"
    assert plan.steps == (s1, s2)
    assert plan.metadata == {"tag": "etl"}


def test_execution_plan_invalid_fields():
    with pytest.raises(ValueError, match="plan_id must be a non-empty string"):
        ExecutionPlan(steps=(), plan_id="")

    with pytest.raises(TypeError, match="All items in steps must be PlanStep instances"):
        ExecutionPlan(steps=["not_a_step"])


def test_planner_create_valid_single_and_multi_step_plan(skill_registry):
    planner = TaskPlanner(skill_registry)

    # Single step
    s1 = PlanStep(step_id="step_1", skill_name="fetch_data")
    plan1 = planner.create_plan([s1])
    assert len(plan1.steps) == 1
    assert plan1.steps[0].step_id == "step_1"

    # Multi-step
    s2 = PlanStep(step_id="step_2", skill_name="process_data", dependencies=("step_1",))
    s3 = PlanStep(step_id="step_3", skill_name="report", dependencies=("step_2",))
    plan2 = planner.create_plan([s1, s2, s3])
    assert len(plan2.steps) == 3


def test_planner_dependency_ordering(skill_registry):
    planner = TaskPlanner(skill_registry)

    # Provide in reverse topological order
    s3 = PlanStep(step_id="step_3", skill_name="report", dependencies=("step_2",))
    s2 = PlanStep(step_id="step_2", skill_name="process_data", dependencies=("step_1",))
    s1 = PlanStep(step_id="step_1", skill_name="fetch_data")

    plan = planner.create_plan([s3, s2, s1])
    order = planner.get_execution_order(plan)

    assert [s.step_id for s in order] == ["step_1", "step_2", "step_3"]


def test_planner_rejects_empty_plan(skill_registry):
    planner = TaskPlanner(skill_registry)
    with pytest.raises(ValueError, match="must contain at least one step"):
        planner.create_plan([])


def test_planner_rejects_unknown_skill(skill_registry):
    planner = TaskPlanner(skill_registry)
    s = PlanStep(step_id="step_1", skill_name="unregistered_skill")
    with pytest.raises(KeyError, match="Unknown skill 'unregistered_skill'"):
        planner.create_plan([s])


def test_planner_rejects_duplicate_step_id(skill_registry):
    planner = TaskPlanner(skill_registry)
    s1 = PlanStep(step_id="dup_step", skill_name="fetch_data")
    s2 = PlanStep(step_id="dup_step", skill_name="process_data")

    with pytest.raises(ValueError, match="Duplicate step ID detected"):
        planner.create_plan([s1, s2])


def test_planner_rejects_nonexistent_dependency(skill_registry):
    planner = TaskPlanner(skill_registry)
    s1 = PlanStep(step_id="step_1", skill_name="fetch_data", dependencies=("nonexistent",))

    with pytest.raises(ValueError, match="depends on nonexistent step 'nonexistent'"):
        planner.create_plan([s1])


def test_planner_rejects_self_dependency(skill_registry):
    planner = TaskPlanner(skill_registry)
    s1 = PlanStep(step_id="step_1", skill_name="fetch_data", dependencies=("step_1",))

    with pytest.raises(ValueError, match="cannot depend on itself"):
        planner.create_plan([s1])


def test_planner_rejects_circular_dependency(skill_registry):
    planner = TaskPlanner(skill_registry)
    s1 = PlanStep(step_id="step_1", skill_name="fetch_data", dependencies=("step_2",))
    s2 = PlanStep(step_id="step_2", skill_name="process_data", dependencies=("step_1",))

    with pytest.raises(ValueError, match="Circular dependency detected"):
        planner.create_plan([s1, s2])


def test_planner_alias_module_imports():
    from core.planner import ExecutionPlan as Plan
    from core.planner import PlanStep as Step
    from core.planner import TaskPlanner as Planner

    assert Plan is ExecutionPlan
    assert Step is PlanStep
    assert Planner is TaskPlanner
