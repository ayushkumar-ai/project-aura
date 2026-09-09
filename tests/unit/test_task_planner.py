import json
import pytest
from uuid import UUID, uuid4

from core.capability_registry import (
    CapabilityRegistry,
    ModelCapability,
    ModelDescriptor,
)
from core.model_router import ModelRouter, TaskRequirements
from core.models import AURAResponse
from core.provider_registry import ProviderRegistry
from core.skill_registry import Skill, SkillRegistry
from core.task_planner import ExecutionPlan, PlanStep, TaskPlanner
from interfaces.model import ModelInterface
from providers.fake_model import FakeModelProvider


class ProgrammableModelProvider(ModelInterface):
    """Test model double returning canned plan outputs."""

    def __init__(self, response_content: str = ""):
        self.response_content = response_content
        self.last_prompt = ""
        self.calls = 0

    def generate(self, prompt: str, request_id: UUID) -> AURAResponse:
        self.last_prompt = prompt
        self.calls += 1
        if self.response_content == "RAISE_ERROR":
            raise RuntimeError("Model generation failed.")
        return AURAResponse(request_id=request_id, content=self.response_content)


@pytest.fixture
def skill_registry():
    reg = SkillRegistry()
    reg.register(Skill(name="fetch_data", description="Fetches web data", handler=lambda x: "data"))
    reg.register(Skill(name="process_data", description="Processes raw data", handler=lambda x: "processed"))
    reg.register(Skill(name="report", description="Generates reports", handler=lambda x: "report"))
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


# ==========================================================
# MODEL-ASSISTED PLANNING TESTS
# ==========================================================


def test_model_assisted_plan_generation_valid(skill_registry):
    canned_plan = {
        "steps": [
            {
                "step_id": "step_1",
                "skill_name": "fetch_data",
                "input_data": {"url": "https://api.example.com"},
                "dependencies": [],
            },
            {
                "step_id": "step_2",
                "skill_name": "process_data",
                "dependencies": ["step_1"],
            },
            {
                "step_id": "step_3",
                "skill_name": "report",
                "dependencies": ["step_2"],
            },
        ]
    }
    model = ProgrammableModelProvider(json.dumps(canned_plan))
    planner = TaskPlanner(skill_registry=skill_registry, model=model)

    plan = planner.plan("Fetch data, process it, and generate a summary report.")

    assert model.calls == 1
    assert "fetch_data" in model.last_prompt
    assert "Fetch data, process it" in model.last_prompt
    assert len(plan.steps) == 3
    assert plan.steps[0].step_id == "step_1"
    assert plan.steps[1].step_id == "step_2"
    assert plan.steps[2].step_id == "step_3"
    assert plan.steps[1].dependencies == ("step_1",)
    assert plan.steps[2].dependencies == ("step_2",)


def test_model_assisted_plan_generation_with_markdown_fences(skill_registry):
    canned_plan = {
        "steps": [
            {
                "step_id": "s1",
                "skill_name": "fetch_data",
                "dependencies": [],
            }
        ]
    }
    raw_content = f"```json\n{json.dumps(canned_plan)}\n```"
    model = ProgrammableModelProvider(raw_content)
    planner = TaskPlanner(skill_registry=skill_registry, model=model)

    plan = planner.plan("Get data")
    assert len(plan.steps) == 1
    assert plan.steps[0].step_id == "s1"


def test_model_assisted_plan_rejects_empty_task(skill_registry):
    planner = TaskPlanner(skill_registry, model=ProgrammableModelProvider("{}"))
    with pytest.raises(ValueError, match="Task description must be a non-empty string"):
        planner.plan("")

    with pytest.raises(ValueError, match="Task description must be a non-empty string"):
        planner.plan("   ")


def test_model_assisted_plan_requires_model_or_router(skill_registry):
    planner = TaskPlanner(skill_registry)
    with pytest.raises(ValueError, match="ModelInterface or ModelRouter required"):
        planner.plan("Do task")


def test_model_assisted_plan_via_model_router(skill_registry):
    cap_reg = CapabilityRegistry()
    prov_reg = ProviderRegistry()

    canned_plan = {
        "steps": [{"step_id": "s1", "skill_name": "fetch_data", "dependencies": []}]
    }
    p_model = ProgrammableModelProvider(json.dumps(canned_plan))
    prov_reg.register("prov", p_model)

    m_desc = ModelDescriptor(
        model_id="reasoning_model",
        provider_id="prov",
        capabilities={ModelCapability.REASONING},
    )
    cap_reg.register_model(m_desc)

    router = ModelRouter(cap_reg, prov_reg)
    planner = TaskPlanner(skill_registry=skill_registry, model_router=router)

    plan = planner.plan("Fetch data")
    assert len(plan.steps) == 1
    assert plan.steps[0].skill_name == "fetch_data"


def test_model_assisted_plan_rejects_unknown_skill(skill_registry):
    canned_plan = {
        "steps": [
            {"step_id": "s1", "skill_name": "invented_alien_skill", "dependencies": []}
        ]
    }
    model = ProgrammableModelProvider(json.dumps(canned_plan))
    planner = TaskPlanner(skill_registry=skill_registry, model=model)

    with pytest.raises(KeyError, match="Unknown skill 'invented_alien_skill'"):
        planner.plan("Do something alien")


def test_model_assisted_plan_rejects_malformed_json(skill_registry):
    model = ProgrammableModelProvider("I cannot do this task because I am an AI.")
    planner = TaskPlanner(skill_registry=skill_registry, model=model)

    with pytest.raises(ValueError, match="Failed to parse model plan output as JSON"):
        planner.plan("Do something")


def test_model_assisted_plan_rejects_missing_steps_key(skill_registry):
    model = ProgrammableModelProvider(json.dumps({"wrong_key": []}))
    planner = TaskPlanner(skill_registry=skill_registry, model=model)

    with pytest.raises(ValueError, match="missing 'steps' key"):
        planner.plan("Do something")


def test_model_assisted_plan_rejects_invalid_dependencies(skill_registry):
    canned_plan = {
        "steps": [
            {"step_id": "s1", "skill_name": "fetch_data", "dependencies": ["nonexistent_step"]}
        ]
    }
    model = ProgrammableModelProvider(json.dumps(canned_plan))
    planner = TaskPlanner(skill_registry=skill_registry, model=model)

    with pytest.raises(ValueError, match="depends on nonexistent step 'nonexistent_step'"):
        planner.plan("Do something")


def test_model_assisted_plan_rejects_circular_dependencies(skill_registry):
    canned_plan = {
        "steps": [
            {"step_id": "s1", "skill_name": "fetch_data", "dependencies": ["s2"]},
            {"step_id": "s2", "skill_name": "process_data", "dependencies": ["s1"]},
        ]
    }
    model = ProgrammableModelProvider(json.dumps(canned_plan))
    planner = TaskPlanner(skill_registry=skill_registry, model=model)

    with pytest.raises(ValueError, match="Circular dependency detected"):
        planner.plan("Do circular task")


def test_model_assisted_plan_handles_model_failure(skill_registry):
    model = ProgrammableModelProvider("RAISE_ERROR")
    planner = TaskPlanner(skill_registry=skill_registry, model=model)

    with pytest.raises(RuntimeError, match="Model generation failed"):
        planner.plan("Do failing task")


def test_model_plan_does_not_execute_automatically(skill_registry):
    executed = []

    def tracking_handler(inp):
        executed.append(inp)
        return "ok"

    skill_registry.register(Skill(name="track_skill", handler=tracking_handler))

    canned_plan = {
        "steps": [{"step_id": "s1", "skill_name": "track_skill", "dependencies": []}]
    }
    model = ProgrammableModelProvider(json.dumps(canned_plan))
    planner = TaskPlanner(skill_registry=skill_registry, model=model)

    plan = planner.plan("Run track skill")

    # The plan is created and validated, but NOT executed!
    assert len(plan.steps) == 1
    assert executed == []


def test_planner_alias_module_imports():
    from core.planner import ExecutionPlan as Plan
    from core.planner import PlanStep as Step
    from core.planner import TaskPlanner as Planner

    assert Plan is ExecutionPlan
    assert Step is PlanStep
    assert Planner is TaskPlanner


# ==========================================================
# PLANNER HARDENING REGRESSION TESTS (M8.9)
# ==========================================================


def test_model_plan_rejects_empty_or_whitespace_response(skill_registry):
    # Empty string
    p1 = TaskPlanner(skill_registry, model=ProgrammableModelProvider(""))
    with pytest.raises(ValueError, match="empty or whitespace-only response"):
        p1.plan("Task")

    # Whitespace only
    p2 = TaskPlanner(skill_registry, model=ProgrammableModelProvider("   \n\t  "))
    with pytest.raises(ValueError, match="empty or whitespace-only response"):
        p2.plan("Task")


def test_model_plan_rejects_json_scalars(skill_registry):
    # String scalar
    p1 = TaskPlanner(skill_registry, model=ProgrammableModelProvider('"just a string"'))
    with pytest.raises(ValueError, match="must be an object with 'steps' or a list of steps"):
        p1.plan("Task")

    # Number scalar
    p2 = TaskPlanner(skill_registry, model=ProgrammableModelProvider('12345'))
    with pytest.raises(ValueError, match="must be an object with 'steps' or a list of steps"):
        p2.plan("Task")

    # Boolean scalar
    p3 = TaskPlanner(skill_registry, model=ProgrammableModelProvider('true'))
    with pytest.raises(ValueError, match="must be an object with 'steps' or a list of steps"):
        p3.plan("Task")

    # Null scalar
    p4 = TaskPlanner(skill_registry, model=ProgrammableModelProvider('null'))
    with pytest.raises(ValueError, match="must be an object with 'steps' or a list of steps"):
        p4.plan("Task")


def test_model_plan_rejects_null_or_wrong_steps_type(skill_registry):
    # Null steps
    p1 = TaskPlanner(skill_registry, model=ProgrammableModelProvider('{"steps": null}'))
    with pytest.raises(ValueError, match="'steps' key cannot be null"):
        p1.plan("Task")

    # Dict instead of list
    p2 = TaskPlanner(skill_registry, model=ProgrammableModelProvider('{"steps": {"step_1": "fetch_data"}}'))
    with pytest.raises(ValueError, match="'steps' must be a list"):
        p2.plan("Task")

    # Int instead of list
    p3 = TaskPlanner(skill_registry, model=ProgrammableModelProvider('{"steps": 42}'))
    with pytest.raises(ValueError, match="'steps' must be a list"):
        p3.plan("Task")

    # Empty steps list
    p4 = TaskPlanner(skill_registry, model=ProgrammableModelProvider('{"steps": []}'))
    with pytest.raises(ValueError, match="empty list of steps"):
        p4.plan("Task")


def test_model_plan_rejects_malformed_step_objects(skill_registry):
    # Step is a string
    p1 = TaskPlanner(skill_registry, model=ProgrammableModelProvider('{"steps": ["not_a_dict"]}'))
    with pytest.raises(ValueError, match="must be a JSON object/dictionary"):
        p1.plan("Task")

    # Step is an integer
    p2 = TaskPlanner(skill_registry, model=ProgrammableModelProvider('{"steps": [123]}'))
    with pytest.raises(ValueError, match="must be a JSON object/dictionary"):
        p2.plan("Task")

    # Step is null
    p3 = TaskPlanner(skill_registry, model=ProgrammableModelProvider('{"steps": [null]}'))
    with pytest.raises(ValueError, match="must be a JSON object/dictionary"):
        p3.plan("Task")


def test_model_plan_rejects_missing_or_invalid_step_fields(skill_registry):
    # Missing step_id
    p1 = TaskPlanner(skill_registry, model=ProgrammableModelProvider('{"steps": [{"skill_name": "fetch_data"}]}'))
    with pytest.raises(ValueError, match="must have a valid non-empty string 'step_id'"):
        p1.plan("Task")

    # Whitespace step_id
    p2 = TaskPlanner(skill_registry, model=ProgrammableModelProvider('{"steps": [{"step_id": "   ", "skill_name": "fetch_data"}]}'))
    with pytest.raises(ValueError, match="must have a valid non-empty string 'step_id'"):
        p2.plan("Task")

    # Missing skill_name
    p3 = TaskPlanner(skill_registry, model=ProgrammableModelProvider('{"steps": [{"step_id": "s1"}]}'))
    with pytest.raises(ValueError, match="must have a valid non-empty string 'skill_name'"):
        p3.plan("Task")

    # Whitespace skill_name
    p4 = TaskPlanner(skill_registry, model=ProgrammableModelProvider('{"steps": [{"step_id": "s1", "skill_name": "   "}]}'))
    with pytest.raises(ValueError, match="must have a valid non-empty string 'skill_name'"):
        p4.plan("Task")


def test_model_plan_rejects_malformed_dependency_elements(skill_registry):
    # Non-list dependencies (e.g. string)
    p1 = TaskPlanner(skill_registry, model=ProgrammableModelProvider('{"steps": [{"step_id": "s1", "skill_name": "fetch_data", "dependencies": "s0"}]}'))
    with pytest.raises(ValueError, match="must be a list or tuple"):
        p1.plan("Task")

    # Dependency containing non-string/empty element
    p2 = TaskPlanner(skill_registry, model=ProgrammableModelProvider('{"steps": [{"step_id": "s1", "skill_name": "fetch_data", "dependencies": [123]}]}'))
    with pytest.raises(ValueError, match="Dependency in step 's1' must be a non-empty string"):
        p2.plan("Task")

    # Dependency containing empty string
    p3 = TaskPlanner(skill_registry, model=ProgrammableModelProvider('{"steps": [{"step_id": "s1", "skill_name": "fetch_data", "dependencies": ["   "]}]}'))
    with pytest.raises(ValueError, match="Dependency in step 's1' must be a non-empty string"):
        p3.plan("Task")


def test_model_plan_rejects_self_dependency(skill_registry):
    canned = {
        "steps": [
            {"step_id": "s1", "skill_name": "fetch_data", "dependencies": ["s1"]}
        ]
    }
    planner = TaskPlanner(skill_registry, model=ProgrammableModelProvider(json.dumps(canned)))
    with pytest.raises(ValueError, match="cannot depend on itself"):
        planner.plan("Task")


def test_model_plan_rejects_duplicate_step_ids(skill_registry):
    canned = {
        "steps": [
            {"step_id": "s1", "skill_name": "fetch_data"},
            {"step_id": "s1", "skill_name": "process_data"},
        ]
    }
    planner = TaskPlanner(skill_registry, model=ProgrammableModelProvider(json.dumps(canned)))
    with pytest.raises(ValueError, match="Duplicate step ID detected"):
        planner.plan("Task")


def test_model_plan_parses_and_validates_task_requirements(skill_registry):
    # Valid task_requirements
    canned_valid = {
        "steps": [
            {
                "step_id": "s1",
                "skill_name": "fetch_data",
                "task_requirements": {
                    "required_capabilities": ["reasoning"],
                    "preferred_model": "gpt-4",
                    "preferred_provider": "openai"
                }
            }
        ]
    }
    p1 = TaskPlanner(skill_registry, model=ProgrammableModelProvider(json.dumps(canned_valid)))
    plan = p1.plan("Task")
    reqs = plan.steps[0].task_requirements
    assert reqs is not None
    assert ModelCapability.REASONING in reqs.required_capabilities
    assert reqs.preferred_model == "gpt-4"
    assert reqs.preferred_provider == "openai"

    # Invalid task_requirements: not a dict
    canned_invalid_req = {
        "steps": [{"step_id": "s1", "skill_name": "fetch_data", "task_requirements": "not_a_dict"}]
    }
    p2 = TaskPlanner(skill_registry, model=ProgrammableModelProvider(json.dumps(canned_invalid_req)))
    with pytest.raises(ValueError, match="task_requirements for step 's1' must be a dictionary"):
        p2.plan("Task")

    # Invalid required_capabilities: empty string item
    canned_invalid_cap = {
        "steps": [
            {
                "step_id": "s1",
                "skill_name": "fetch_data",
                "task_requirements": {"required_capabilities": [""]}
            }
        ]
    }
    p3 = TaskPlanner(skill_registry, model=ProgrammableModelProvider(json.dumps(canned_invalid_cap)))
    with pytest.raises(ValueError, match="Capability for step 's1' must be a non-empty string"):
        p3.plan("Task")


def test_model_plan_with_surrounding_commentary_and_fenced_json(skill_registry):
    raw_response = """
    Certainly! Below is the requested execution plan to fetch and process data:

    ```json
    {
        "steps": [
            {
                "step_id": "step_1",
                "skill_name": "fetch_data",
                "input_data": {"url": "https://data.example.com"}
            },
            {
                "step_id": "step_2",
                "skill_name": "process_data",
                "dependencies": ["step_1"]
            }
        ]
    }
    ```

    Please let me know if you would like any modifications to this plan.
    """
    planner = TaskPlanner(skill_registry, model=ProgrammableModelProvider(raw_response))
    plan = planner.plan("Fetch and process data")

    assert len(plan.steps) == 2
    assert plan.steps[0].step_id == "step_1"
    assert plan.steps[1].step_id == "step_2"
    assert plan.steps[1].dependencies == ("step_1",)
    assert plan.steps[0].input_data == {"url": "https://data.example.com"}
