"""Unit tests for Milestone 26 Skill Synthesizer."""

import pytest
from core.artifact_manager import ArtifactManager
from core.artifact_store import InMemoryArtifactStore
from core.skill_synthesis import SkillSynthesizer
from core.skill_types import SkillLifecycleState, TestVector


def test_synthesizer_basic_tool():
    synth = SkillSynthesizer()
    skill = synth.synthesize_tool(
        name="word_counter",
        description="Counts words in input string",
        source_code="def execute(x):\n    return str(len(x.split()))",
        test_vectors=[
            {"input_data": "apple banana orange", "expected_output_contains": ["3"]},
            {"input_data": "one", "expected_output_contains": ["1"]},
        ],
        author_role_id="coder",
        metadata={"category": "nlp", "is_admin": True},  # is_admin should be stripped
    )
    assert skill.name == "word_counter"
    assert skill.author_role_id == "coder"
    assert len(skill.test_vectors) == 2
    assert skill.lifecycle_state == SkillLifecycleState.SANDBOX_TESTED
    assert "is_admin" not in skill.metadata
    assert skill.metadata["category"] == "nlp"


def test_synthesizer_with_artifact_manager():
    store = InMemoryArtifactStore()
    mgr = ArtifactManager(store=store)
    synth = SkillSynthesizer(artifact_manager=mgr)

    skill = synth.synthesize_tool(
        name="prefix_adder",
        description="Adds prefix to string",
        source_code="def execute(x):\n    return 'PREFIX_' + str(x)",
        author_role_id="coder",
        originating_goal_id="goal_123",
    )
    assert skill.artifact_id is not None
    artifact = mgr.get_artifact(skill.artifact_id)
    assert artifact is not None
    assert "prefix_adder" in artifact.name


def test_synthesizer_composite_skill():
    synth = SkillSynthesizer()
    comp = synth.synthesize_composite_skill(
        name="math_and_count",
        description="Executes math then counts words",
        steps=[
            {"step_id": "s1", "skill_or_tool_name": "calculator", "input_template": "{input}"},
            {"step_id": "s2", "skill_or_tool_name": "word_counter", "input_template": "{prev}"},
        ],
        author_role_id="architect",
    )
    assert comp.name == "math_and_count"
    assert len(comp.steps) == 2
    assert comp.steps[0].skill_or_tool_name == "calculator"
    assert comp.steps[1].skill_or_tool_name == "word_counter"


def test_synthesizer_template_generation():
    template = SkillSynthesizer.generate_tool_template(function_name="run_calc", docstring="Calculates stuff")
    assert "def run_calc(input_data: str) -> str:" in template
    assert "Calculates stuff" in template
