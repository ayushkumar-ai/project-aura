import logging
import threading
from typing import Any

from core.agent_role import AgentRole, BuiltinRole
from core.capability_registry import ModelCapability

logger = logging.getLogger("aura.role_registry")


def get_default_builtin_roles() -> list[AgentRole]:
    """Generate the standardized default built-in roles for Project AURA."""
    return [
        AgentRole(
            role_id=BuiltinRole.COORDINATOR.value,
            name="Team Coordinator",
            description="Decomposes complex objectives, delegates tasks to specialists, and synthesizes final deliverables.",
            system_prompt=(
                "You are the Executive Team Coordinator in Project AURA. Your responsibility is to break down complex tasks, "
                "delegate work to specialized domain agents, track overall progress, and synthesize final high-confidence deliverables."
            ),
            allowed_skills=("*",),
            required_capabilities=(ModelCapability.REASONING,),
            temperature=0.4,
        ),
        AgentRole(
            role_id=BuiltinRole.ARCHITECT.value,
            name="System Architect",
            description="Designs high-level software architecture, system interfaces, state machines, and data schemas.",
            system_prompt=(
                "You are the Principal System Architect in Project AURA. Your responsibility is to analyze requirements, "
                "design robust architectural components, identify dependency boundaries, and ensure modular, scalable software designs."
            ),
            allowed_skills=(),
            required_capabilities=(ModelCapability.REASONING,),
            temperature=0.3,
        ),
        AgentRole(
            role_id=BuiltinRole.RESEARCHER.value,
            name="Deep Researcher",
            description="Conducts thorough multi-source research, gathers verifiable evidence, and synthesizes factual findings.",
            system_prompt=(
                "You are the Deep Research Specialist in Project AURA. Your responsibility is to discover accurate information, "
                "verify empirical claims against reliable sources, and produce concise, factual evidence summaries with citations."
            ),
            allowed_skills=("web_search", "research", "fetch_url"),
            required_capabilities=(ModelCapability.REASONING,),
            temperature=0.3,
        ),
        AgentRole(
            role_id=BuiltinRole.CODER.value,
            name="Software Engineer",
            description="Implements clean, production-grade Python code, algorithms, unit tests, and refactors.",
            system_prompt=(
                "You are the Senior Software Engineer in Project AURA. Your responsibility is to write clean, type-safe, "
                "tested, and efficient implementation code following standard architectural conventions and strict policy rules."
            ),
            allowed_skills=("calculator", "echo"),
            required_capabilities=(ModelCapability.CODING, ModelCapability.REASONING),
            temperature=0.2,
        ),
        AgentRole(
            role_id=BuiltinRole.REVIEWER.value,
            name="Code & Plan Reviewer",
            description="Audits plans, algorithms, and code for correctness, edge cases, performance, and best practices.",
            system_prompt=(
                "You are the Technical Reviewer in Project AURA. Your responsibility is to critically evaluate code, plans, and "
                "proposals, identify flaws, hallucinated assumptions, and propose concrete, actionable improvements."
            ),
            allowed_skills=(),
            required_capabilities=(ModelCapability.REASONING,),
            temperature=0.2,
        ),
        AgentRole(
            role_id=BuiltinRole.SECURITY_AUDITOR.value,
            name="Security & Policy Auditor",
            description="Evaluates system plans and actions for security vulnerabilities, policy violations, SSRF risks, and taint safety.",
            system_prompt=(
                "You are the Lead Security & Compliance Auditor in Project AURA. Your responsibility is to scrutinize execution plans "
                "for privilege escalation, untrusted data leakage, SSRF risks, unauthorized tool invocations, and policy boundaries."
            ),
            allowed_skills=(),
            required_capabilities=(ModelCapability.REASONING,),
            temperature=0.1,
        ),
        AgentRole(
            role_id=BuiltinRole.DATA_ANALYST.value,
            name="Data & Metrics Analyst",
            description="Performs quantitative calculations, metrics analysis, performance benchmarking, and evaluation.",
            system_prompt=(
                "You are the Data & Metrics Analyst in Project AURA. Your responsibility is to analyze numerical outputs, "
                "evaluate statistical benchmarks, perform precise mathematical computations, and validate data consistency."
            ),
            allowed_skills=("calculator",),
            required_capabilities=(ModelCapability.REASONING,),
            temperature=0.2,
        ),
    ]


class RoleRegistry:
    """Thread-safe catalog and lifecycle manager for specialized AgentRole personas."""

    def __init__(self, max_roles: int = 100, register_defaults: bool = True):
        if not isinstance(max_roles, int) or max_roles <= 0:
            raise ValueError("max_roles must be a positive integer.")
        self.max_roles = max_roles
        self._lock = threading.RLock()
        self._roles: dict[str, AgentRole] = {}

        if register_defaults:
            for role in get_default_builtin_roles():
                self._roles[role.role_id] = role

    def register_role(self, role: AgentRole, overwrite: bool = False) -> None:
        """Register a new AgentRole persona."""
        if not isinstance(role, AgentRole):
            raise TypeError("role must be an instance of AgentRole.")

        with self._lock:
            if role.role_id in self._roles and not overwrite:
                raise ValueError(f"AgentRole with role_id '{role.role_id}' is already registered.")
            if role.role_id not in self._roles and len(self._roles) >= self.max_roles:
                raise ValueError(f"Cannot register role '{role.role_id}': maximum role limit ({self.max_roles}) reached.")
            self._roles[role.role_id] = role
            logger.info("Registered AgentRole: %s (%s)", role.role_id, role.name)

    register = register_role

    def get_role(self, role_id: str) -> AgentRole:
        """Retrieve a registered AgentRole by its role_id."""
        if not isinstance(role_id, str) or not role_id.strip():
            raise ValueError("role_id must be a non-empty string.")
        norm_id = role_id.strip().lower()

        with self._lock:
            if norm_id not in self._roles:
                raise KeyError(f"AgentRole '{norm_id}' is not registered.")
            return self._roles[norm_id]

    def has_role(self, role_id: str) -> bool:
        """Check whether a role_id exists in the registry."""
        if not isinstance(role_id, str) or not role_id.strip():
            return False
        norm_id = role_id.strip().lower()
        with self._lock:
            return norm_id in self._roles

    def list_roles(self) -> list[AgentRole]:
        """List all registered AgentRole personas."""
        with self._lock:
            return list(self._roles.values())

    def unregister_role(self, role_id: str) -> bool:
        """Remove a registered role by ID (cannot remove built-in roles unless explicit)."""
        if not isinstance(role_id, str) or not role_id.strip():
            return False
        norm_id = role_id.strip().lower()

        with self._lock:
            if norm_id in self._roles:
                del self._roles[norm_id]
                logger.info("Unregistered AgentRole: %s", norm_id)
                return True
            return False

    def clear(self) -> None:
        """Clear all registered roles."""
        with self._lock:
            self._roles.clear()
