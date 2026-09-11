"""Unit tests for Milestone 26 Dynamic Skill Registry & Telemetry Manager."""

import pytest
from core.dynamic_skill_registry import DynamicSkillRegistry
from core.skill_types import (
    SynthesizedSkill,
    CompositeSkill,
    SkillStep,
    SkillLifecycleState,
)
from core.tool_registry import ToolRegistry


def test_registry_registration_and_lookup():
    reg = DynamicSkillRegistry(max_skills=10)
    skill = SynthesizedSkill(
        name="adder",
        description="Adds two numbers",
        source_code="def execute(x):\n    a, b = map(int, x.split())\n    return str(a + b)",
        ast_hash="",
        lifecycle_state=SkillLifecycleState.VERIFIED,
    )
    reg.register_skill(skill, activate=True)
    assert reg.has_skill("adder")
    assert reg.has("adder")

    retrieved = reg.get_skill("adder")
    assert retrieved.name == "adder"
    assert retrieved.lifecycle_state == SkillLifecycleState.ACTIVE

    # Tool adapter
    tool = reg.get_tool("adder")
    assert tool.name == "adder"
    assert tool.execute("10 25") == "35"


def test_registry_execution_and_auto_deprecation():
    reg = DynamicSkillRegistry(
        auto_deprecation_threshold=0.30,
        min_invocations_for_deprecation=3,
    )
    # Flaky code that fails on input == 'err'
    code = """
def execute(x):
    if x == 'err':
        raise ValueError('Forced error')
    return 'ok: ' + str(x)
"""
    skill = SynthesizedSkill(
        name="flaky_tool",
        description="Fails occasionally",
        source_code=code,
        ast_hash="",
        lifecycle_state=SkillLifecycleState.VERIFIED,
    )
    reg.register_skill(skill, activate=True)

    # 1. Success
    out1 = reg.execute_skill("flaky_tool", "data1")
    assert out1 == "ok: data1"

    # 2. Error
    with pytest.raises(RuntimeError):
        reg.execute_skill("flaky_tool", "err")

    # 3. Error
    with pytest.raises(RuntimeError):
        reg.execute_skill("flaky_tool", "err")

    # Now 3 invocations, 2 errors = 66.6% error rate > 30% threshold
    assert skill.lifecycle_state == SkillLifecycleState.DEPRECATED
    assert "auto_deprecation_reason" in skill.metadata

    # Further execution should be blocked
    with pytest.raises(PermissionError) as exc_info:
        reg.execute_skill("flaky_tool", "data2")
    assert "deprecated" in str(exc_info.value).lower()


def test_registry_manual_deprecate_and_revoke():
    reg = DynamicSkillRegistry()
    skill = SynthesizedSkill(
        name="temp_tool",
        description="Temporary tool",
        source_code="def execute(x): return x",
        ast_hash="",
        lifecycle_state=SkillLifecycleState.ACTIVE,
    )
    reg.register_skill(skill, activate=True)

    assert reg.deprecate_skill("temp_tool", reason="Obsolete") is True
    assert reg.get_skill("temp_tool").lifecycle_state == SkillLifecycleState.DEPRECATED

    assert reg.revoke_skill("temp_tool", reason="Security hazard") is True
    assert reg.get_skill("temp_tool").lifecycle_state == SkillLifecycleState.REVOKED

    with pytest.raises(PermissionError):
        reg.execute_skill("temp_tool", "test")


def test_registry_composite_skill_execution():
    reg = DynamicSkillRegistry()
    skill1 = SynthesizedSkill(
        name="upper_tool",
        description="Uppercase",
        source_code="def execute(x): return x.upper()",
        ast_hash="",
        lifecycle_state=SkillLifecycleState.VERIFIED,
    )
    skill2 = SynthesizedSkill(
        name="bracket_tool",
        description="Brackets",
        source_code="def execute(x): return '[' + x + ']'",
        ast_hash="",
        lifecycle_state=SkillLifecycleState.VERIFIED,
    )
    reg.register_skill(skill1, activate=True)
    reg.register_skill(skill2, activate=True)

    comp = CompositeSkill(
        name="pipe_upper_bracket",
        description="Pipes uppercase to brackets",
        steps=(
            SkillStep(step_id="s1", skill_or_tool_name="upper_tool", input_template="{input}"),
            SkillStep(step_id="s2", skill_or_tool_name="bracket_tool", input_template="{prev}"),
        ),
    )
    reg.register_composite_skill(comp, activate=True)

    res = reg.execute_composite_skill("pipe_upper_bracket", "hello world")
    assert res == "[HELLO WORLD]"


def test_registry_checkpoint_serialization_and_restore():
    reg = DynamicSkillRegistry()
    skill = SynthesizedSkill(
        name="persist_tool",
        description="Persisted tool",
        source_code="def execute(x): return 'saved:' + str(x)",
        ast_hash="",
        lifecycle_state=SkillLifecycleState.VERIFIED,
    )
    reg.register_skill(skill, activate=True)
    reg.execute_skill("persist_tool", "alpha")

    d = reg.to_dict()
    assert "skills" in d
    assert len(d["skills"]) == 1

    restored = DynamicSkillRegistry.from_dict(d)
    assert restored.has_skill("persist_tool")
    restored_skill = restored.get_skill("persist_tool")
    assert restored_skill.name == "persist_tool"
    assert restored_skill.invocation_count == 1
    assert restored.execute_skill("persist_tool", "beta") == "saved:beta"
