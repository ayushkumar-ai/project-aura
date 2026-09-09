import pytest

from core.agent_runtime import AgentRequest, AgentResult, AgentRuntime
from core.capability_registry import (
    CapabilityRegistry,
    ModelCapability,
    ModelDescriptor,
)
from core.model_router import ModelRouter
from core.policy import Policy
from core.provider_registry import ProviderRegistry
from core.skill_registry import Skill, SkillRegistry
from core.task_planner import ExecutionPlan, PlanStep, TaskPlanner
from core.task_state import StepState, StepStatus, TaskState, TaskStatus
from core.task_state_store import InMemoryTaskStateStore, TaskStateStore
from core.tool_registry import ToolRegistry
from core.workflow_executor import WorkflowExecutor, WorkflowResult
from interfaces.tool import ToolInterface
from interfaces.tool_executor import ToolExecutor
from providers.fake_model import FakeModelProvider


class TrackingTool(ToolInterface):
    def __init__(self, name: str = "tracker"):
        self._name = name
        self.calls = []

    @property
    def name(self) -> str:
        return self._name

    def execute(self, input_data: str) -> str:
        self.calls.append(input_data)
        return f"tracked:{input_data}"


@pytest.fixture
def persistent_workflow_setup():
    cap_reg = CapabilityRegistry()
    prov_reg = ProviderRegistry()
    skill_reg = SkillRegistry()
    tool_reg = ToolRegistry()
    store = InMemoryTaskStateStore()

    p_fake = FakeModelProvider()
    prov_reg.register("fake_prov", p_fake)

    m_desc = ModelDescriptor(
        model_id="fake_model",
        provider_id="fake_prov",
        capabilities={ModelCapability.REASONING},
    )
    cap_reg.register_model(m_desc)

    router = ModelRouter(cap_reg, prov_reg)

    tool = TrackingTool("tracker")
    tool_reg.register("tracker", tool)
    policy = Policy(authorized_tools={"tracker"})
    tool_executor = ToolExecutor(registry=tool_reg, policy=policy)

    execution_counts = {"s1": 0, "s2": 0, "s3": 0}

    def s1_handler(inp):
        execution_counts["s1"] += 1
        return f"s1({inp})"

    def s2_handler(inp):
        execution_counts["s2"] += 1
        return f"s2({inp})"

    def s3_handler(inp):
        execution_counts["s3"] += 1
        return f"s3({inp})"

    skill_reg.register(Skill(name="s1_skill", handler=s1_handler))
    skill_reg.register(Skill(name="s2_skill", handler=s2_handler))
    skill_reg.register(Skill(name="s3_skill", handler=s3_handler))

    runtime = AgentRuntime(
        skill_registry=skill_reg,
        model_router=router,
        tool_executor=tool_executor,
        policy=policy,
    )

    planner = TaskPlanner(skill_reg)
    executor = WorkflowExecutor(runtime=runtime, planner=planner, state_store=store)

    return {
        "store": store,
        "runtime": runtime,
        "planner": planner,
        "executor": executor,
        "counts": execution_counts,
        "skill_reg": skill_reg,
        "tool": tool,
    }


def test_workflow_persists_running_and_completed_states(persistent_workflow_setup):
    setup = persistent_workflow_setup
    planner = setup["planner"]
    executor = setup["executor"]
    store = setup["store"]

    plan = planner.create_plan([
        PlanStep(step_id="step_1", skill_name="s1_skill", input_data="init"),
        PlanStep(step_id="step_2", skill_name="s2_skill", dependencies=("step_1",)),
    ])

    result = executor.execute(plan=plan, task_id="task_123")

    assert result.success is True
    assert result.task_id == "task_123"
    assert result.final_output == "s2(s1(init))"

    # Verify persisted state in store
    persisted = store.get("task_123")
    assert persisted.status == TaskStatus.COMPLETED
    assert persisted.final_output == "s2(s1(init))"
    assert persisted.step_states["step_1"].status == StepStatus.COMPLETED
    assert persisted.step_states["step_2"].status == StepStatus.COMPLETED
    assert persisted.step_states["step_1"].output == "s1(init)"
    assert persisted.step_states["step_2"].output == "s2(s1(init))"


def test_workflow_persists_failed_state(persistent_workflow_setup):
    setup = persistent_workflow_setup
    skill_reg = setup["skill_reg"]
    planner = setup["planner"]
    executor = setup["executor"]
    store = setup["store"]

    def broken_handler(inp):
        raise RuntimeError("Crash in S2")

    skill_reg.register(Skill(name="broken_skill", handler=broken_handler))

    plan = planner.create_plan([
        PlanStep(step_id="s1", skill_name="s1_skill", input_data="start"),
        PlanStep(step_id="s2", skill_name="broken_skill", dependencies=("s1",)),
        PlanStep(step_id="s3", skill_name="s3_skill", dependencies=("s2",)),
    ])

    result = executor.execute(plan=plan, task_id="fail_task")

    assert result.success is False
    assert result.failed_step_id == "s2"
    assert "Crash in S2" in result.error

    persisted = store.get("fail_task")
    assert persisted.status == TaskStatus.FAILED
    assert persisted.failed_step_id == "s2"
    assert persisted.step_states["s1"].status == StepStatus.COMPLETED
    assert persisted.step_states["s2"].status == StepStatus.FAILED
    assert persisted.step_states["s3"].status == StepStatus.SKIPPED


def test_resumption_idempotency_does_not_reexecute_completed_steps(persistent_workflow_setup):
    setup = persistent_workflow_setup
    planner = setup["planner"]
    executor = setup["executor"]
    store = setup["store"]
    counts = setup["counts"]

    plan = planner.create_plan([
        PlanStep(step_id="s1", skill_name="s1_skill", input_data="start"),
        PlanStep(step_id="s2", skill_name="s2_skill", dependencies=("s1",)),
        PlanStep(step_id="s3", skill_name="s3_skill", dependencies=("s2",)),
    ])

    # Simulate an interrupted task where s1 and s2 completed, but process stopped before s3
    task_state = store.create(task_id="resume_task", plan_id=plan.plan_id, plan=plan)
    task_state.status = TaskStatus.RUNNING

    # Step 1 completed
    res1 = AgentResult(success=True, skill_name="s1_skill", output="s1(start)")
    task_state.step_states["s1"].status = StepStatus.COMPLETED
    task_state.step_states["s1"].agent_result = res1
    task_state.step_states["s1"].output = "s1(start)"

    # Step 2 completed
    res2 = AgentResult(success=True, skill_name="s2_skill", output="s2(s1(start))")
    task_state.step_states["s2"].status = StepStatus.COMPLETED
    task_state.step_states["s2"].agent_result = res2
    task_state.step_states["s2"].output = "s2(s1(start))"

    # Step 3 was not started
    task_state.step_states["s3"].status = StepStatus.NOT_STARTED

    store.save(task_state)

    # Resume the workflow
    result = executor.resume(task_id="resume_task")

    assert result.success is True
    assert result.final_output == "s3(s2(s1(start)))"
    assert result.executed_steps == ["s1", "s2", "s3"]

    # Verify IDEMPOTENCY: s1 and s2 were NOT re-executed! Only s3 was called!
    assert counts["s1"] == 0
    assert counts["s2"] == 0
    assert counts["s3"] == 1


def test_resumption_handles_interrupted_running_step(persistent_workflow_setup):
    setup = persistent_workflow_setup
    planner = setup["planner"]
    executor = setup["executor"]
    store = setup["store"]
    counts = setup["counts"]

    plan = planner.create_plan([
        PlanStep(step_id="s1", skill_name="s1_skill", input_data="start"),
        PlanStep(step_id="s2", skill_name="s2_skill", dependencies=("s1",)),
    ])

    # Simulate crash while s2 was RUNNING
    task_state = store.create(task_id="interrupted_task", plan_id=plan.plan_id, plan=plan)
    task_state.status = TaskStatus.RUNNING

    # Step 1 completed
    task_state.step_states["s1"].status = StepStatus.COMPLETED
    task_state.step_states["s1"].agent_result = AgentResult(success=True, skill_name="s1_skill", output="s1(start)")
    task_state.step_states["s1"].output = "s1(start)"

    # Step 2 was RUNNING when process crashed
    task_state.step_states["s2"].status = StepStatus.RUNNING

    store.save(task_state)

    # Resuming should reset s2 to NOT_STARTED and re-run s2 safely
    result = executor.resume(task_id="interrupted_task")

    assert result.success is True
    assert result.final_output == "s2(s1(start))"

    # s1 was skipped (completed), s2 was executed on resume
    assert counts["s1"] == 0
    assert counts["s2"] == 1


def test_resume_preserves_safety_boundaries_and_tool_authorization(persistent_workflow_setup):
    setup = persistent_workflow_setup
    skill_reg = setup["skill_reg"]
    planner = setup["planner"]
    executor = setup["executor"]
    store = setup["store"]
    tool = setup["tool"]

    def tool_handler(inp, context):
        exec_tool = context["tool_executor"]
        return exec_tool.execute("tracker", inp)

    skill_reg.register(Skill(name="tool_skill", tools=("tracker",), handler=tool_handler))

    plan = planner.create_plan([
        PlanStep(step_id="s1", skill_name="s1_skill", input_data="data"),
        PlanStep(step_id="s2", skill_name="tool_skill", dependencies=("s1",)),
    ])

    # Simulate step 1 already completed
    task_state = store.create(task_id="tool_task", plan_id=plan.plan_id, plan=plan)
    task_state.step_states["s1"].status = StepStatus.COMPLETED
    task_state.step_states["s1"].agent_result = AgentResult(success=True, skill_name="s1_skill", output="s1(data)")
    task_state.step_states["s1"].output = "s1(data)"
    store.save(task_state)

    # Resume execution
    result = executor.resume(task_id="tool_task")

    assert result.success is True
    assert result.final_output == "tracked:s1(data)"
    assert tool.calls == ["s1(data)"]


def test_resume_requires_state_store(persistent_workflow_setup):
    setup = persistent_workflow_setup
    runtime = setup["runtime"]
    planner = setup["planner"]

    # Executor without state store
    stateless_executor = WorkflowExecutor(runtime=runtime, planner=planner)

    with pytest.raises(ValueError, match="TaskStateStore is required to resume"):
        stateless_executor.resume("task_1")


def test_resume_rejects_plan_id_mismatch(persistent_workflow_setup):
    setup = persistent_workflow_setup
    planner = setup["planner"]
    executor = setup["executor"]
    store = setup["store"]

    plan1 = planner.create_plan([PlanStep(step_id="s1", skill_name="s1_skill")], plan_id="plan_A")
    plan2 = planner.create_plan([PlanStep(step_id="s1", skill_name="s1_skill")], plan_id="plan_B")

    store.create(task_id="mismatch_task", plan_id=plan1.plan_id, plan=plan1)

    with pytest.raises(ValueError, match="Plan ID mismatch"):
        executor.execute(plan=plan2, task_id="mismatch_task")


def test_workflow_exports_alias():
    from core.workflow import (
        InMemoryTaskStateStore as MemStore,
        StepState as SS,
        StepStatus as SStat,
        TaskState as TS,
        TaskStateStore as TSS,
        TaskStatus as TStat,
        WorkflowExecutor as WE,
        WorkflowResult as WR,
    )

    assert MemStore is InMemoryTaskStateStore
    assert SS is StepState
    assert SStat is StepStatus
    assert TS is TaskState
    assert TSS is TaskStateStore
    assert TStat is TaskStatus
    assert WE is WorkflowExecutor
    assert WR is WorkflowResult
