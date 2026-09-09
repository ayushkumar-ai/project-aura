import pytest
from core.agent_runtime import AgentResult, AgentRuntime
from core.approval import (
    ApprovalDecision,
    ApprovalDecisionType,
    ApprovalGateway,
    ApprovalRequest,
    ApprovalStatus,
)
from core.capability_registry import CapabilityRegistry, ModelCapability, ModelDescriptor
from core.model_router import ModelRouter
from core.policy import Policy
from core.provider_registry import ProviderRegistry
from core.skill_registry import Skill, SkillRegistry
from core.task_planner import ExecutionPlan, PlanStep, TaskPlanner
from core.task_state import StepStatus, TaskStatus
from core.task_state_store import InMemoryTaskStateStore
from core.tool_registry import ToolRegistry
from core.workflow_executor import WorkflowExecutor, WorkflowResult
from interfaces.tool import ToolInterface
from interfaces.tool_executor import ToolExecutor
from providers.fake_model import FakeModelProvider


class EchoTool(ToolInterface):
    def __init__(self, name: str = "echo"):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def execute(self, input_data: str) -> str:
        return f"echo:{input_data}"


@pytest.fixture
def workflow_approval_setup():
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

    echo_tool = EchoTool("echo")
    tool_reg.register("echo", echo_tool)
    policy = Policy(authorized_tools={"echo"})
    tool_executor = ToolExecutor(registry=tool_reg, policy=policy)

    calls = {"safe": 0, "sensitive": 0, "downstream": 0}

    def safe_handler(inp):
        calls["safe"] += 1
        return f"safe({inp})"

    def sensitive_handler(inp):
        calls["sensitive"] += 1
        return f"sensitive({inp})"

    def downstream_handler(inp):
        calls["downstream"] += 1
        return f"downstream({inp})"

    skill_reg.register(Skill(name="safe_skill", handler=safe_handler))
    skill_reg.register(Skill(name="sensitive_skill", handler=sensitive_handler))
    skill_reg.register(Skill(name="downstream_skill", handler=downstream_handler))

    runtime = AgentRuntime(
        skill_registry=skill_reg,
        model_router=router,
        tool_executor=tool_executor,
        policy=policy,
    )

    gateway = ApprovalGateway(
        policy=policy,
        skill_registry=skill_reg,
        sensitive_skills={"sensitive_skill"},
    )

    planner = TaskPlanner(skill_reg)
    executor = WorkflowExecutor(
        runtime=runtime,
        planner=planner,
        state_store=store,
        approval_gateway=gateway,
    )

    return {
        "store": store,
        "runtime": runtime,
        "planner": planner,
        "gateway": gateway,
        "executor": executor,
        "calls": calls,
        "policy": policy,
        "tool_executor": tool_executor,
    }


def test_safe_workflow_executes_normally_with_gateway(workflow_approval_setup):
    setup = workflow_approval_setup
    planner = setup["planner"]
    executor = setup["executor"]
    calls = setup["calls"]

    plan = planner.create_plan([
        PlanStep(step_id="s1", skill_name="safe_skill", input_data="data"),
    ])

    result = executor.execute(plan=plan, task_id="task_safe")
    assert result.success is True
    assert result.final_output == "safe(data)"
    assert calls["safe"] == 1


def test_approval_required_workflow_pauses_and_persists_state(workflow_approval_setup):
    setup = workflow_approval_setup
    planner = setup["planner"]
    executor = setup["executor"]
    store = setup["store"]
    calls = setup["calls"]

    plan = planner.create_plan([
        PlanStep(step_id="s1", skill_name="safe_skill", input_data="start"),
        PlanStep(step_id="s2", skill_name="sensitive_skill", dependencies=("s1",)),
        PlanStep(step_id="s3", skill_name="downstream_skill", dependencies=("s2",)),
    ])

    result = executor.execute(plan=plan, task_id="task_gated")

    # Workflow paused awaiting approval for s2
    assert result.success is False
    assert result.failed_step_id == "s2"
    assert result.approval_request is not None
    assert result.approval_request.skill_name == "sensitive_skill"
    assert result.approval_request.is_pending() is True

    # Step 1 executed, Step 2 and 3 did not
    assert calls["safe"] == 1
    assert calls["sensitive"] == 0
    assert calls["downstream"] == 0

    # Task state is PAUSED
    task_state = store.get("task_gated")
    assert task_state.status == TaskStatus.PAUSED
    assert task_state.step_states["s1"].status == StepStatus.COMPLETED
    assert task_state.step_states["s2"].status == StepStatus.NOT_STARTED


def test_approved_workflow_resumes_without_reexecuting_completed_steps(workflow_approval_setup):
    setup = workflow_approval_setup
    planner = setup["planner"]
    executor = setup["executor"]
    gateway = setup["gateway"]
    store = setup["store"]
    calls = setup["calls"]

    plan = planner.create_plan([
        PlanStep(step_id="s1", skill_name="safe_skill", input_data="start"),
        PlanStep(step_id="s2", skill_name="sensitive_skill", dependencies=("s1",)),
        PlanStep(step_id="s3", skill_name="downstream_skill", dependencies=("s2",)),
    ])

    # 1. Execute until paused
    res1 = executor.execute(plan=plan, task_id="task_approve_resume")
    assert res1.success is False
    app_id = res1.approval_request.approval_id

    # 2. Approve the request
    gateway.approve(app_id)

    # 3. Resume workflow
    res2 = executor.resume(task_id="task_approve_resume")
    assert res2.success is True
    assert res2.final_output == "downstream(sensitive(safe(start)))"

    # IDEMPOTENCY: s1 executed once, s2 executed once, s3 executed once
    assert calls["safe"] == 1
    assert calls["sensitive"] == 1
    assert calls["downstream"] == 1

    # Task state completed
    task_state = store.get("task_approve_resume")
    assert task_state.status == TaskStatus.COMPLETED


def test_rejected_workflow_does_not_execute_protected_step(workflow_approval_setup):
    setup = workflow_approval_setup
    planner = setup["planner"]
    executor = setup["executor"]
    gateway = setup["gateway"]
    store = setup["store"]
    calls = setup["calls"]

    plan = planner.create_plan([
        PlanStep(step_id="s1", skill_name="safe_skill", input_data="start"),
        PlanStep(step_id="s2", skill_name="sensitive_skill", dependencies=("s1",)),
        PlanStep(step_id="s3", skill_name="downstream_skill", dependencies=("s2",)),
    ])

    # 1. Execute until paused
    res1 = executor.execute(plan=plan, task_id="task_reject_resume")
    app_id = res1.approval_request.approval_id

    # 2. Reject the request
    gateway.reject(app_id, reason="Denied by admin")

    # 3. Resume workflow
    res2 = executor.resume(task_id="task_reject_resume")
    assert res2.success is False
    assert res2.failed_step_id == "s2"
    assert "Denied by admin" in res2.error

    # Sensitive and downstream steps NEVER executed
    assert calls["safe"] == 1
    assert calls["sensitive"] == 0
    assert calls["downstream"] == 0

    # Task state is FAILED, s2 is FAILED, s3 is SKIPPED
    task_state = store.get("task_reject_resume")
    assert task_state.status == TaskStatus.FAILED
    assert task_state.step_states["s1"].status == StepStatus.COMPLETED
    assert task_state.step_states["s2"].status == StepStatus.FAILED
    assert task_state.step_states["s3"].status == StepStatus.SKIPPED


def test_approval_does_not_bypass_policy_or_tool_executor(workflow_approval_setup):
    setup = workflow_approval_setup
    planner = setup["planner"]
    executor = setup["executor"]
    gateway = setup["gateway"]
    store = setup["store"]
    skill_reg = setup["runtime"].skill_registry

    # Define a handler that attempts to execute an unauthorized tool 'unauthorized_cmd'
    def rogue_handler(inp, context):
        exec_tool = context["tool_executor"]
        return exec_tool.execute("unauthorized_cmd", inp)

    skill_reg.register(Skill(name="rogue_skill", handler=rogue_handler))

    # Gateway requires approval for rogue_skill
    gateway.sensitive_skills.add("rogue_skill")

    plan = planner.create_plan([
        PlanStep(step_id="s1", skill_name="rogue_skill", input_data="cmd"),
    ])

    # 1. Execute -> paused for approval
    res1 = executor.execute(plan=plan, task_id="task_rogue")
    app_id = res1.approval_request.approval_id

    # 2. Human approves the gateway request
    gateway.approve(app_id)

    # 3. Resuming execution attempts to run the step, but ToolExecutor / Policy DENIES the tool!
    res2 = executor.resume(task_id="task_rogue")
    assert res2.success is False
    # Verified: Approval did NOT bypass ToolExecutor / Policy!
    assert "not authorized" in res2.error or "Unknown tool" in res2.error


def test_workflow_executor_gateway_validation(workflow_approval_setup):
    setup = workflow_approval_setup
    runtime = setup["runtime"]
    planner = setup["planner"]

    with pytest.raises(TypeError, match="approval_gateway must be an instance of ApprovalGateway"):
        WorkflowExecutor(runtime=runtime, planner=planner, approval_gateway="invalid")


def test_workflow_exports_aliases():
    from core.workflow import (
        ApprovalDecision as AD,
        ApprovalDecisionType as ADT,
        ApprovalGateway as AG,
        ApprovalRequest as AR,
        ApprovalStatus as AS,
        InMemoryTaskStateStore as IMTSS,
        StepState as SS,
        StepStatus as SStat,
        TaskState as TS,
        TaskStateStore as TSS,
        TaskStatus as TStat,
        WorkflowExecutor as WE,
        WorkflowResult as WR,
    )

    assert AG is ApprovalGateway
    assert AR is ApprovalRequest
    assert AD is ApprovalDecision
    assert ADT is ApprovalDecisionType
    assert AS is ApprovalStatus
    assert WE is WorkflowExecutor
    assert WR is WorkflowResult
