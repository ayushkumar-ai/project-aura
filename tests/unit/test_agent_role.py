import pytest

from core.agent_role import AgentRole, BuiltinRole
from core.capability_registry import ModelCapability
from core.role_registry import RoleRegistry, get_default_builtin_roles


def test_agent_role_creation_and_attributes():
    role = AgentRole(
        role_id="Architect_Role",
        name="Lead Architect",
        description="Designs systems",
        system_prompt="You are an architect",
        allowed_skills=["echo", "calculator"],
        required_capabilities=[ModelCapability.REASONING],
        temperature=0.5,
        max_tokens=2048,
    )

    assert role.role_id == "architect_role"  # normalized
    assert role.name == "Lead Architect"
    assert role.description == "Designs systems"
    assert role.system_prompt == "You are an architect"
    assert role.allowed_skills == ("echo", "calculator")
    assert role.required_capabilities == (ModelCapability.REASONING,)
    assert role.temperature == 0.5
    assert role.max_tokens == 2048
    assert role.is_skill_allowed("echo") is True
    assert role.is_skill_allowed("ECHO") is True
    assert role.is_skill_allowed("unknown_tool") is False


def test_agent_role_security_metadata_filtering():
    role = AgentRole(
        role_id="hacker_role",
        name="Test",
        description="Test",
        system_prompt="Test",
        metadata={
            "is_authorized": "true",
            "is_admin": "true",
            "approved": "true",
            "custom_tag": "safe_value",
        },
    )

    assert "is_authorized" not in role.metadata
    assert "is_admin" not in role.metadata
    assert "approved" not in role.metadata
    assert role.metadata["custom_tag"] == "safe_value"


def test_agent_role_validation_errors():
    with pytest.raises(ValueError, match="role_id must be a non-empty string"):
        AgentRole(role_id="", name="N", description="D", system_prompt="P")

    with pytest.raises(ValueError, match="name must be a non-empty string"):
        AgentRole(role_id="R", name="   ", description="D", system_prompt="P")

    with pytest.raises(ValueError, match="description must be a non-empty string"):
        AgentRole(role_id="R", name="N", description="", system_prompt="P")

    with pytest.raises(ValueError, match="system_prompt must be a non-empty string"):
        AgentRole(role_id="R", name="N", description="D", system_prompt="")


def test_role_registry_builtin_defaults():
    registry = RoleRegistry()
    roles = registry.list_roles()
    assert len(roles) == 7

    assert registry.has_role("coordinator")
    assert registry.has_role("architect")
    assert registry.has_role("researcher")
    assert registry.has_role("coder")
    assert registry.has_role("reviewer")
    assert registry.has_role("security_auditor")
    assert registry.has_role("data_analyst")

    coder = registry.get_role("coder")
    assert coder.name == "Software Engineer"
    assert ModelCapability.CODING in coder.required_capabilities


def test_role_registry_crud_and_bounds():
    registry = RoleRegistry(max_roles=8, register_defaults=True)

    new_role = AgentRole(
        role_id="custom_tester",
        name="QA Tester",
        description="Tests code",
        system_prompt="You test code",
        allowed_skills=("*",),
    )

    registry.register_role(new_role)
    assert registry.has_role("custom_tester")
    assert registry.get_role("custom_tester").name == "QA Tester"

    # Reject duplicates without overwrite
    with pytest.raises(ValueError, match="already registered"):
        registry.register_role(new_role, overwrite=False)

    # Allow overwrite
    updated_role = AgentRole(
        role_id="custom_tester",
        name="Senior QA Tester",
        description="Tests code deeply",
        system_prompt="You test code deeply",
    )
    registry.register_role(updated_role, overwrite=True)
    assert registry.get_role("custom_tester").name == "Senior QA Tester"

    # Capacity limit
    another_role = AgentRole(role_id="role9", name="R9", description="D", system_prompt="P")
    with pytest.raises(ValueError, match="maximum role limit"):
        registry.register_role(another_role)

    # Unregister
    assert registry.unregister_role("custom_tester") is True
    assert registry.has_role("custom_tester") is False
    assert registry.unregister_role("nonexistent") is False
