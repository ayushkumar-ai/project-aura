"""Unit tests for M33 Structured Planning Engine Subsystem."""

import pytest
from core.structured_plan_types import (
    PlanStatus,
    PlanStepNode,
    PlanStepStatus,
    StructuredPlan,
)
from core.structured_planner import StructuredPlanningEngine


def test_plan_creation_heuristic_decomposition():
    engine = StructuredPlanningEngine()
    plan = engine.create_plan(goal="Migrate database to PostgreSQL")
    assert plan.plan_id.startswith("plan_")
    assert len(plan.steps) == 3
    assert plan.status == PlanStatus.CREATED
    assert plan.steps[0].step_id == "step_1_analyze"
    assert plan.steps[1].depends_on == ["step_1_analyze"]


def test_plan_validation_cycle_detection():
    engine = StructuredPlanningEngine()
    step_a = PlanStepNode(step_id="a", title="A", description="Step A", depends_on=["b"])
    step_b = PlanStepNode(step_id="b", title="B", description="Step B", depends_on=["a"])

    with pytest.raises(ValueError, match="Circular dependency detected"):
        engine.create_plan(goal="Cyclic Goal", steps=[step_a, step_b])


def test_plan_validation_dangling_dependency():
    engine = StructuredPlanningEngine()
    step_a = PlanStepNode(step_id="a", title="A", description="Step A", depends_on=["non_existent_step"])

    with pytest.raises(ValueError, match="depends on non-existent step"):
        engine.create_plan(goal="Dangling Goal", steps=[step_a])


def test_plan_execution_success():
    engine = StructuredPlanningEngine()
    plan = engine.create_plan(goal="Build and deploy microservice")
    audit = engine.execute_plan(plan)

    assert audit.is_success is True
    assert audit.steps_completed == 3
    assert audit.steps_failed == 0
    assert len(audit.execution_trace) == 3
    assert plan.status == PlanStatus.COMPLETED


def test_plan_cancellation():
    engine = StructuredPlanningEngine()
    plan = engine.create_plan(goal="Long running pipeline")
    engine.cancel_plan(plan.plan_id)
    audit = engine.execute_plan(plan)

    assert audit.is_success is False
    assert plan.status == PlanStatus.CANCELLED
