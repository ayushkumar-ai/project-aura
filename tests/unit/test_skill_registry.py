import pytest

from core.capability_registry import ModelCapability
from core.skill_registry import Skill, SkillRegistry


def test_skill_initialization_and_normalization():
    handler_fn = lambda x: f"result: {x}"
    skill = Skill(
        name=" research_agent ",
        description="Performs deep research",
        required_capabilities={ModelCapability.REASONING, "TOOL_USE"},
        tools=(" SEARCH ", "calculator"),
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        handler=handler_fn,
        metadata={"category": "analysis"},
    )

    assert skill.name == "research_agent"
    assert skill.description == "Performs deep research"
    assert skill.required_capabilities == frozenset({"reasoning", "tool_use"})
    assert skill.tools == ("search", "calculator")
    assert skill.input_schema == {"type": "object", "properties": {"query": {"type": "string"}}}
    assert skill.handler is handler_fn
    assert skill.metadata == {"category": "analysis"}


def test_skill_requires_capability():
    skill = Skill(
        name="coder",
        description="Writes code",
        required_capabilities={ModelCapability.CODING, ModelCapability.REASONING},
    )

    assert skill.requires_capability(ModelCapability.CODING) is True
    assert skill.requires_capability("coding") is True
    assert skill.requires_capability("CODING") is True
    assert skill.requires_capability(ModelCapability.VISION) is False
    assert skill.requires_capability("unknown") is False
    assert skill.requires_capability(None) is False


def test_skill_requires_tool():
    skill = Skill(
        name="math_solver",
        tools=("calculator", "plotter"),
    )

    assert skill.requires_tool("calculator") is True
    assert skill.requires_tool("CALCULATOR") is True
    assert skill.requires_tool("plotter") is True
    assert skill.requires_tool("search") is False
    assert skill.requires_tool("") is False
    assert skill.requires_tool(None) is False


def test_skill_rejects_invalid_fields():
    with pytest.raises(ValueError, match="Skill name must be a non-empty string"):
        Skill(name="")

    with pytest.raises(ValueError, match="Skill name must be a non-empty string"):
        Skill(name="   ")

    with pytest.raises(ValueError, match="Skill name must be a non-empty string"):
        Skill(name=None)

    with pytest.raises(TypeError, match="Skill description must be a string"):
        Skill(name="valid", description=123)

    with pytest.raises(TypeError, match="Invalid capability type"):
        Skill(name="valid", required_capabilities=[123])

    with pytest.raises(ValueError, match="Tool name must be a non-empty string"):
        Skill(name="valid", tools=("",))

    with pytest.raises(ValueError, match="Tool name must be a non-empty string"):
        Skill(name="valid", tools=(123,))

    with pytest.raises(TypeError, match="input_schema must be a dict"):
        Skill(name="valid", input_schema="not_a_dict")

    with pytest.raises(TypeError, match="Skill handler must be callable or None"):
        Skill(name="valid", handler="not_a_callable")

    with pytest.raises(TypeError, match="metadata must be a dict"):
        Skill(name="valid", metadata="not_a_dict")


def test_skill_registry_registers_and_retrieves_skill():
    registry = SkillRegistry()
    skill = Skill(name="search_skill", description="Web search")

    registry.register(skill)

    assert registry.get("search_skill") is skill
    assert registry.get("SEARCH_SKILL") is skill
    assert registry.get_skill("search_skill") is skill


def test_skill_registry_has_and_contains():
    registry = SkillRegistry()
    skill = Skill(name="search_skill")

    registry.register(skill)

    assert registry.has("search_skill") is True
    assert registry.has("SEARCH_SKILL") is True
    assert registry.has_skill("search_skill") is True
    assert "search_skill" in registry
    assert "unknown_skill" not in registry
    assert registry.has("unknown_skill") is False
    assert registry.has("") is False
    assert registry.has(None) is False


def test_skill_registry_lists_skills_and_names():
    registry = SkillRegistry()
    s1 = Skill(name="skill_a")
    s2 = Skill(name="skill_b")

    registry.register(s1)
    registry.register(s2)

    assert registry.list_skills() == [s1, s2]
    assert registry.list_skill_names() == ["skill_a", "skill_b"]


def test_skill_registry_rejects_duplicate_registration():
    registry = SkillRegistry()
    skill1 = Skill(name="unique_skill")
    skill2 = Skill(name="UNIQUE_SKILL")

    registry.register(skill1)

    with pytest.raises(ValueError, match="Skill already registered"):
        registry.register(skill2)


def test_skill_registry_rejects_non_skill_object():
    registry = SkillRegistry()

    with pytest.raises(TypeError, match="Skill must be an instance of Skill"):
        registry.register("not_a_skill")


def test_skill_registry_raises_key_error_for_unknown():
    registry = SkillRegistry()

    with pytest.raises(KeyError, match="Unknown skill"):
        registry.get("unknown_skill")

    with pytest.raises(KeyError, match="Unknown skill"):
        registry.get("")

    with pytest.raises(KeyError, match="Unknown skill"):
        registry.get(None)


def test_skill_registry_discovery_by_capability():
    registry = SkillRegistry()

    s_research = Skill(
        name="research",
        required_capabilities={ModelCapability.REASONING, ModelCapability.TOOL_USE},
    )
    s_coding = Skill(
        name="coding",
        required_capabilities={ModelCapability.CODING, ModelCapability.REASONING},
    )
    s_vision = Skill(
        name="vision_analysis",
        required_capabilities={ModelCapability.VISION},
    )

    registry.register(s_research)
    registry.register(s_coding)
    registry.register(s_vision)

    # REASONING matches research and coding
    reasoning_skills = registry.find_skills_by_capability(ModelCapability.REASONING)
    assert reasoning_skills == [s_research, s_coding]

    # String lookup
    coding_skills = registry.find_skills_by_capability("coding")
    assert coding_skills == [s_coding]

    # VISION matches vision_analysis
    vision_skills = registry.find_skills_by_capability(ModelCapability.VISION)
    assert vision_skills == [s_vision]

    # Audio matches nothing
    audio_skills = registry.find_skills_by_capability(ModelCapability.AUDIO)
    assert audio_skills == []


def test_skill_registry_discovery_by_tool():
    registry = SkillRegistry()

    s1 = Skill(name="web_searcher", tools=("browser", "search"))
    s2 = Skill(name="file_searcher", tools=("grep", "search"))
    s3 = Skill(name="calc", tools=("calculator",))

    registry.register(s1)
    registry.register(s2)
    registry.register(s3)

    search_skills = registry.find_skills_by_tool("search")
    assert search_skills == [s1, s2]

    calc_skills = registry.find_skills_by_tool("calculator")
    assert calc_skills == [s3]

    unused_tool_skills = registry.find_skills_by_tool("bash")
    assert unused_tool_skills == []


def test_skill_registry_alias_module_import():
    from core.skill import Skill as ImportedSkill
    from core.skill import SkillRegistry as ImportedRegistry

    assert ImportedSkill is Skill
    assert ImportedRegistry is SkillRegistry
