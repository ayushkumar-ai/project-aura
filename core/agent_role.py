import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.capability_registry import ModelCapability

logger = logging.getLogger("aura.agent_role")


class BuiltinRole(str, Enum):
    """Standardized built-in agent personas for Project AURA."""

    COORDINATOR = "coordinator"
    ARCHITECT = "architect"
    RESEARCHER = "researcher"
    CODER = "coder"
    REVIEWER = "reviewer"
    SECURITY_AUDITOR = "security_auditor"
    DATA_ANALYST = "data_analyst"


# Forbidden metadata keys that could attempt privilege escalation
FORBIDDEN_METADATA_KEYS = frozenset({
    "is_authorized",
    "is_admin",
    "approved",
    "bypass_policy",
    "elevated_privileges",
    "sudo",
})


@dataclass(frozen=True)
class AgentRole:
    """Represents a specialized agent persona, its system prompt, and authorized skill bounds."""

    role_id: str
    name: str
    description: str
    system_prompt: str
    allowed_skills: tuple[str, ...] = field(default_factory=tuple)
    required_capabilities: tuple[ModelCapability, ...] = field(default_factory=tuple)
    temperature: float = 0.7
    max_tokens: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.role_id, str) or not self.role_id.strip():
            raise ValueError("role_id must be a non-empty string.")
        norm_id = self.role_id.strip().lower()
        object.__setattr__(self, "role_id", norm_id)

        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be a non-empty string.")
        object.__setattr__(self, "name", self.name.strip())

        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("description must be a non-empty string.")
        object.__setattr__(self, "description", self.description.strip())

        if not isinstance(self.system_prompt, str) or not self.system_prompt.strip():
            raise ValueError("system_prompt must be a non-empty string.")
        object.__setattr__(self, "system_prompt", self.system_prompt.strip())

        # Validate allowed_skills
        norm_skills: list[str] = []
        if isinstance(self.allowed_skills, (list, tuple, set, frozenset)):
            for s in self.allowed_skills:
                if not isinstance(s, str) or not s.strip():
                    raise ValueError("Each allowed skill name must be a non-empty string.")
                norm_skills.append(s.strip().lower())
        else:
            raise TypeError("allowed_skills must be a sequence of skill name strings.")
        object.__setattr__(self, "allowed_skills", tuple(dict.fromkeys(norm_skills)))

        # Validate required_capabilities
        norm_caps: list[ModelCapability] = []
        if isinstance(self.required_capabilities, (list, tuple, set, frozenset)):
            for c in self.required_capabilities:
                if isinstance(c, ModelCapability):
                    norm_caps.append(c)
                elif isinstance(c, str) and c.strip():
                    try:
                        norm_caps.append(ModelCapability(c.strip().lower()))
                    except ValueError:
                        raise ValueError(f"Unknown ModelCapability value: '{c}'")
                else:
                    raise TypeError(f"Invalid capability item: {c}")
        else:
            raise TypeError("required_capabilities must be a sequence of ModelCapability enums.")
        object.__setattr__(self, "required_capabilities", tuple(dict.fromkeys(norm_caps)))

        # Validate and sanitize metadata against privilege escalation
        cleaned_meta: dict[str, str] = {}
        if isinstance(self.metadata, dict):
            for k, v in self.metadata.items():
                k_str = str(k).strip().lower()
                if k_str in FORBIDDEN_METADATA_KEYS:
                    logger.warning(
                        "Security warning: Filtered forbidden authorization key '%s' from role '%s' metadata.",
                        k,
                        norm_id,
                    )
                    continue
                cleaned_meta[str(k).strip()] = str(v)
        else:
            raise TypeError("metadata must be a dictionary.")
        object.__setattr__(self, "metadata", cleaned_meta)

    def is_skill_allowed(self, skill_name: str) -> bool:
        """Check whether this role is authorized to invoke a specific skill."""
        if not self.allowed_skills:
            return False
        if "*" in self.allowed_skills:
            return True
        return skill_name.strip().lower() in self.allowed_skills

    def to_dict(self) -> dict[str, Any]:
        """Convert role definition into a serializable dictionary."""
        return {
            "role_id": self.role_id,
            "name": self.name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "allowed_skills": list(self.allowed_skills),
            "required_capabilities": [c.value for c in self.required_capabilities],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "metadata": dict(self.metadata),
        }
