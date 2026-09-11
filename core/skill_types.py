"""Milestone 26: Core types, enums, and data contracts for dynamic skill synthesis,
sandboxed tool generation, and trajectory-verified capability evolution.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from core.capability_registry import ModelCapability
from core.provenance import TaintedValue, is_tainted, unwrap_tainted, wrap_tainted
from interfaces.tool import ToolInterface


# Forbidden metadata keys that could attempt privilege escalation
FORBIDDEN_METADATA_KEYS = frozenset({
    "is_authorized",
    "is_admin",
    "approved",
    "approval_status",
    "is_approved",
    "auto_approve",
    "permission",
    "authorized",
    "bypass_policy",
    "role_override",
    "system_override",
    "sudo",
    "elevated_privileges",
})


def sanitize_skill_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Recursively sanitize metadata dictionary to remove forbidden authorization keys."""
    if not isinstance(meta, dict):
        raise TypeError("metadata must be a dictionary.")

    cleaned: dict[str, Any] = {}
    for k, v in meta.items():
        k_str = str(k).strip()
        if k_str.lower() in FORBIDDEN_METADATA_KEYS:
            continue
        if callable(v):
            continue
        if isinstance(v, dict):
            cleaned[k_str] = sanitize_skill_metadata(v)
        elif isinstance(v, (list, tuple)):
            cleaned[k_str] = [
                sanitize_skill_metadata(item) if isinstance(item, dict) else (str(item) if not callable(item) else "")
                for item in v
            ]
        elif isinstance(v, (str, int, float, bool)) or v is None:
            cleaned[k_str] = v
        elif isinstance(v, TaintedValue):
            cleaned[k_str] = v
        else:
            cleaned[k_str] = repr(v)
    return cleaned


def compute_code_hash(source_code: str) -> str:
    """Compute deterministic SHA-256 hash of normalized source code."""
    if not isinstance(source_code, str):
        raise TypeError("source_code must be a string.")
    normalized = "\n".join(line.rstrip() for line in source_code.strip().splitlines())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class SkillLifecycleState(str, Enum):
    """Lifecycle state machine for dynamically synthesized skills."""

    DRAFT = "draft"                    # Initial synthesized specification / code
    SANDBOX_TESTED = "sandbox_tested"  # Passed AST security checks & isolated sandbox execution
    VERIFIED = "verified"              # Passed full trajectory verification test suite
    ACTIVE = "active"                  # Registered and available for agent discovery & execution
    DEPRECATED = "deprecated"          # Marked obsolete or high error rate; excluded from new plans
    REVOKED = "revoked"                # Explicitly disabled due to security violation or policy failure


@dataclass(frozen=True)
class TestVector:
    """Deterministic test case specification for sandboxed skill verification."""

    __test__ = False

    input_data: str
    expected_output_contains: tuple[str, ...] = field(default_factory=tuple)
    expected_output_regex: str | None = None
    expected_schema: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 2.0
    description: str = ""

    def __post_init__(self):
        if not isinstance(self.input_data, str):
            raise TypeError("input_data must be a string.")

        if isinstance(self.expected_output_contains, (list, tuple, set, frozenset)):
            cleaned_contains = tuple(str(x) for x in self.expected_output_contains if str(x))
            object.__setattr__(self, "expected_output_contains", cleaned_contains)
        else:
            raise TypeError("expected_output_contains must be a sequence of strings.")

        if self.expected_output_regex is not None and not isinstance(self.expected_output_regex, str):
            raise TypeError("expected_output_regex must be a string or None.")

        if not isinstance(self.expected_schema, dict):
            raise TypeError("expected_schema must be a dictionary.")

        if not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive number.")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

        if not isinstance(self.description, str):
            raise TypeError("description must be a string.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_data": self.input_data,
            "expected_output_contains": list(self.expected_output_contains),
            "expected_output_regex": self.expected_output_regex,
            "expected_schema": dict(self.expected_schema),
            "timeout_seconds": self.timeout_seconds,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TestVector:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        return cls(
            input_data=str(data.get("input_data", "")),
            expected_output_contains=tuple(data.get("expected_output_contains", ())),
            expected_output_regex=data.get("expected_output_regex"),
            expected_schema=dict(data.get("expected_schema", {})),
            timeout_seconds=float(data.get("timeout_seconds", 2.0)),
            description=str(data.get("description", "")),
        )


@dataclass(frozen=True)
class SecurityAuditReport:
    """Detailed audit report produced by the AST security sandbox validator."""

    is_safe: bool
    ast_hash: str
    violations: tuple[str, ...] = field(default_factory=tuple)
    allowed_imports: tuple[str, ...] = field(default_factory=tuple)
    complexity_score: int = 0
    checked_nodes_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.is_safe, bool):
            raise TypeError("is_safe must be a boolean.")
        if not isinstance(self.ast_hash, str) or not self.ast_hash.strip():
            raise ValueError("ast_hash must be a non-empty string.")

        if isinstance(self.violations, (list, tuple, set, frozenset)):
            object.__setattr__(self, "violations", tuple(str(v) for v in self.violations))
        else:
            raise TypeError("violations must be a sequence of strings.")

        if isinstance(self.allowed_imports, (list, tuple, set, frozenset)):
            object.__setattr__(self, "allowed_imports", tuple(str(i) for i in self.allowed_imports))
        else:
            raise TypeError("allowed_imports must be a sequence of strings.")

        if not isinstance(self.complexity_score, int) or self.complexity_score < 0:
            raise ValueError("complexity_score must be a non-negative integer.")

        if not isinstance(self.checked_nodes_count, int) or self.checked_nodes_count < 0:
            raise ValueError("checked_nodes_count must be a non-negative integer.")

        object.__setattr__(self, "metadata", sanitize_skill_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_safe": self.is_safe,
            "ast_hash": self.ast_hash,
            "violations": list(self.violations),
            "allowed_imports": list(self.allowed_imports),
            "complexity_score": self.complexity_score,
            "checked_nodes_count": self.checked_nodes_count,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SecurityAuditReport:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        return cls(
            is_safe=bool(data.get("is_safe", False)),
            ast_hash=str(data.get("ast_hash", "")),
            violations=tuple(data.get("violations", ())),
            allowed_imports=tuple(data.get("allowed_imports", ())),
            complexity_score=int(data.get("complexity_score", 0)),
            checked_nodes_count=int(data.get("checked_nodes_count", 0)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class SkillVerificationReport:
    """Benchmark and trajectory verification report for a synthesized skill."""

    skill_name: str
    passed: bool
    pass_rate: float
    total_tests: int
    passed_tests: int
    avg_latency_ms: float
    security_report: SecurityAuditReport
    verified_at: float = field(default_factory=time.time)
    test_results: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    invariants_verified: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.skill_name, str) or not self.skill_name.strip():
            raise ValueError("skill_name must be a non-empty string.")
        object.__setattr__(self, "skill_name", self.skill_name.strip())

        if not isinstance(self.passed, bool):
            raise TypeError("passed must be a boolean.")

        if not isinstance(self.pass_rate, (int, float)) or not (0.0 <= self.pass_rate <= 1.0):
            raise ValueError("pass_rate must be a float between 0.0 and 1.0.")
        object.__setattr__(self, "pass_rate", float(self.pass_rate))

        if not isinstance(self.total_tests, int) or self.total_tests < 0:
            raise ValueError("total_tests must be a non-negative integer.")

        if not isinstance(self.passed_tests, int) or self.passed_tests < 0:
            raise ValueError("passed_tests must be a non-negative integer.")

        if not isinstance(self.avg_latency_ms, (int, float)) or self.avg_latency_ms < 0:
            raise ValueError("avg_latency_ms must be non-negative.")
        object.__setattr__(self, "avg_latency_ms", float(self.avg_latency_ms))

        if not isinstance(self.security_report, SecurityAuditReport):
            raise TypeError("security_report must be an instance of SecurityAuditReport.")

        if not isinstance(self.verified_at, (int, float)):
            raise TypeError("verified_at must be numeric.")

        if isinstance(self.test_results, (list, tuple)):
            object.__setattr__(self, "test_results", tuple(dict(r) for r in self.test_results if isinstance(r, dict)))
        else:
            raise TypeError("test_results must be a sequence of dicts.")

        if isinstance(self.invariants_verified, (list, tuple, set, frozenset)):
            object.__setattr__(self, "invariants_verified", tuple(str(inv) for inv in self.invariants_verified))
        else:
            raise TypeError("invariants_verified must be a sequence of strings.")

        object.__setattr__(self, "metadata", sanitize_skill_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "passed": self.passed,
            "pass_rate": self.pass_rate,
            "total_tests": self.total_tests,
            "passed_tests": self.passed_tests,
            "avg_latency_ms": self.avg_latency_ms,
            "security_report": self.security_report.to_dict(),
            "verified_at": self.verified_at,
            "test_results": list(self.test_results),
            "invariants_verified": list(self.invariants_verified),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillVerificationReport:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        sec_data = data.get("security_report", {})
        sec_report = SecurityAuditReport.from_dict(sec_data) if isinstance(sec_data, dict) else SecurityAuditReport(is_safe=False, ast_hash="invalid")
        return cls(
            skill_name=str(data.get("skill_name", "")),
            passed=bool(data.get("passed", False)),
            pass_rate=float(data.get("pass_rate", 0.0)),
            total_tests=int(data.get("total_tests", 0)),
            passed_tests=int(data.get("passed_tests", 0)),
            avg_latency_ms=float(data.get("avg_latency_ms", 0.0)),
            security_report=sec_report,
            verified_at=float(data.get("verified_at", time.time())),
            test_results=tuple(data.get("test_results", ())),
            invariants_verified=tuple(data.get("invariants_verified", ())),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class SynthesizedSkill:
    """Represents a dynamically synthesized capability with full AST code provenance,
    lifecycle state machine, and trajectory verification reports."""

    name: str
    description: str
    source_code: str
    ast_hash: str
    lifecycle_state: SkillLifecycleState = SkillLifecycleState.DRAFT
    entrypoint_function: str = "execute"
    required_capabilities: tuple[str, ...] = field(default_factory=tuple)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    test_vectors: tuple[TestVector, ...] = field(default_factory=tuple)
    author_role_id: str = "coder"
    originating_goal_id: str | None = None
    artifact_id: str | None = None
    verification_report: SkillVerificationReport | None = None
    version: int = 1
    invocation_count: int = 0
    error_count: int = 0
    total_latency_ms: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Skill name must be a non-empty string.")
        self.name = self.name.strip().lower()

        if not isinstance(self.description, str):
            raise TypeError("description must be a string.")
        self.description = self.description.strip()

        if not isinstance(self.source_code, str) or not self.source_code.strip():
            raise ValueError("source_code must be a non-empty string.")

        if not isinstance(self.ast_hash, str) or not self.ast_hash.strip():
            self.ast_hash = compute_code_hash(self.source_code)
        else:
            self.ast_hash = self.ast_hash.strip()

        if isinstance(self.lifecycle_state, str):
            self.lifecycle_state = SkillLifecycleState(self.lifecycle_state)
        elif not isinstance(self.lifecycle_state, SkillLifecycleState):
            raise TypeError("lifecycle_state must be an instance of SkillLifecycleState.")

        if not isinstance(self.entrypoint_function, str) or not self.entrypoint_function.strip():
            self.entrypoint_function = "execute"
        else:
            self.entrypoint_function = self.entrypoint_function.strip()

        if isinstance(self.required_capabilities, (list, tuple, set, frozenset)):
            caps: list[str] = []
            for c in self.required_capabilities:
                if isinstance(c, ModelCapability):
                    caps.append(c.value)
                elif isinstance(c, str) and c.strip():
                    caps.append(c.strip().lower())
            self.required_capabilities = tuple(dict.fromkeys(caps))
        else:
            raise TypeError("required_capabilities must be a sequence of strings or ModelCapability.")

        if not isinstance(self.input_schema, dict):
            raise TypeError("input_schema must be a dictionary.")
        if not isinstance(self.output_schema, dict):
            raise TypeError("output_schema must be a dictionary.")

        if isinstance(self.test_vectors, (list, tuple)):
            vectors: list[TestVector] = []
            for tv in self.test_vectors:
                if isinstance(tv, TestVector):
                    vectors.append(tv)
                elif isinstance(tv, dict):
                    vectors.append(TestVector.from_dict(tv))
                else:
                    raise TypeError("test_vectors elements must be TestVector instances or dicts.")
            self.test_vectors = tuple(vectors)
        else:
            raise TypeError("test_vectors must be a sequence of TestVector instances.")

        if not isinstance(self.author_role_id, str) or not self.author_role_id.strip():
            self.author_role_id = "coder"
        else:
            self.author_role_id = self.author_role_id.strip().lower()

        if self.originating_goal_id is not None and not isinstance(self.originating_goal_id, str):
            raise TypeError("originating_goal_id must be a string or None.")

        if self.artifact_id is not None and not isinstance(self.artifact_id, str):
            raise TypeError("artifact_id must be a string or None.")

        if self.verification_report is not None and not isinstance(self.verification_report, SkillVerificationReport):
            raise TypeError("verification_report must be an instance of SkillVerificationReport or None.")

        if not isinstance(self.version, int) or self.version < 1:
            raise ValueError("version must be a positive integer.")

        if not isinstance(self.invocation_count, int) or self.invocation_count < 0:
            raise ValueError("invocation_count must be a non-negative integer.")

        if not isinstance(self.error_count, int) or self.error_count < 0:
            raise ValueError("error_count must be a non-negative integer.")

        if not isinstance(self.total_latency_ms, (int, float)) or self.total_latency_ms < 0:
            raise ValueError("total_latency_ms must be non-negative.")
        self.total_latency_ms = float(self.total_latency_ms)

        if not isinstance(self.created_at, (int, float)):
            raise TypeError("created_at must be numeric.")
        if not isinstance(self.updated_at, (int, float)):
            raise TypeError("updated_at must be numeric.")

        self.metadata = sanitize_skill_metadata(self.metadata)

    @property
    def error_rate(self) -> float:
        """Calculate the real-time error rate for telemetry tracking."""
        if self.invocation_count == 0:
            return 0.0
        return min(1.0, self.error_count / self.invocation_count)

    @property
    def avg_latency_ms(self) -> float:
        """Calculate average execution latency in milliseconds."""
        if self.invocation_count == 0:
            return 0.0
        return self.total_latency_ms / self.invocation_count

    @property
    def is_verified(self) -> bool:
        """Check if skill has successfully passed trajectory verification."""
        return self.lifecycle_state in (SkillLifecycleState.VERIFIED, SkillLifecycleState.ACTIVE)

    def record_invocation(self, latency_ms: float, is_error: bool = False) -> None:
        """Record telemetry metrics from an execution."""
        self.invocation_count += 1
        if is_error:
            self.error_count += 1
        self.total_latency_ms += max(0.0, float(latency_ms))
        self.updated_at = time.time()

    def transition_to(self, new_state: SkillLifecycleState | str) -> None:
        """Transition skill to a new lifecycle state."""
        state = SkillLifecycleState(new_state) if isinstance(new_state, str) else new_state
        self.lifecycle_state = state
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "source_code": self.source_code,
            "ast_hash": self.ast_hash,
            "lifecycle_state": self.lifecycle_state.value,
            "entrypoint_function": self.entrypoint_function,
            "required_capabilities": list(self.required_capabilities),
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "test_vectors": [tv.to_dict() for tv in self.test_vectors],
            "author_role_id": self.author_role_id,
            "originating_goal_id": self.originating_goal_id,
            "artifact_id": self.artifact_id,
            "verification_report": self.verification_report.to_dict() if self.verification_report else None,
            "version": self.version,
            "invocation_count": self.invocation_count,
            "error_count": self.error_count,
            "total_latency_ms": self.total_latency_ms,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SynthesizedSkill:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")

        vectors = [TestVector.from_dict(tv) for tv in data.get("test_vectors", []) if isinstance(tv, dict)]
        vr_data = data.get("verification_report")
        vr = SkillVerificationReport.from_dict(vr_data) if isinstance(vr_data, dict) else None

        return cls(
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            source_code=str(data.get("source_code", "")),
            ast_hash=str(data.get("ast_hash", "")),
            lifecycle_state=SkillLifecycleState(data.get("lifecycle_state", SkillLifecycleState.DRAFT.value)),
            entrypoint_function=str(data.get("entrypoint_function", "execute")),
            required_capabilities=tuple(data.get("required_capabilities", ())),
            input_schema=dict(data.get("input_schema", {})),
            output_schema=dict(data.get("output_schema", {})),
            test_vectors=tuple(vectors),
            author_role_id=str(data.get("author_role_id", "coder")),
            originating_goal_id=data.get("originating_goal_id"),
            artifact_id=data.get("artifact_id"),
            verification_report=vr,
            version=int(data.get("version", 1)),
            invocation_count=int(data.get("invocation_count", 0)),
            error_count=int(data.get("error_count", 0)),
            total_latency_ms=float(data.get("total_latency_ms", 0.0)),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            metadata=dict(data.get("metadata", {})),
        )


class DynamicTool(ToolInterface):
    """Bridge adapter exposing a SynthesizedSkill as an executable ToolInterface instance.
    Execution is routed through the sandboxed executor."""

    def __init__(
        self,
        skill: SynthesizedSkill,
        executor_func: Callable[[str, str], str] | None = None,
    ):
        if not isinstance(skill, SynthesizedSkill):
            raise TypeError("skill must be an instance of SynthesizedSkill.")
        self.skill = skill
        self._executor_func = executor_func

    @property
    def name(self) -> str:
        return self.skill.name

    @property
    def description(self) -> str:
        return self.skill.description or f"Dynamically synthesized tool: {self.skill.name}"

    @property
    def capabilities(self) -> tuple[str, ...]:
        return self.skill.required_capabilities

    def execute(self, input_data: str) -> str:
        """Execute the dynamic tool with the supplied input string."""
        if self._executor_func is not None:
            return self._executor_func(self.skill.name, input_data)
        raise RuntimeError(f"Dynamic tool '{self.name}' has no registered sandboxed executor function.")


@dataclass(frozen=True)
class SkillStep:
    """Represents a single step in a declarative CompositeSkill pipeline."""

    step_id: str
    skill_or_tool_name: str
    input_template: str = "{input}"
    output_key: str = "result"
    condition: str | None = None
    timeout_seconds: float = 5.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.step_id, str) or not self.step_id.strip():
            raise ValueError("step_id must be a non-empty string.")
        object.__setattr__(self, "step_id", self.step_id.strip())

        if not isinstance(self.skill_or_tool_name, str) or not self.skill_or_tool_name.strip():
            raise ValueError("skill_or_tool_name must be a non-empty string.")
        object.__setattr__(self, "skill_or_tool_name", self.skill_or_tool_name.strip().lower())

        if not isinstance(self.input_template, str):
            raise TypeError("input_template must be a string.")

        if not isinstance(self.output_key, str) or not self.output_key.strip():
            object.__setattr__(self, "output_key", "result")
        else:
            object.__setattr__(self, "output_key", self.output_key.strip())

        if self.condition is not None and not isinstance(self.condition, str):
            raise TypeError("condition must be a string or None.")

        if not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive number.")
        object.__setattr__(self, "timeout_seconds", float(self.timeout_seconds))

        object.__setattr__(self, "metadata", sanitize_skill_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "skill_or_tool_name": self.skill_or_tool_name,
            "input_template": self.input_template,
            "output_key": self.output_key,
            "condition": self.condition,
            "timeout_seconds": self.timeout_seconds,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillStep:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        return cls(
            step_id=str(data.get("step_id", "")),
            skill_or_tool_name=str(data.get("skill_or_tool_name", "")),
            input_template=str(data.get("input_template", "{input}")),
            output_key=str(data.get("output_key", "result")),
            condition=data.get("condition"),
            timeout_seconds=float(data.get("timeout_seconds", 5.0)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class CompositeSkill:
    """Declarative workflow pipeline composing existing tools and skills in sequence."""

    name: str
    description: str
    steps: tuple[SkillStep, ...]
    lifecycle_state: SkillLifecycleState = SkillLifecycleState.DRAFT
    author_role_id: str = "architect"
    originating_goal_id: str | None = None
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Composite skill name must be a non-empty string.")
        self.name = self.name.strip().lower()

        if not isinstance(self.description, str):
            raise TypeError("description must be a string.")
        self.description = self.description.strip()

        if isinstance(self.steps, (list, tuple)):
            step_list: list[SkillStep] = []
            for s in self.steps:
                if isinstance(s, SkillStep):
                    step_list.append(s)
                elif isinstance(s, dict):
                    step_list.append(SkillStep.from_dict(s))
                else:
                    raise TypeError("steps elements must be SkillStep instances or dicts.")
            if not step_list:
                raise ValueError("CompositeSkill must contain at least one step.")
            self.steps = tuple(step_list)
        else:
            raise TypeError("steps must be a sequence of SkillStep instances.")

        if isinstance(self.lifecycle_state, str):
            self.lifecycle_state = SkillLifecycleState(self.lifecycle_state)
        elif not isinstance(self.lifecycle_state, SkillLifecycleState):
            raise TypeError("lifecycle_state must be an instance of SkillLifecycleState.")

        if not isinstance(self.author_role_id, str) or not self.author_role_id.strip():
            self.author_role_id = "architect"
        else:
            self.author_role_id = self.author_role_id.strip().lower()

        if self.originating_goal_id is not None and not isinstance(self.originating_goal_id, str):
            raise TypeError("originating_goal_id must be a string or None.")

        if not isinstance(self.input_schema, dict):
            raise TypeError("input_schema must be a dictionary.")
        if not isinstance(self.output_schema, dict):
            raise TypeError("output_schema must be a dictionary.")

        if not isinstance(self.version, int) or self.version < 1:
            raise ValueError("version must be a positive integer.")

        if not isinstance(self.created_at, (int, float)):
            raise TypeError("created_at must be numeric.")
        if not isinstance(self.updated_at, (int, float)):
            raise TypeError("updated_at must be numeric.")

        self.metadata = sanitize_skill_metadata(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "steps": [s.to_dict() for s in self.steps],
            "lifecycle_state": self.lifecycle_state.value,
            "author_role_id": self.author_role_id,
            "originating_goal_id": self.originating_goal_id,
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompositeSkill:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")

        steps = [SkillStep.from_dict(s) for s in data.get("steps", []) if isinstance(s, dict)]
        return cls(
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            steps=tuple(steps),
            lifecycle_state=SkillLifecycleState(data.get("lifecycle_state", SkillLifecycleState.DRAFT.value)),
            author_role_id=str(data.get("author_role_id", "architect")),
            originating_goal_id=data.get("originating_goal_id"),
            input_schema=dict(data.get("input_schema", {})),
            output_schema=dict(data.get("output_schema", {})),
            version=int(data.get("version", 1)),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            metadata=dict(data.get("metadata", {})),
        )
