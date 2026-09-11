"""Milestone 26: Autonomous Dynamic Skill & Tool Synthesizer.

Synthesizes programmatic Python tools and declarative composite skill pipelines
from structured specifications with AST validation and CAS artifact integration.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Sequence

from core.artifact_manager import ArtifactManager
from core.artifact_types import ArtifactType
from core.code_sandbox import CodeSandboxValidator
from core.skill_types import (
    CompositeSkill,
    SecurityAuditReport,
    SkillLifecycleState,
    SkillStep,
    SynthesizedSkill,
    TestVector,
    compute_code_hash,
    sanitize_skill_metadata,
)
from core.trace_types import SpanKind, SpanStatus
from core.tracing import Tracer

logger = logging.getLogger("aura.skill_synthesis")

MAX_COMPOSITE_STEPS = 10
MAX_SOURCE_CODE_BYTES = 32768


class SkillSynthesizer:
    """Autonomous engine for synthesizing and assembling safe, verifiable skills and tools."""

    def __init__(
        self,
        validator: CodeSandboxValidator | None = None,
        artifact_manager: ArtifactManager | None = None,
        tracer: Tracer | None = None,
    ):
        self.validator = validator if validator is not None else CodeSandboxValidator(max_code_bytes=MAX_SOURCE_CODE_BYTES)
        self.artifact_manager = artifact_manager
        self.tracer = tracer

    def synthesize_tool(
        self,
        name: str,
        description: str,
        source_code: str,
        entrypoint_function: str = "execute",
        test_vectors: Sequence[TestVector | dict[str, Any]] = (),
        required_capabilities: Sequence[str] = (),
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        author_role_id: str = "coder",
        originating_goal_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SynthesizedSkill:
        """Synthesize a programmatic Python tool from source code and validation test vectors."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Skill name must be a non-empty string.")
        if not isinstance(source_code, str) or not source_code.strip():
            raise ValueError("source_code must be a non-empty string.")

        span = None
        if self.tracer is not None:
            span = self.tracer.start_span(
                name=f"skill_synthesis.tool.{name.strip().lower()}",
                kind=SpanKind.INTERNAL,
                attributes={"skill_name": name, "author_role": author_role_id},
            )

        try:
            # 1. Validate AST Security
            sec_report = self.validator.validate_source(
                source_code=source_code,
                entrypoint_function=entrypoint_function,
            )

            if not sec_report.is_safe:
                violation_summary = "; ".join(sec_report.violations)
                logger.warning(
                    "SkillSynthesizer: AST validation failed for skill '%s': %s",
                    name,
                    violation_summary,
                )

            # 2. Parse test vectors
            parsed_vectors: list[TestVector] = []
            for tv in test_vectors:
                if isinstance(tv, TestVector):
                    parsed_vectors.append(tv)
                elif isinstance(tv, dict):
                    parsed_vectors.append(TestVector.from_dict(tv))
                else:
                    raise TypeError("test_vectors elements must be TestVector or dict.")

            ast_hash = compute_code_hash(source_code)

            # 3. Optional CAS Artifact Registration (M24)
            artifact_id = None
            if self.artifact_manager is not None:
                try:
                    if hasattr(self.artifact_manager, "store_artifact"):
                        artifact = self.artifact_manager.store_artifact(
                            name=f"skill_{name.strip().lower()}_v1.py",
                            content=source_code,
                            artifact_type=ArtifactType.CODE,
                            producer_goal_id=originating_goal_id,
                            creator_role_id=author_role_id,
                            metadata={
                                "ast_hash": ast_hash,
                                "entrypoint": entrypoint_function,
                                "skill_name": name.strip().lower(),
                            },
                        )
                        artifact_id = artifact.artifact_id
                    elif hasattr(self.artifact_manager, "create_artifact"):
                        artifact = self.artifact_manager.create_artifact(
                            name=f"skill_{name.strip().lower()}_v1.py",
                            content=source_code,
                            artifact_type=ArtifactType.CODE,
                            originating_goal_id=originating_goal_id,
                            created_by=author_role_id,
                            metadata={
                                "ast_hash": ast_hash,
                                "entrypoint": entrypoint_function,
                                "skill_name": name.strip().lower(),
                            },
                        )
                        artifact_id = artifact.artifact_id
                except Exception as ex:
                    logger.debug("Artifact registration skipped during synthesis: %s", ex)

            clean_meta = sanitize_skill_metadata(metadata or {})
            clean_meta["security_audit"] = sec_report.to_dict()

            synthesized = SynthesizedSkill(
                name=name,
                description=description,
                source_code=source_code,
                ast_hash=ast_hash,
                lifecycle_state=SkillLifecycleState.SANDBOX_TESTED if sec_report.is_safe else SkillLifecycleState.DRAFT,
                entrypoint_function=entrypoint_function,
                required_capabilities=tuple(required_capabilities),
                input_schema=input_schema or {},
                output_schema=output_schema or {},
                test_vectors=tuple(parsed_vectors),
                author_role_id=author_role_id,
                originating_goal_id=originating_goal_id,
                artifact_id=artifact_id,
                metadata=clean_meta,
            )

            if span is not None:
                span.set_attribute("ast_hash", ast_hash)
                span.set_attribute("is_safe", sec_report.is_safe)
                span.set_status(SpanStatus.OK)

            return synthesized
        except Exception as e:
            if span is not None:
                span.record_exception(e)
                span.set_status(SpanStatus.ERROR)
            raise
        finally:
            if span is not None:
                span.end()

    def synthesize_composite_skill(
        self,
        name: str,
        description: str,
        steps: Sequence[SkillStep | dict[str, Any]],
        author_role_id: str = "architect",
        originating_goal_id: str | None = None,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CompositeSkill:
        """Synthesize a declarative CompositeSkill chaining multiple existing tools or skills."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Composite skill name must be a non-empty string.")
        if not steps:
            raise ValueError("Composite skill must contain at least one step.")
        if len(steps) > MAX_COMPOSITE_STEPS:
            raise ValueError(f"Composite skill exceeds maximum allowed steps ({MAX_COMPOSITE_STEPS}).")

        parsed_steps: list[SkillStep] = []
        for s in steps:
            if isinstance(s, SkillStep):
                parsed_steps.append(s)
            elif isinstance(s, dict):
                parsed_steps.append(SkillStep.from_dict(s))
            else:
                raise TypeError("steps elements must be SkillStep instances or dicts.")

        clean_meta = sanitize_skill_metadata(metadata or {})

        composite = CompositeSkill(
            name=name,
            description=description,
            steps=tuple(parsed_steps),
            lifecycle_state=SkillLifecycleState.DRAFT,
            author_role_id=author_role_id,
            originating_goal_id=originating_goal_id,
            input_schema=input_schema or {},
            output_schema=output_schema or {},
            metadata=clean_meta,
        )
        return composite

    @staticmethod
    def generate_tool_template(function_name: str = "execute", docstring: str = "") -> str:
        """Generate a compliant, safe Python tool boilerplate template."""
        doc = docstring or "Process input_data string and return result string."
        return (
            "def " + function_name + "(input_data: str) -> str:\n"
            "    \"\"\"" + doc + "\"\"\"\n"
            "    # Safe operations only\n"
            "    result = input_data.strip()\n"
            "    return str(result)\n"
        )
