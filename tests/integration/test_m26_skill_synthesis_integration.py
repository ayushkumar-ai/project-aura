"""End-to-End Integration Tests for Milestone 26 Dynamic Skill Synthesis & Sandboxed Capability Evolution."""

import pytest
from app.aura import AURA
from core.agentic_runtime import AgenticRuntime
from core.skill_types import SkillLifecycleState, TestVector


def test_m26_end_to_end_synthesis_and_execution_integration():
    """Verify dynamic Python tool synthesis, AST validation, sandboxing, and execution."""
    runtime = AgenticRuntime()
    aura = AURA(orchestrator=None, agentic_runtime=runtime)

    # 1. Synthesize a dynamic tool
    skill = aura.synthesize_skill(
        name="cube_calc_integ",
        description="Calculates cube of an integer",
        source_code="def execute(x):\n    n = int(x.strip())\n    return str(n ** 3)",
        test_vectors=[
            TestVector(input_data="3", expected_output_contains=("27",)),
            TestVector(input_data="4", expected_output_contains=("64",)),
        ],
        author_role_id="coder",
        verify_after_synthesis=True,
    )
    assert skill.name == "cube_calc_integ"
    assert skill.is_verified is True
    assert skill.verification_report.passed is True

    # 2. Register skill in runtime
    aura.register_dynamic_skill(skill, activate=True)
    assert aura.get_dynamic_skill("cube_calc_integ") is not None

    # 3. Execute through facade
    res = aura.execute_dynamic_skill("cube_calc_integ", "5")
    assert res == "125"

    # Telemetry check
    dyn_skill = aura.get_dynamic_skill("cube_calc_integ")
    assert dyn_skill.invocation_count == 1


def test_m26_synthesis_security_rejection_integration():
    """Verify AST security visitor blocks forbidden imports during synthesis."""
    runtime = AgenticRuntime()
    aura = AURA(orchestrator=None, agentic_runtime=runtime)

    # Attempt to synthesize tool with forbidden module
    skill = aura.synthesize_skill(
        name="hack_tool_integ",
        description="Attempts to read files with os",
        source_code="import os\ndef execute(x):\n    return os.listdir('.')",
        verify_after_synthesis=True,
    )
    assert skill.is_verified is False
    assert skill.verification_report.passed is False
    assert skill.verification_report.security_report.is_safe is False

    # Attempting to register without verification pass keeps it inactive
    aura.register_dynamic_skill(skill, activate=True)
    with pytest.raises(PermissionError):
        aura.execute_dynamic_skill("hack_tool_integ", "data")


def test_m26_composite_skill_pipeline_integration():
    """Verify multi-step composite skill pipeline execution."""
    runtime = AgenticRuntime()
    aura = AURA(orchestrator=None, agentic_runtime=runtime)

    # Synthesize component tools
    s1 = aura.synthesize_skill(
        name="num_adder_integ",
        description="Adds 10",
        source_code="def execute(x): return str(int(x) + 10)",
        test_vectors=[TestVector(input_data="5", expected_output_contains=("15",))],
    )
    s2 = aura.synthesize_skill(
        name="num_doubler_integ",
        description="Doubles input",
        source_code="def execute(x): return str(int(x) * 2)",
        test_vectors=[TestVector(input_data="10", expected_output_contains=("20",))],
    )
    aura.register_dynamic_skill(s1, activate=True)
    aura.register_dynamic_skill(s2, activate=True)

    # Synthesize composite pipeline
    comp = aura.synthesize_composite_skill(
        name="add_then_double_integ",
        description="Adds 10 then doubles",
        steps=[
            {"step_id": "step1", "skill_or_tool_name": "num_adder_integ", "input_template": "{input}"},
            {"step_id": "step2", "skill_or_tool_name": "num_doubler_integ", "input_template": "{prev}"},
        ],
    )
    aura.agentic_runtime.register_composite_skill(comp, activate=True)

    # Execute composite skill: (5 + 10) * 2 = 30
    out = aura.execute_dynamic_skill("add_then_double_integ", "5")
    assert out == "30"
