"""M48 — Bounded Agentic Planning, Execution & Human Approval Test Suite.

Verifies:
1. Plan DAG Creation, Dependency Validation & Cycle Rejection
2. Full Bounded Execution Lifecycle (Plan -> Validate -> Execute -> Observe -> Complete)
3. Human-in-the-Loop Approval Gating & Rejection Handling
4. Resource Budget & Max Limits Enforcement (max_tool_calls, max_duration, max_replans)
5. Execution Cancellation
6. Policy Enforcement Boundary (Planner cannot bypass Policy)
"""

import pytest
from unittest.mock import MagicMock

from core.bounded_planner_executor import (
    BoundedAgenticExecutor,
    BoundedExecutionConfig,
    ExecutionApprovalState,
)
from core.agent_plan import AgentPlan, AgentPlanStep, StepStatus
from core.approval import ApprovalGateway, ApprovalStatus
from core.policy import Policy, PolicyDecision


def test_plan_creation_and_dag_validation():
    """Verify plan creation and DAG acyclicity validation."""
    executor = BoundedAgenticExecutor()

    # Empty goal rejection
    with pytest.raises(ValueError):
        executor.create_plan("   ")

    # Valid plan
    plan = executor.create_plan("Calculate 256 * 1024")
    assert len(plan.steps) == 3
    is_valid, msg = executor.validate_plan(plan)
    assert is_valid
    assert msg == ""

    # Circular dependency detection
    step1 = AgentPlanStep(step_id="s1", skill_name="a", dependencies=("s2",))
    step2 = AgentPlanStep(step_id="s2", skill_name="b", dependencies=("s1",))
    bad_plan = AgentPlan(steps=(step1, step2))
    is_valid, msg = executor.validate_plan(bad_plan)
    assert not is_valid
    assert "circular dependency" in msg


def test_bounded_execution_lifecycle():
    """Verify standard bounded execution lifecycle completes successfully."""
    executor = BoundedAgenticExecutor()
    result = executor.run(goal="Evaluate 128 + 256", user_id="user_123")

    assert result.status == ExecutionApprovalState.SUCCEEDED
    assert result.steps_executed >= 2
    assert result.duration_seconds > 0.0
    assert result.trace is not None
    assert len(result.trace.observations) >= 2


def test_human_approval_workflow_gating():
    """Verify sensitive tools pause in AWAITING_APPROVAL and resume on approval."""
    gateway = ApprovalGateway(sensitive_tools={"calculator"})
    executor = BoundedAgenticExecutor(approval_gateway=gateway)

    # 1. Run goal needing calculator -> should pause in AWAITING_APPROVAL
    result = executor.run(goal="Compute 500 * 20", user_id="user_123", task_id="task_calc_123")
    assert result.status == ExecutionApprovalState.AWAITING_APPROVAL
    assert len(result.approval_requests) == 1

    app_req = result.approval_requests[0]
    assert app_req.status == ApprovalStatus.PENDING

    # 2. Explicitly approve
    gateway.approve(app_req.approval_id)
    assert app_req.status == ApprovalStatus.APPROVED

    # 3. Re-run with approved gateway -> completes successfully
    res2 = executor.run(goal="Compute 500 * 20", user_id="user_123", task_id="task_calc_123")
    assert res2.status == ExecutionApprovalState.SUCCEEDED


def test_bounded_resource_budget_limits():
    """Verify strict resource budget limits are enforced."""
    cfg = BoundedExecutionConfig(max_tool_calls=0)  # zero tool calls allowed
    executor = BoundedAgenticExecutor(config=cfg)

    result = executor.run(goal="Compute 100 * 50", user_id="user_123")
    assert result.status == ExecutionApprovalState.FAILED
    assert "max tool calls limit" in result.final_output


def test_cancellation_support():
    """Verify execution cancellation."""
    executor = BoundedAgenticExecutor()
    cancelled = True
    result = executor.run(goal="Compute 10 + 20", user_id="user_123", cancellation_requested=lambda: cancelled)

    assert result.status == ExecutionApprovalState.CANCELLED
    assert "cancelled" in result.final_output


def test_planner_is_not_security_boundary():
    """Verify policy engine denial blocks step execution regardless of plan."""
    mock_policy = MagicMock(spec=Policy)
    mock_policy.authorize_tool.return_value = PolicyDecision.DENY
    mock_policy.evaluate.return_value = PolicyDecision.DENY

    executor = BoundedAgenticExecutor(policy_engine=mock_policy)
    result = executor.run(goal="Analyze data", user_id="user_123")

    assert result.status == ExecutionApprovalState.FAILED
