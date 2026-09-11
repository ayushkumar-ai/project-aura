"""End-to-End Integration Tests for Milestone 26."""

import pytest
from app.aura import AURA
from core.agentic_runtime import AgenticRuntime
from core.policy import Policy, PolicyDecision
from core.provenance import wrap_tainted, is_tainted, unwrap_tainted
from core.runtime_checkpoint import RuntimeCheckpointManager
from core.skill_types import SkillLifecycleState, TestVector


def test_m26_end_to_end_synthesis_and_execution():
    runtime = AgenticRuntime()
    aura = AURA(orchestrator=None, agentic_runtime=runtime)

    # 1. Synthesize a dynamic tool
    skill = aura.synthesize_skill(
        name="cube_calc",
        description="Calculates cube of an integer",
        source_code="def execute(x):\n    n = int(x.strip())\n    return str(n ** 3)",
        test_vectors=[
            TestVector(input_data="3", expected_output_contains=("27",)),
            TestVector(input_data="4", expected_output_contains=("64",)),
        ],
        author_role_id="coder",
        verify_after_synthesis=True,
    )
    assert skill.name == "cube_calc"
    assert skill.is_verified is True
    assert skill.verification_report.passed is True

    # 2. Register skill in runtime
    aura.register_dynamic_skill(skill, activate=True)
    assert aura.get_dynamic_skill("cube_calc") is not None

    # 3. Execute through facade
    res = aura.execute_dynamic_skill("cube_calc", "5")
    assert res == "125"

    # Telemetry check
    dyn_skill = aura.get_dynamic_skill("cube_calc")
    assert dyn_skill.invocation_count == 1


def test_m26_synthesis_security_rejection():
    runtime = AgenticRuntime()
    aura = AURA(orchestrator=None, agentic_runtime=runtime)

    # Attempt to synthesize tool with forbidden module
    skill = aura.synthesize_skill(
        name="hack_tool",
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
        aura.execute_dynamic_skill("hack_tool", "data")


def test_m26_composite_skill_pipeline_facade():
    runtime = AgenticRuntime()
    aura = AURA(orchestrator=None, agentic_runtime=runtime)

    # Synthesize component tools
    s1 = aura.synthesize_skill(
        name="num_adder",
        description="Adds 10",
        source_code="def execute(x): return str(int(x) + 10)",
        test_vectors=[TestVector(input_data="5", expected_output_contains=("15",))],
    )
    s2 = aura.synthesize_skill(
        name="num_doubler",
        description="Doubles input",
        source_code="def execute(x): return str(int(x) * 2)",
        test_vectors=[TestVector(input_data="10", expected_output_contains=("20",))],
    )
    aura.register_dynamic_skill(s1, activate=True)
    aura.register_dynamic_skill(s2, activate=True)

    # Synthesize composite pipeline
    comp = aura.synthesize_composite_skill(
        name="add_then_double",
        description="Adds 10 then doubles",
        steps=[
            {"step_id": "step1", "skill_or_tool_name": "num_adder", "input_template": "{input}"},
            {"step_id": "step2", "skill_or_tool_name": "num_doubler", "input_template": "{prev}"},
        ],
    )
    aura.agentic_runtime.register_composite_skill(comp, activate=True)

    # Execute composite skill
    out = aura.execute_dynamic_skill("add_then_double", "5")
    # (5 + 10) * 2 = 30
    assert out == "30"


def test_m26_checkpoint_preservation(tmp_path):
    ckpt_dir = tmp_path / "checkpoints"
    ckpt_mgr = RuntimeCheckpointManager(checkpoint_dir=ckpt_dir)
    runtime = AgenticRuntime(checkpoint_manager=ckpt_mgr)
    aura = AURA(orchestrator=None, agentic_runtime=runtime)

    skill = aura.synthesize_skill(
        name="hash_tool",
        description="Hashes input",
        source_code="import hashlib\ndef execute(x): return hashlib.md5(x.encode()).hexdigest()",
        test_vectors=[TestVector(input_data="test", expected_output_contains=("098f6bcd4621d373cade4e832627b4f6",))],
    )
    aura.register_dynamic_skill(skill, activate=True)
    res1 = aura.execute_dynamic_skill("hash_tool", "test")
    assert res1 == "098f6bcd4621d373cade4e832627b4f6"

    # Save checkpoint
    ckpt_meta = aura.save_state_checkpoint(checkpoint_id="ckpt_m26_test")
    assert ckpt_meta is not None

    # Create new runtime and restore checkpoint
    ckpt_mgr2 = RuntimeCheckpointManager(checkpoint_dir=ckpt_dir)
    runtime2 = AgenticRuntime(checkpoint_manager=ckpt_mgr2)
    aura2 = AURA(orchestrator=None, agentic_runtime=runtime2)
    aura2.restore_state_checkpoint(ckpt_dir / "ckpt_m26_test.json")

    # Verify skill restored and executable
    restored_skill = aura2.get_dynamic_skill("hash_tool")
    assert restored_skill is not None
    assert restored_skill.name == "hash_tool"
    assert restored_skill.invocation_count == 1

    res2 = aura2.execute_dynamic_skill("hash_tool", "test")
    assert res2 == "098f6bcd4621d373cade4e832627b4f6"
