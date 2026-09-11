"""Unit tests for Milestone 26 Skill Verification Harness."""

import pytest
from core.skill_types import SynthesizedSkill, TestVector, SkillLifecycleState
from core.skill_verification import SkillVerificationHarness
from evaluation.trajectory_verifier import TrajectoryVerifier


def test_harness_successful_verification():
    harness = SkillVerificationHarness()
    skill = SynthesizedSkill(
        name="string_reverser",
        description="Reverses input string",
        source_code="def execute(x):\n    return x[::-1]",
        ast_hash="",
        test_vectors=(
            TestVector(input_data="abc", expected_output_contains=("cba",)),
            TestVector(input_data="radar", expected_output_regex=r"^radar$"),
        ),
    )
    report = harness.verify_skill(skill)
    assert report.passed is True
    assert report.pass_rate == 1.0
    assert report.passed_tests == 2
    assert report.total_tests == 2
    assert skill.lifecycle_state == SkillLifecycleState.VERIFIED
    assert skill.is_verified is True


def test_harness_failed_test_vector():
    harness = SkillVerificationHarness()
    skill = SynthesizedSkill(
        name="faulty_math",
        description="Performs bad math",
        source_code="def execute(x):\n    return str(int(x) + 1)",
        ast_hash="",
        test_vectors=(
            TestVector(input_data="5", expected_output_contains=("6",)),
            TestVector(input_data="10", expected_output_contains=("20",)),  # Will fail
        ),
    )
    report = harness.verify_skill(skill)
    assert report.passed is False
    assert report.pass_rate == 0.5
    assert report.passed_tests == 1
    assert report.total_tests == 2
    assert skill.lifecycle_state == SkillLifecycleState.DRAFT


def test_harness_security_audit_failure_short_circuit():
    harness = SkillVerificationHarness()
    skill = SynthesizedSkill(
        name="malicious_tool",
        description="Tries to import os",
        source_code="import os\ndef execute(x): return os.name",
        ast_hash="",
        test_vectors=(TestVector(input_data="x", expected_output_contains=("nt",)),),
    )
    report = harness.verify_skill(skill)
    assert report.passed is False
    assert report.security_report.is_safe is False
    assert skill.lifecycle_state == SkillLifecycleState.DRAFT


def test_harness_with_trajectory_verifier_integration():
    tv = TrajectoryVerifier()
    harness = SkillVerificationHarness(trajectory_verifier=tv)
    skill = SynthesizedSkill(
        name="json_formatter",
        description="Formats JSON",
        source_code="import json\ndef execute(x):\n    d = json.loads(x)\n    return json.dumps(d, sort_keys=True)",
        ast_hash="",
        test_vectors=(
            TestVector(
                input_data='{"b": 2, "a": 1}',
                expected_output_contains=('{"a": 1, "b": 2}',),
                expected_schema={"a": "int", "b": "int"},
            ),
        ),
    )
    report = harness.verify_skill(skill)
    assert report.passed is True
    assert "trajectory_verifier_checked" in report.invariants_verified
