"""Milestone 26: Dynamic Skill Registry & Capability Lifecycle Manager.

Thread-safe catalog managing synthesized skills, composite pipelines, versioning,
telemetry monitoring, auto-deprecation, and checkpoint serialization.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Sequence

from core.code_sandbox import CodeSandboxValidator, SandboxedToolExecutor
from core.skill_registry import Skill, SkillRegistry
from core.skill_types import (
    CompositeSkill,
    DynamicTool,
    SkillLifecycleState,
    SynthesizedSkill,
)
from core.tool_registry import ToolRegistry
from core.trace_types import SpanKind, SpanStatus
from core.tracing import Tracer

logger = logging.getLogger("aura.dynamic_skill_registry")


class DynamicSkillRegistry:
    """Thread-safe catalog and lifecycle manager for dynamically synthesized skills."""

    def __init__(
        self,
        max_skills: int = 100,
        auto_deprecation_threshold: float = 0.30,
        min_invocations_for_deprecation: int = 5,
        executor: SandboxedToolExecutor | None = None,
        tool_registry: ToolRegistry | None = None,
        tracer: Tracer | None = None,
    ):
        if not isinstance(max_skills, int) or max_skills <= 0:
            raise ValueError("max_skills must be a positive integer.")
        self.max_skills = max_skills
        self.auto_deprecation_threshold = max(0.05, min(1.0, float(auto_deprecation_threshold)))
        self.min_invocations_for_deprecation = max(1, int(min_invocations_for_deprecation))
        self._lock = threading.RLock()

        self._skills: dict[str, SynthesizedSkill] = {}
        self._composite_skills: dict[str, CompositeSkill] = {}
        self._dynamic_tools: dict[str, DynamicTool] = {}
        self._executor = executor if executor is not None else SandboxedToolExecutor()
        self._tool_registry = tool_registry
        self._tracer = tracer

    def register_skill(
        self,
        skill: SynthesizedSkill,
        activate: bool = True,
        overwrite: bool = False,
    ) -> None:
        """Register a new synthesized skill in the dynamic catalog."""
        if not isinstance(skill, SynthesizedSkill):
            raise TypeError("skill must be an instance of SynthesizedSkill.")

        with self._lock:
            key = skill.name.strip().lower()
            if key in self._skills and not overwrite:
                raise ValueError(f"Dynamic skill with name '{key}' is already registered.")
            if key not in self._skills and len(self._skills) >= self.max_skills:
                raise ValueError(
                    f"Cannot register skill '{key}': registry limit ({self.max_skills}) reached."
                )

            if activate and skill.is_verified:
                skill.transition_to(SkillLifecycleState.ACTIVE)

            self._skills[key] = skill

            # Create DynamicTool adapter and expose
            tool_adapter = DynamicTool(
                skill=skill,
                executor_func=self._execute_tool_adapter,
            )
            self._dynamic_tools[key] = tool_adapter

            if self._tool_registry is not None and skill.lifecycle_state == SkillLifecycleState.ACTIVE:
                try:
                    if not self._tool_registry.has(key):
                        self._tool_registry.register(tool_adapter)
                except Exception as ex:
                    logger.debug("Tool registry registration skipped: %s", ex)

            logger.info("Registered dynamic skill '%s' [state=%s]", key, skill.lifecycle_state.value)

            if self._tracer is not None:
                span = self._tracer.start_span(
                    name=f"dynamic_registry.register.{key}",
                    kind=SpanKind.INTERNAL,
                    attributes={"skill_name": key, "state": skill.lifecycle_state.value},
                )
                span.end()

    def register_composite_skill(
        self,
        skill: CompositeSkill,
        activate: bool = True,
        overwrite: bool = False,
    ) -> None:
        """Register a declarative composite skill."""
        if not isinstance(skill, CompositeSkill):
            raise TypeError("skill must be an instance of CompositeSkill.")

        with self._lock:
            key = skill.name.strip().lower()
            if key in self._composite_skills and not overwrite:
                raise ValueError(f"Composite skill with name '{key}' is already registered.")

            if activate:
                skill.lifecycle_state = SkillLifecycleState.ACTIVE
            self._composite_skills[key] = skill
            logger.info("Registered composite skill '%s' [steps=%d]", key, len(skill.steps))

    def get_skill(self, name: str) -> SynthesizedSkill:
        """Retrieve a registered synthesized skill by name."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Skill name must be a non-empty string.")
        key = name.strip().lower()

        with self._lock:
            if key not in self._skills:
                raise KeyError(f"Dynamic skill '{key}' is not registered.")
            return self._skills[key]

    def get_composite_skill(self, name: str) -> CompositeSkill:
        """Retrieve a registered composite skill by name."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Composite skill name must be a non-empty string.")
        key = name.strip().lower()

        with self._lock:
            if key not in self._composite_skills:
                raise KeyError(f"Composite skill '{key}' is not registered.")
            return self._composite_skills[key]

    def get_tool(self, name: str) -> DynamicTool:
        """Retrieve the DynamicTool adapter for a registered skill."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Tool name must be a non-empty string.")
        key = name.strip().lower()

        with self._lock:
            if key not in self._dynamic_tools:
                raise KeyError(f"Dynamic tool adapter '{key}' is not registered.")
            return self._dynamic_tools[key]

    def has_skill(self, name: str) -> bool:
        """Check whether a synthesized skill is registered."""
        if not isinstance(name, str) or not name.strip():
            return False
        key = name.strip().lower()
        with self._lock:
            return key in self._skills or key in self._composite_skills

    def has(self, name: str) -> bool:
        return self.has_skill(name)

    def list_skills(
        self,
        state: SkillLifecycleState | None = None,
    ) -> list[SynthesizedSkill]:
        """List all registered synthesized skills, optionally filtered by state."""
        with self._lock:
            if state is None:
                return list(self._skills.values())
            return [s for s in self._skills.values() if s.lifecycle_state == state]

    def list_composite_skills(self) -> list[CompositeSkill]:
        """List all registered composite skills."""
        with self._lock:
            return list(self._composite_skills.values())

    def deprecate_skill(self, name: str, reason: str = "Manual deprecation") -> bool:
        """Transition a skill to DEPRECATED state."""
        if not isinstance(name, str) or not name.strip():
            return False
        key = name.strip().lower()

        with self._lock:
            if key in self._skills:
                skill = self._skills[key]
                skill.transition_to(SkillLifecycleState.DEPRECATED)
                skill.metadata["deprecation_reason"] = reason
                logger.warning("Dynamic skill '%s' deprecated: %s", key, reason)
                return True
            return False

    def revoke_skill(self, name: str, reason: str = "Security revocation") -> bool:
        """Revoke and disable a skill due to policy/security violations."""
        if not isinstance(name, str) or not name.strip():
            return False
        key = name.strip().lower()

        with self._lock:
            if key in self._skills:
                skill = self._skills[key]
                skill.transition_to(SkillLifecycleState.REVOKED)
                skill.metadata["revocation_reason"] = reason
                logger.error("Dynamic skill '%s' REVOKED: %s", key, reason)
                return True
            return False

    def _execute_tool_adapter(self, skill_name: str, input_data: str) -> str:
        """Internal callback invoked by DynamicTool adapters."""
        return self.execute_skill(skill_name, input_data)

    def execute_skill(
        self,
        name: str,
        input_data: str,
        timeout: float | None = None,
    ) -> str:
        """Execute a registered dynamic skill in the isolated sandbox with telemetry tracking."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Skill name must be a non-empty string.")
        key = name.strip().lower()

        with self._lock:
            if key not in self._skills:
                if key in self._composite_skills:
                    return self.execute_composite_skill(key, input_data)
                raise KeyError(f"Dynamic skill '{key}' is not registered.")
            skill = self._skills[key]

        if skill.lifecycle_state != SkillLifecycleState.ACTIVE:
            raise PermissionError(
                f"Cannot execute dynamic skill '{key}': lifecycle state is '{skill.lifecycle_state.value}' (must be 'active')."
            )

        t_start = time.perf_counter()
        is_err = False
        try:
            output = self._executor.execute(
                source_code=skill.source_code,
                input_data=input_data,
                entrypoint_function=skill.entrypoint_function,
                timeout=timeout,
            )
            return output
        except Exception as ex:
            is_err = True
            raise
        finally:
            latency_ms = (time.perf_counter() - t_start) * 1000.0
            with self._lock:
                skill.record_invocation(latency_ms=latency_ms, is_error=is_err)

                # Check auto-deprecation policy
                if (
                    skill.invocation_count >= self.min_invocations_for_deprecation
                    and skill.error_rate >= self.auto_deprecation_threshold
                ):
                    skill.transition_to(SkillLifecycleState.DEPRECATED)
                    skill.metadata["auto_deprecation_reason"] = (
                        f"Error rate ({skill.error_rate:.1%}) exceeded threshold ({self.auto_deprecation_threshold:.1%}) "
                        f"over {skill.invocation_count} invocations."
                    )
                    logger.warning(
                        "Auto-deprecated dynamic skill '%s' due to high error rate (%.1f%%)",
                        key,
                        skill.error_rate * 100,
                    )

    def execute_composite_skill(
        self,
        name: str,
        input_data: str,
        tool_executor: Any | None = None,
    ) -> str:
        """Execute a composite skill pipeline sequentially."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Composite skill name must be a non-empty string.")
        key = name.strip().lower()

        with self._lock:
            if key not in self._composite_skills:
                raise KeyError(f"Composite skill '{key}' is not registered.")
            composite = self._composite_skills[key]

        if composite.lifecycle_state != SkillLifecycleState.ACTIVE:
            raise PermissionError(
                f"Cannot execute composite skill '{key}': lifecycle state is '{composite.lifecycle_state.value}' (must be 'active')."
            )

        current_input = input_data
        context: dict[str, str] = {"input": input_data, "prev": input_data}

        for step in composite.steps:
            # Build step input from template
            step_in = step.input_template.format(**context) if "{" in step.input_template else current_input

            # Execute step via internal skill, dynamic tool, or fallback
            step_tool = step.skill_or_tool_name.strip().lower()
            if self.has_skill(step_tool):
                step_out = self.execute_skill(step_tool, step_in, timeout=step.timeout_seconds)
            elif tool_executor is not None and hasattr(tool_executor, "execute"):
                step_out = tool_executor.execute(step_tool, step_in, timeout=step.timeout_seconds)
            else:
                raise RuntimeError(
                    f"Composite skill step '{step.step_id}' references unknown tool or skill: '{step_tool}'"
                )

            current_input = str(step_out)
            context["prev"] = current_input
            context[step.output_key] = current_input

        return current_input

    def to_dict(self) -> dict[str, Any]:
        """Serialize complete dynamic registry state for checkpoint persistence."""
        with self._lock:
            return {
                "skills": [s.to_dict() for s in self._skills.values()],
                "composite_skills": [cs.to_dict() for cs in self._composite_skills.values()],
                "max_skills": self.max_skills,
                "auto_deprecation_threshold": self.auto_deprecation_threshold,
                "min_invocations_for_deprecation": self.min_invocations_for_deprecation,
            }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        validator: CodeSandboxValidator | None = None,
        executor: SandboxedToolExecutor | None = None,
        tracer: Tracer | None = None,
    ) -> DynamicSkillRegistry:
        """Deserialize dynamic registry from checkpoint snapshot with AST code re-verification."""
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")

        val = validator if validator is not None else CodeSandboxValidator()
        exec_engine = executor if executor is not None else SandboxedToolExecutor(validator=val)

        registry = cls(
            max_skills=int(data.get("max_skills", 100)),
            auto_deprecation_threshold=float(data.get("auto_deprecation_threshold", 0.30)),
            min_invocations_for_deprecation=int(data.get("min_invocations_for_deprecation", 5)),
            executor=exec_engine,
            tracer=tracer,
        )

        for s_data in data.get("skills", []):
            if isinstance(s_data, dict):
                try:
                    skill = SynthesizedSkill.from_dict(s_data)
                    # Security invariant check: re-verify code before registering
                    sec = val.validate_source(skill.source_code, skill.entrypoint_function)
                    if not sec.is_safe:
                        logger.warning(
                            "Corrupt or unsafe skill '%s' rejected during checkpoint restore: %s",
                            skill.name,
                            sec.violations,
                        )
                        skill.transition_to(SkillLifecycleState.REVOKED)
                    registry.register_skill(skill, activate=(skill.lifecycle_state == SkillLifecycleState.ACTIVE), overwrite=True)
                except Exception as ex:
                    logger.debug("Error restoring dynamic skill: %s", ex)

        for cs_data in data.get("composite_skills", []):
            if isinstance(cs_data, dict):
                try:
                    cs = CompositeSkill.from_dict(cs_data)
                    registry.register_composite_skill(cs, activate=(cs.lifecycle_state == SkillLifecycleState.ACTIVE), overwrite=True)
                except Exception as ex:
                    logger.debug("Error restoring composite skill: %s", ex)

        return registry
