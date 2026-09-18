"""M59 — Structured Planner & Policy Validator Unit Tests."""

from unittest.mock import MagicMock
import pytest
from core.agent_mesh.context import AgentContext
from core.agent_mesh.intent import ClassifiedIntent
from core.agent_mesh.planner import StructuredPlanner
from core.agent_mesh.types import ActionType, ExecutionPlan, PlanStep
from core.agent_mesh.validator import PlanValidator
from core.platform.types import CapabilityRiskLevel


class TestPlannerAndValidatorUnit:
    def test_structured_planner_with_model_gateway(self):
        # Invariant M59-F12
        mock_mg = MagicMock()
        mock_response = MagicMock()
        mock_response.text = '''
        {
          "steps": [
            {
              "step_number": 1,
              "action_type": "tool",
              "action_name": "calculator",
              "parameters": {"expr": "42 * 2"},
              "risk_level": "low",
              "requires_approval": false,
              "description": "Calculate product"
            }
          ]
        }
        '''
        mock_mg.generate.return_value = mock_response

        planner = StructuredPlanner(model_gateway=mock_mg)
        intent = ClassifiedIntent(raw_intent="Calculate 42 * 2", cleaned_goal="Calculate 42 * 2")
        context = AgentContext(tenant_id="tenant_1", user_id="user_1")

        plan = planner.plan(tenant_id="tenant_1", intent=intent, context=context)
        assert len(plan.steps) == 1
        assert plan.steps[0].action_type == ActionType.TOOL
        assert plan.steps[0].action_name == "calculator"
        assert plan.steps[0].parameters == {"expr": "42 * 2"}

    def test_structured_planner_deterministic_fallback(self):
        # Invariant M59-F15
        planner = StructuredPlanner(model_gateway=None)
        intent = ClassifiedIntent(
            raw_intent="Check local filesystem",
            cleaned_goal="Check local filesystem",
            requires_device_interaction=True,
        )
        context = AgentContext(tenant_id="tenant_1", user_id="user_1")

        plan = planner.plan(tenant_id="tenant_1", intent=intent, context=context)
        assert len(plan.steps) > 0
        assert plan.goal == "Check local filesystem"

    def test_plan_validator_empty_and_invalid_plans(self):
        # Invariant M59-F12
        validator = PlanValidator()

        with pytest.raises(ValueError, match="Execution plan must contain at least one step"):
            validator.validate_plan("tenant_1", ExecutionPlan(goal="Empty", steps=[]))

        invalid_step_plan = ExecutionPlan(
            goal="Invalid step",
            steps=[PlanStep(step_number=1, action_type=ActionType.TOOL, action_name="")]
        )
        with pytest.raises(ValueError, match="missing an action name"):
            validator.validate_plan("tenant_1", invalid_step_plan)

    def test_plan_validator_high_risk_requires_approval(self):
        # Invariants M59-F08, M59-F09, M59-F21
        validator = PlanValidator()

        high_risk_step = PlanStep(
            step_number=1,
            action_type=ActionType.DEVICE,
            action_name="delete_sandboxed_file",
            risk_level=CapabilityRiskLevel.HIGH,
            requires_approval=True,
        )

        with pytest.raises(PermissionError, match="requires a valid M48 human approval token"):
            validator.validate_step_pre_execution("tenant_1", high_risk_step, approval_token=None)

        # Valid with approval token and approval engine
        mock_approval_engine = MagicMock()
        mock_approval_engine.validate_token.return_value = True

        validating_engine = PlanValidator(approval_engine=mock_approval_engine)
        validating_engine.validate_step_pre_execution("tenant_1", high_risk_step, approval_token="valid_app_token_123")
        mock_approval_engine.validate_token.assert_called_once()

    def test_plan_validator_policy_rejection(self):
        # Invariant M59-F08
        mock_policy = MagicMock()
        mock_policy.evaluate.return_value = False

        validator = PlanValidator(policy_engine=mock_policy)
        step = PlanStep(
            step_number=1,
            action_type=ActionType.TOOL,
            action_name="unauthorized_tool",
            risk_level=CapabilityRiskLevel.LOW,
        )

        with pytest.raises(PermissionError, match="Policy denied execution"):
            validator.validate_step_pre_execution("tenant_1", step)
