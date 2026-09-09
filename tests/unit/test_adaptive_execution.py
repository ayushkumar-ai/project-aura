import json
import pytest
from uuid import UUID

from core.agent_runtime import AgentResult, AgentRuntime
from core.approval import ApprovalGateway, ApprovalStatus
from core.capability_registry import CapabilityRegistry, ModelCapability, ModelDescriptor
from core.model_router import ModelRouter
from core.models import AURAResponse
from core.policy import Policy
from core.provider_registry import ProviderRegistry
from core.skill_registry import Skill, SkillRegistry
from core.task_planner import ExecutionPlan, PlanStep, ReplanContext, TaskPlanner
from core.task_state import StepStatus, TaskStatus
from core.task_state_store import InMemoryTaskStateStore
from core.tool_registry import ToolRegistry
from core.workflow_executor import WorkflowExecutor, WorkflowResult, is_recoverable_failure
from interfaces.model import ModelInterface
from interfaces.tool import ToolInterface
from interfaces.tool_executor import ToolExecutor


class MockTool(ToolInterface):
    def __init__(self, name: str = "mock"):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def execute(self, input_data: str) -> str:
        return f"tool_out:{input_data}"


class ProgrammableModel(ModelInterface):
    """Test model double supporting sequential canned responses."""

    def __init__(self, responses: list[str] | None = None):
        self.responses = list(responses) if responses is not None else []
        self.last_prompt = ""
        self.calls = 0

    def generate(self, prompt: str, request_id: UUID) -> AURAResponse:
        self.last_prompt = prompt
        self.calls += 1
        if not self.responses:
            return AURAResponse(request_id=request_id, content="{}")
        content = self.responses.pop(0)
        if content == "RAISE_ERROR":
            raise RuntimeError("Model generation error")
        return AURAResponse(request_id=request_id, content=content)


@pytest.fixture
def adaptive_setup():
    cap_reg = CapabilityRegistry()
    prov_reg = ProviderRegistry()
    skill_reg = SkillRegistry()
    tool_reg = ToolRegistry()
    tool_reg.register("echo", MockTool("echo"))
    tool_reg.register("unauthorized_tool", MockTool("unauthorized_tool"))
    store = InMemoryTaskStateStore()

    p_model = ProgrammableModel()
    prov_reg.register("prov", p_model)
    cap_reg.register_model(ModelDescriptor(model_id="m1", provider_id="prov", capabilities={ModelCapability.REASONING}))
    router = ModelRouter(cap_reg, prov_reg)

    policy = Policy(authorized_tools={"echo"})
    tool_executor = ToolExecutor(registry=tool_reg, policy=policy)

    execution_counts = {"fetch": 0, "failing": 0, "backup": 0, "report": 0}

    def fetch_handler(inp):
        execution_counts["fetch"] += 1
        return "data_payload"

    def failing_handler(inp):
        execution_counts["failing"] += 1
        raise RuntimeError("External API timeout")

    def backup_handler(inp):
        execution_counts["backup"] += 1
        return f"backup({inp})"

    def report_handler(inp):
        execution_counts["report"] += 1
        return f"report({inp})"

    skill_reg.register(Skill(name="fetch_skill", handler=fetch_handler))
    skill_reg.register(Skill(name="failing_skill", handler=failing_handler))
    skill_reg.register(Skill(name="backup_skill", handler=backup_handler))
    skill_reg.register(Skill(name="report_skill", handler=report_handler))

    runtime = AgentRuntime(
        skill_registry=skill_reg,
        model_router=router,
        tool_executor=tool_executor,
        policy=policy,
    )

    planner = TaskPlanner(skill_registry=skill_reg, model=p_model)

    return {
        "store": store,
        "runtime": runtime,
        "planner": planner,
        "model": p_model,
        "counts": execution_counts,
        "skill_reg": skill_reg,
        "policy": policy,
    }


def test_successful_workflow_does_not_trigger_replanning(adaptive_setup):
    setup = adaptive_setup
    runtime = setup["runtime"]
    planner = setup["planner"]
    store = setup["store"]
    model = setup["model"]
    counts = setup["counts"]

    plan = planner.create_plan([
        PlanStep(step_id="s1", skill_name="fetch_skill"),
        PlanStep(step_id="s2", skill_name="report_skill", dependencies=("s1",)),
    ])

    executor = WorkflowExecutor(
        runtime=runtime,
        planner=planner,
        state_store=store,
        max_replans=2,
    )

    result = executor.execute(plan=plan, task_id="task_success")
    assert result.success is True
    assert result.final_output == "report(data_payload)"
    assert model.calls == 0  # Re-planning was never invoked
    assert counts["fetch"] == 1
    assert counts["report"] == 1


def test_recoverable_failure_triggers_replanning_and_preserves_completed_work(adaptive_setup):
    setup = adaptive_setup
    runtime = setup["runtime"]
    planner = setup["planner"]
    store = setup["store"]
    model = setup["model"]
    counts = setup["counts"]

    # Initial plan: fetch -> failing -> report
    plan = planner.create_plan([
        PlanStep(step_id="s1", skill_name="fetch_skill"),
        PlanStep(step_id="s2", skill_name="failing_skill", dependencies=("s1",)),
        PlanStep(step_id="s3", skill_name="report_skill", dependencies=("s2",)),
    ])

    # Model provides replacement plan using backup_skill instead of failing_skill
    canned_replacement = {
        "steps": [
            {"step_id": "s1", "skill_name": "fetch_skill"},
            {"step_id": "s2_alt", "skill_name": "backup_skill", "dependencies": ["s1"]},
            {"step_id": "s3", "skill_name": "report_skill", "dependencies": ["s2_alt"]},
        ]
    }
    model.responses = [json.dumps(canned_replacement)]

    executor = WorkflowExecutor(
        runtime=runtime,
        planner=planner,
        state_store=store,
        max_replans=1,
        task_description="Fetch data and generate report",
    )

    result = executor.execute(plan=plan, task_id="task_adapted")

    # Workflow succeeded after adaptation
    assert result.success is True
    assert result.final_output == "report(backup(data_payload))"
    assert model.calls == 1  # Planner replan was called

    # IDEMPOTENCY INVARIANT: s1 was executed ONCE (not replayed on replan)
    assert counts["fetch"] == 1
    assert counts["failing"] == 1
    assert counts["backup"] == 1
    assert counts["report"] == 1

    # Task state verified
    task_state = store.get("task_adapted")
    assert task_state.status == TaskStatus.COMPLETED
    assert task_state.metadata["replan_count"] == 1
    assert task_state.step_states["s1"].status == StepStatus.COMPLETED
    assert task_state.step_states["s2_alt"].status == StepStatus.COMPLETED
    assert task_state.step_states["s3"].status == StepStatus.COMPLETED


def test_non_recoverable_policy_denial_does_not_trigger_replanning(adaptive_setup):
    setup = adaptive_setup
    runtime = setup["runtime"]
    planner = setup["planner"]
    store = setup["store"]
    model = setup["model"]
    counts = setup["counts"]
    skill_reg = setup["skill_reg"]

    # Register a rogue skill that fails due to Policy denial
    def rogue_handler(inp, context):
        exec_tool = context["tool_executor"]
        return exec_tool.execute("unauthorized_tool", inp)

    skill_reg.register(Skill(name="rogue_skill", handler=rogue_handler))

    plan = planner.create_plan([
        PlanStep(step_id="s1", skill_name="fetch_skill"),
        PlanStep(step_id="s2", skill_name="rogue_skill", dependencies=("s1",)),
    ])

    executor = WorkflowExecutor(
        runtime=runtime,
        planner=planner,
        state_store=store,
        max_replans=2,
    )

    result = executor.execute(plan=plan, task_id="task_policy_deny")

    # Policy denial is non-recoverable -> no re-planning triggered
    assert result.success is False
    assert model.calls == 0
    assert result.failed_step_id == "s2"
    assert "not authorized" in result.error

    task_state = store.get("task_policy_deny")
    assert task_state.status == TaskStatus.FAILED


def test_replan_limit_prevents_infinite_loops(adaptive_setup):
    setup = adaptive_setup
    runtime = setup["runtime"]
    planner = setup["planner"]
    store = setup["store"]
    model = setup["model"]
    counts = setup["counts"]

    # Initial plan with failing skill
    plan = planner.create_plan([
        PlanStep(step_id="s1", skill_name="failing_skill"),
    ])

    # Model returns another plan that also uses failing_skill
    canned_replacement = {
        "steps": [{"step_id": "s1_retry", "skill_name": "failing_skill"}]
    }
    model.responses = [json.dumps(canned_replacement), json.dumps(canned_replacement)]

    executor = WorkflowExecutor(
        runtime=runtime,
        planner=planner,
        state_store=store,
        max_replans=1,  # Only 1 replan allowed
    )

    result = executor.execute(plan=plan, task_id="task_limit")

    assert result.success is False
    assert model.calls == 1  # Exactly 1 replan was attempted, then stopped
    assert counts["failing"] == 2  # initial + 1 replan attempt

    task_state = store.get("task_limit")
    assert task_state.status == TaskStatus.FAILED
    assert task_state.metadata["replan_count"] == 1


def test_invalid_replacement_plan_fails_safely(adaptive_setup):
    setup = adaptive_setup
    runtime = setup["runtime"]
    planner = setup["planner"]
    store = setup["store"]
    model = setup["model"]

    plan = planner.create_plan([
        PlanStep(step_id="s1", skill_name="failing_skill"),
    ])

    # Model returns malformed JSON during replanning
    model.responses = ["Malformed JSON output"]

    executor = WorkflowExecutor(
        runtime=runtime,
        planner=planner,
        state_store=store,
        max_replans=1,
    )

    result = executor.execute(plan=plan, task_id="task_malformed_replan")

    assert result.success is False
    assert "Re-planning failed" in result.error
    assert result.metadata.get("replan_failed") is True

    task_state = store.get("task_malformed_replan")
    assert task_state.status == TaskStatus.FAILED


def test_unknown_skill_in_replacement_plan_is_rejected(adaptive_setup):
    setup = adaptive_setup
    runtime = setup["runtime"]
    planner = setup["planner"]
    store = setup["store"]
    model = setup["model"]

    plan = planner.create_plan([
        PlanStep(step_id="s1", skill_name="failing_skill"),
    ])

    # Model proposes unregistered skill
    canned = {
        "steps": [{"step_id": "s1_alt", "skill_name": "nonexistent_skill"}]
    }
    model.responses = [json.dumps(canned)]

    executor = WorkflowExecutor(
        runtime=runtime,
        planner=planner,
        state_store=store,
        max_replans=1,
    )

    result = executor.execute(plan=plan, task_id="task_unknown_replan")
    assert result.success is False
    assert "Unknown skill 'nonexistent_skill'" in result.error


def test_circular_dependency_in_replacement_plan_is_rejected(adaptive_setup):
    setup = adaptive_setup
    runtime = setup["runtime"]
    planner = setup["planner"]
    store = setup["store"]
    model = setup["model"]

    plan = planner.create_plan([
        PlanStep(step_id="s1", skill_name="failing_skill"),
    ])

    # Model proposes circular dependency
    canned = {
        "steps": [
            {"step_id": "s_a", "skill_name": "fetch_skill", "dependencies": ["s_b"]},
            {"step_id": "s_b", "skill_name": "report_skill", "dependencies": ["s_a"]},
        ]
    }
    model.responses = [json.dumps(canned)]

    executor = WorkflowExecutor(
        runtime=runtime,
        planner=planner,
        state_store=store,
        max_replans=1,
    )

    result = executor.execute(plan=plan, task_id="task_circular_replan")
    assert result.success is False
    assert "Circular dependency detected" in result.error


def test_new_sensitive_actions_in_replacement_plan_require_fresh_approval(adaptive_setup):
    setup = adaptive_setup
    runtime = setup["runtime"]
    planner = setup["planner"]
    store = setup["store"]
    model = setup["model"]
    policy = setup["policy"]
    skill_reg = setup["skill_reg"]

    # Gateway classifies backup_skill as sensitive
    gateway = ApprovalGateway(
        policy=policy,
        skill_registry=skill_reg,
        sensitive_skills={"backup_skill"},
    )

    plan = planner.create_plan([
        PlanStep(step_id="s1", skill_name="fetch_skill"),
        PlanStep(step_id="s2", skill_name="failing_skill", dependencies=("s1",)),
    ])

    # Model proposes backup_skill
    canned = {
        "steps": [
            {"step_id": "s1", "skill_name": "fetch_skill"},
            {"step_id": "s2_alt", "skill_name": "backup_skill", "dependencies": ["s1"]},
        ]
    }
    model.responses = [json.dumps(canned)]

    executor = WorkflowExecutor(
        runtime=runtime,
        planner=planner,
        state_store=store,
        approval_gateway=gateway,
        max_replans=1,
    )

    # 1. Execute: fetch succeeds, failing fails, replan proposes backup_skill,
    # and gateway PAUSES workflow because backup_skill requires approval!
    result = executor.execute(plan=plan, task_id="task_sens_replan")

    assert result.success is False
    assert result.failed_step_id == "s2_alt"
    assert result.approval_request is not None
    assert result.approval_request.skill_name == "backup_skill"

    # Task state is PAUSED
    task_state = store.get("task_sens_replan")
    assert task_state.status == TaskStatus.PAUSED
    assert task_state.step_states["s1"].status == StepStatus.COMPLETED

    # 2. Approve the new sensitive action
    gateway.approve(result.approval_request.approval_id)

    # 3. Resume workflow -> backup_skill executes, s1 not re-executed
    resume_res = executor.resume(task_id="task_sens_replan")
    assert resume_res.success is True
    assert resume_res.final_output == "backup(data_payload)"


def test_replan_context_validation():
    p = ExecutionPlan(steps=(PlanStep(step_id="s1", skill_name="s"),), plan_id="p1")

    with pytest.raises(ValueError, match="task must be a non-empty string"):
        ReplanContext(task="", failed_step_id="s1", error_message="err")

    with pytest.raises(ValueError, match="failed_step_id must be a non-empty string"):
        ReplanContext(task="task", failed_step_id="  ", error_message="err")

    with pytest.raises(TypeError, match="error_message must be a string"):
        ReplanContext(task="task", failed_step_id="s1", error_message=123)

    with pytest.raises(TypeError, match="completed_steps must be a sequence"):
        ReplanContext(task="task", failed_step_id="s1", error_message="err", completed_steps=123)


def test_is_recoverable_failure_helper():
    assert is_recoverable_failure(error="Connection timeout") is True
    assert is_recoverable_failure(error="HTTP 500 Internal Error") is True
    assert is_recoverable_failure(error="Tool 'bash' is not authorized by policy") is False
    assert is_recoverable_failure(error="PermissionError: access denied") is False
    assert is_recoverable_failure(error="Approval rejected: security review denied") is False
    assert is_recoverable_failure(error=None) is True
