"""Unit tests for Milestone 26 Skill Types, Contracts, and Enums."""

import pytest
from core.provenance import wrap_tainted, is_tainted
from core.skill_types import (
    SkillLifecycleState,
    TestVector,
    SecurityAuditReport,
    SkillVerificationReport,
    SynthesizedSkill,
    DynamicTool,
    SkillStep,
    CompositeSkill,
    sanitize_skill_metadata,
    compute_code_hash,
)


def test_sanitize_skill_metadata():
    raw_meta = {
        "is_admin": True,
        "is_authorized": True,
        "approved": True,
        "bypass_policy": True,
        "sudo": True,
        "valid_key": "safe_value",
        "nested": {
            "is_admin": True,
            "nested_safe": 123,
        },
        "callable_val": lambda x: x,
    }
    cleaned = sanitize_skill_metadata(raw_meta)
    assert "is_admin" not in cleaned
    assert "is_authorized" not in cleaned
    assert "approved" not in cleaned
    assert "bypass_policy" not in cleaned
    assert "sudo" not in cleaned
    assert cleaned["valid_key"] == "safe_value"
    assert "is_admin" not in cleaned["nested"]
    assert cleaned["nested"]["nested_safe"] == 123
    assert "callable_val" not in cleaned


def test_compute_code_hash():
    code1 = "def execute(x):\n    return x\n"
    code2 = "def execute(x):\n    return x"
    assert compute_code_hash(code1) == compute_code_hash(code2)
    assert len(compute_code_hash(code1)) == 64


def test_test_vector_creation_and_serialization():
    tv = TestVector(
        input_data="hello",
        expected_output_contains=("ell", "o"),
        expected_output_regex=r"^h.*o$",
        expected_schema={"type": "str"},
        timeout_seconds=3.5,
        description="Greeting test",
    )
    d = tv.to_dict()
    assert d["input_data"] == "hello"
    assert d["expected_output_contains"] == ["ell", "o"]
    assert d["timeout_seconds"] == 3.5

    tv2 = TestVector.from_dict(d)
    assert tv2.input_data == tv.input_data
    assert tv2.expected_output_contains == tv.expected_output_contains
    assert tv2.timeout_seconds == 3.5


def test_security_audit_report():
    report = SecurityAuditReport(
        is_safe=True,
        ast_hash="abc123hash",
        violations=(),
        allowed_imports=("math", "json"),
        complexity_score=5,
        checked_nodes_count=20,
    )
    d = report.to_dict()
    assert d["is_safe"] is True
    assert d["ast_hash"] == "abc123hash"
    assert d["allowed_imports"] == ["math", "json"]

    report2 = SecurityAuditReport.from_dict(d)
    assert report2.is_safe is True
    assert report2.allowed_imports == ("math", "json")


def test_skill_verification_report():
    sec = SecurityAuditReport(is_safe=True, ast_hash="hash123")
    vr = SkillVerificationReport(
        skill_name="math_solver",
        passed=True,
        pass_rate=1.0,
        total_tests=3,
        passed_tests=3,
        avg_latency_ms=1.25,
        security_report=sec,
        invariants_verified=("ast_clean", "trajectory_ok"),
    )
    d = vr.to_dict()
    assert d["passed"] is True
    assert d["pass_rate"] == 1.0
    assert d["avg_latency_ms"] == 1.25

    vr2 = SkillVerificationReport.from_dict(d)
    assert vr2.skill_name == "math_solver"
    assert vr2.passed is True
    assert vr2.security_report.is_safe is True


def test_synthesized_skill_lifecycle_and_telemetry():
    skill = SynthesizedSkill(
        name="custom_formatter",
        description="Formats input text",
        source_code="def execute(x):\n    return x.upper()",
        ast_hash="",
        lifecycle_state=SkillLifecycleState.DRAFT,
    )
    assert skill.name == "custom_formatter"
    assert skill.ast_hash != ""
    assert skill.lifecycle_state == SkillLifecycleState.DRAFT
    assert not skill.is_verified

    # Transition
    skill.transition_to(SkillLifecycleState.VERIFIED)
    assert skill.is_verified

    # Telemetry
    skill.record_invocation(latency_ms=10.0, is_error=False)
    skill.record_invocation(latency_ms=20.0, is_error=True)
    assert skill.invocation_count == 2
    assert skill.error_count == 1
    assert skill.error_rate == 0.5
    assert skill.avg_latency_ms == 15.0

    # Serialization roundtrip
    d = skill.to_dict()
    skill2 = SynthesizedSkill.from_dict(d)
    assert skill2.name == skill.name
    assert skill2.invocation_count == 2
    assert skill2.error_count == 1
    assert skill2.lifecycle_state == SkillLifecycleState.VERIFIED


def test_dynamic_tool_adapter():
    skill = SynthesizedSkill(
        name="json_cleaner",
        description="Cleans JSON strings",
        source_code="def execute(x):\n    return x",
        ast_hash="",
    )
    tool = DynamicTool(skill, executor_func=lambda name, inp: f"Handled by {name}: {inp}")
    assert tool.name == "json_cleaner"
    assert tool.description == "Cleans JSON strings"
    assert tool.execute("payload") == "Handled by json_cleaner: payload"


def test_composite_skill():
    step1 = SkillStep(step_id="step1", skill_or_tool_name="calculator", input_template="{input}")
    step2 = SkillStep(step_id="step2", skill_or_tool_name="formatter", input_template="{prev}")
    comp = CompositeSkill(
        name="calc_and_format",
        description="Calculates and formats output",
        steps=(step1, step2),
    )
    assert comp.name == "calc_and_format"
    assert len(comp.steps) == 2

    d = comp.to_dict()
    comp2 = CompositeSkill.from_dict(d)
    assert comp2.name == comp.name
    assert len(comp2.steps) == 2
    assert comp2.steps[0].skill_or_tool_name == "calculator"
