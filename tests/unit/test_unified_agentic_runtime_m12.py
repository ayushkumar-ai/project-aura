import json
import pytest
from uuid import UUID, uuid4
from core.agent_plan import AgentPlan, AgentPlanStep, StepStatus
from core.agent_runtime import AgentRuntime
from core.agentic_runtime import AgenticRuntime, ExecutionMode
from core.approval import ApprovalGateway, ApprovalStatus
from core.autonomous_agent import AutonomousAgentExecutor, AutonomousAgentResult
from core.goal import Goal, GoalStatus
from core.goal_engine import GoalEngine
from core.goal_reasoner import GoalEvaluationResult
from core.goal_store import InMemoryGoalStore
from core.models import AURARequest, AURAResponse
from core.policy import Policy
from core.provenance import wrap_tainted, is_tainted, unwrap_tainted
from core.skill_registry import Skill, SkillRegistry
from core.task_planner import ExecutionPlan, PlanStep
from core.task_state_store import InMemoryTaskStateStore
from core.workflow_executor import WorkflowResult
from interfaces.model import ModelInterface


class PlanModel(ModelInterface):
    def generate(self, prompt: str, request_id: UUID) -> AURAResponse:
        plan_json = json.dumps({
            "steps": [
                {
                    "step_id": "step_1",
                    "skill_name": "echo_skill",
                    "description": "Echo input",
                    "input_data": {"text": "hello"},
                    "dependencies": [],
                }
            ]
        })
        return AURAResponse(
            request_id=request_id,
            content=plan_json,
        )


def _create_runtime():
    skill_reg = SkillRegistry()
    skill_reg.register(
        Skill(
            name="echo_skill",
            description="echo text",
            handler=lambda inp: wrap_tainted(f"echoed: {unwrap_tainted(inp)}", is_untrusted=True) if is_tainted(inp) else f"echoed: {inp}",
        )
    )
    skill_reg.register(
        Skill(
            name="sensitive_skill",
            description="sensitive action",
            handler=lambda inp: f"sensitive_done: {inp}",
        )
    )

    policy = Policy()
    approval_gateway = ApprovalGateway(
        policy=policy,
        skill_registry=skill_reg,
        sensitive_skills={"sensitive_skill"},
    )
    state_store = InMemoryTaskStateStore()
    goal_store = InMemoryGoalStore()
    model = PlanModel()

    runtime = AgenticRuntime(
        skill_registry=skill_reg,
        policy=policy,
        approval_gateway=approval_gateway,
        state_store=state_store,
        goal_store=goal_store,
        model=model,
    )
    return runtime, approval_gateway, goal_store, state_store


def test_unified_runtime_standard_workflow_mode():
    runtime, _, _, _ = _create_runtime()

    res = runtime.execute(
        task="Test simple workflow",
        mode=ExecutionMode.STANDARD_WORKFLOW,
    )
    assert isinstance(res, WorkflowResult)
    assert res.success or res.error is not None


def test_unified_runtime_autonomous_agent_mode():
    runtime, _, _, _ = _create_runtime()

    res = runtime.execute(
        task="Test autonomous task",
        mode=ExecutionMode.AUTONOMOUS_AGENT,
    )
    assert isinstance(res, AutonomousAgentResult)
    assert res.task_id is not None
    assert isinstance(res.plan, AgentPlan)


def test_unified_runtime_goal_driven_mode():
    runtime, _, goal_store, _ = _create_runtime()

    res = runtime.execute(
        task="Maintain system health",
        mode=ExecutionMode.GOAL_DRIVEN,
    )
    assert isinstance(res, GoalEvaluationResult)
    assert res.new_progress is not None
    assert len(goal_store.list_goals()) == 1


def test_unified_runtime_auto_mode_detection():
    runtime, _, _, _ = _create_runtime()

    # AgentPlan -> AUTONOMOUS_AGENT
    plan = AgentPlan(
        plan_id="plan_1",
        task_goal="Run plan",
        steps=(
            AgentPlanStep(
                step_id="s1",
                skill_name="echo_skill",
                objective="Echo text",
                input_data={"test": "hello"},
            ),
        ),
    )
    res_plan = runtime.execute(plan)
    assert isinstance(res_plan, AutonomousAgentResult)
    assert res_plan.success

    # Goal -> GOAL_DRIVEN
    goal = Goal(title="Direct Goal Object", success_criteria=("done",))
    res_goal = runtime.execute(goal)
    assert isinstance(res_goal, GoalEvaluationResult)


def test_unified_runtime_execute_autonomous_method():
    runtime, _, _, state_store = _create_runtime()

    res = runtime.execute_autonomous("Run autonomous task", task_id="task_auto_1")
    assert isinstance(res, AutonomousAgentResult)
    assert res.task_id == "task_auto_1"
    assert state_store.exists("task_auto_1")


def test_unified_runtime_execute_goal_with_subgoals():
    runtime, _, goal_store, _ = _create_runtime()

    root_goal = runtime.goal_engine.create_goal(
        title="Root System Goal",
        success_criteria=("complete_system",),
    )
    root_id = root_goal.goal_id

    sub_eval = runtime.execute_goal(
        goal="Child Subgoal",
        parent_goal_id=root_id,
    )
    assert isinstance(sub_eval, GoalEvaluationResult)
    subgoals = goal_store.get_subgoals(root_id)
    assert len(subgoals) == 1
    assert subgoals[0].parent_goal_id == root_id


def test_unified_runtime_approval_and_policy_invariance():
    runtime, approval_gateway, _, _ = _create_runtime()

    # Execute plan requiring approval in autonomous mode
    plan = AgentPlan(
        plan_id="plan_sensitive",
        task_goal="Run sensitive operation",
        steps=(
            AgentPlanStep(
                step_id="step_sens",
                skill_name="sensitive_skill",
                objective="Sensitive step",
                input_data={"target": "database"},
            ),
        ),
    )

    res = runtime.execute_autonomous(plan, task_id="task_sens_1")
    assert isinstance(res, AutonomousAgentResult)
    # Must pause or require approval
    assert res.is_paused or not res.success
    assert res.approval_request is not None or "approval" in (res.error or "").lower()


def test_unified_runtime_taint_propagation():
    runtime, _, _, _ = _create_runtime()

    tainted_input = wrap_tainted("untrusted text", is_untrusted=True, source_urls=("https://untrusted.com",))
    plan = AgentPlan(
        plan_id="plan_tainted",
        task_goal="Process tainted data",
        steps=(
            AgentPlanStep(
                step_id="step_taint",
                skill_name="echo_skill",
                objective="Echo tainted data",
                input_data=tainted_input,
            ),
        ),
    )

    res = runtime.execute_autonomous(plan, task_id="task_taint_1")
    assert isinstance(res, AutonomousAgentResult)
    assert res.success
    # Output must retain taint
    assert is_tainted(res.final_output)


def test_unified_runtime_backward_compatibility_run_request():
    runtime, _, _, _ = _create_runtime()

    req = AURARequest(user_input="Hello from legacy request")
    resp = runtime.run_request(req)
    assert isinstance(resp, AURAResponse)
    assert resp.request_id == req.request_id
