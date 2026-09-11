from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from core.capability_registry import ModelCapability


@dataclass(frozen=True)
class Skill:
    """Declarative specification and execution contract for an AURA skill."""

    name: str
    description: str = ""
    required_capabilities: frozenset[str] = field(default_factory=frozenset)
    tools: tuple[str, ...] = field(default_factory=tuple)
    input_schema: dict[str, Any] = field(default_factory=dict)
    handler: Callable[..., Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Skill name must be a non-empty string.")
        object.__setattr__(self, "name", self.name.strip())

        if not isinstance(self.description, str):
            raise TypeError("Skill description must be a string.")

        # Normalize capabilities
        caps: set[str] = set()
        for c in self.required_capabilities:
            if isinstance(c, ModelCapability):
                caps.add(c.value)
            elif isinstance(c, str):
                s = c.strip().lower()
                if s:
                    caps.add(s)
            else:
                raise TypeError(f"Invalid capability type: {type(c)}")
        object.__setattr__(self, "required_capabilities", frozenset(caps))

        # Normalize tools
        tool_list: list[str] = []
        for t in self.tools:
            if not isinstance(t, str) or not t.strip():
                raise ValueError("Tool name must be a non-empty string.")
            tool_list.append(t.strip().lower())
        object.__setattr__(self, "tools", tuple(tool_list))

        if not isinstance(self.input_schema, dict):
            raise TypeError("input_schema must be a dict.")

        if self.handler is not None and not callable(self.handler):
            raise TypeError("Skill handler must be callable or None.")

        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a dict.")

    def requires_capability(self, capability: ModelCapability | str) -> bool:
        """Check if this skill requires a given capability."""
        if isinstance(capability, ModelCapability):
            cap_str = capability.value
        elif isinstance(capability, str):
            cap_str = capability.strip().lower()
        else:
            return False
        return cap_str in self.required_capabilities

    def requires_tool(self, tool_name: str) -> bool:
        """Check if this skill requires a given tool."""
        if not isinstance(tool_name, str) or not tool_name.strip():
            return False
        return tool_name.strip().lower() in self.tools


class SkillRegistry:
    """Registry for discovering and managing skills in AURA."""

    def __init__(self, dynamic_registry: Any | None = None):
        self._skills: dict[str, Skill] = {}
        self._dynamic_registry = dynamic_registry

    @property
    def dynamic_registry(self) -> Any | None:
        return self._dynamic_registry

    @dynamic_registry.setter
    def dynamic_registry(self, registry: Any | None) -> None:
        self._dynamic_registry = registry

    def set_dynamic_registry(self, registry: Any | None) -> None:
        self._dynamic_registry = registry

    def register(self, skill: Skill) -> None:
        """Register a skill under its unique name."""
        if not isinstance(skill, Skill):
            raise TypeError("Skill must be an instance of Skill.")

        key = skill.name.strip().lower()
        if key in self._skills:
            raise ValueError(f"Skill already registered: {skill.name}")

        self._skills[key] = skill

    def register_skill(self, skill: Skill) -> None:
        """Alias for register."""
        self.register(skill)

    def get(self, name: str) -> Skill:
        """Retrieve a registered skill by name."""
        if not isinstance(name, str) or not name.strip():
            raise KeyError(f"Unknown skill: {name}")

        key = name.strip().lower()
        if key in self._skills:
            return self._skills[key]

        if self._dynamic_registry is not None and hasattr(self._dynamic_registry, "has_skill") and self._dynamic_registry.has_skill(key):
            try:
                dyn_skill = self._dynamic_registry.get_skill(key)
                return Skill(
                    name=dyn_skill.name,
                    description=dyn_skill.description,
                    required_capabilities=frozenset(dyn_skill.required_capabilities),
                    input_schema=dyn_skill.input_schema,
                    metadata=dyn_skill.metadata,
                )
            except Exception:
                pass

        raise KeyError(f"Unknown skill: {name}")

    def get_skill(self, name: str) -> Skill:
        """Alias for get."""
        return self.get(name)

    def has(self, name: str) -> bool:
        """Check if a skill is registered by name."""
        if not isinstance(name, str) or not name.strip():
            return False
        key = name.strip().lower()
        if key in self._skills:
            return True
        if self._dynamic_registry is not None and hasattr(self._dynamic_registry, "has_skill"):
            return self._dynamic_registry.has_skill(key)
        return False

    def has_skill(self, name: str) -> bool:
        """Alias for has."""
        return self.has(name)

    def __contains__(self, name: str) -> bool:
        return self.has(name)

    def list_skills(self) -> list[Skill]:
        """Return all registered skill objects."""
        skills = list(self._skills.values())
        if self._dynamic_registry is not None and hasattr(self._dynamic_registry, "list_skills"):
            try:
                for dyn_skill in self._dynamic_registry.list_skills():
                    if dyn_skill.name.lower() not in self._skills:
                        skills.append(
                            Skill(
                                name=dyn_skill.name,
                                description=dyn_skill.description,
                                required_capabilities=frozenset(dyn_skill.required_capabilities),
                                input_schema=dyn_skill.input_schema,
                                metadata=dyn_skill.metadata,
                            )
                        )
            except Exception:
                pass
        return skills

    def list_skill_names(self) -> list[str]:
        """Return the names of all registered skills."""
        return [s.name for s in self.list_skills()]

    def find_skills_by_capability(
        self,
        capability: ModelCapability | str,
    ) -> list[Skill]:
        """Find all registered skills requiring the specified capability."""
        return [
            s for s in self.list_skills()
            if s.requires_capability(capability)
        ]

    def find_skills_by_tool(
        self,
        tool_name: str,
    ) -> list[Skill]:
        """Find all registered skills requiring the specified tool."""
        return [
            s for s in self.list_skills()
            if s.requires_tool(tool_name)
        ]
