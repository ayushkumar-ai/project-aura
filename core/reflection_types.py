"""Agentic Self-Reflection and Memory Consolidation Data Models and Schemas (M14).

Defines structured data structures for diagnostic execution reflection,
failure diagnosis, rule distillation, contradiction resolution, and knowledge consolidation.
Strictly strips authorization / permission bypass metadata and preserves provenance grounding.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.provenance import TaintedValue, is_tainted, unwrap_tainted, wrap_tainted

FORBIDDEN_METADATA_KEYS = frozenset({
    "approved",
    "approval_status",
    "is_approved",
    "auto_approve",
    "permission",
    "authorized",
    "bypass_policy",
    "role_override",
})


def strip_forbidden_metadata_keys(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Recursively strip any authorization, permission, or approval keys from metadata."""
    if meta is None:
        return {}
    if not isinstance(meta, dict):
        return {}

    cleaned: dict[str, Any] = {}
    for k, v in meta.items():
        k_str = str(k)
        if k_str.lower() in FORBIDDEN_METADATA_KEYS:
            continue
        if callable(v):
            continue
        if isinstance(v, dict):
            cleaned[k_str] = strip_forbidden_metadata_keys(v)
        elif isinstance(v, (list, tuple)):
            cleaned[k_str] = [
                strip_forbidden_metadata_keys(item) if isinstance(item, dict) else (str(item) if not callable(item) else "")
                for item in v
            ]
        elif isinstance(v, (str, int, float, bool)) or v is None or isinstance(v, TaintedValue):
            cleaned[k_str] = v
        else:
            cleaned[k_str] = repr(v)
    return cleaned


def sanitize_reflection_metadata(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Alias for strip_forbidden_metadata_keys."""
    return strip_forbidden_metadata_keys(meta)


def _canonical_value(val: Any) -> Any:
    """Convert values into deterministic JSON-serializable primitives, preserving TaintedValue."""
    if isinstance(val, TaintedValue):
        return {
            "__tainted__": True,
            "raw_value": _canonical_value(val.raw_value),
            "is_untrusted": val.is_untrusted,
            "source_type": val.source_type,
            "originating_step_id": val.originating_step_id,
            "source_urls": list(val.source_urls),
            "metadata": {str(k): _canonical_value(v) for k, v in sorted(val.metadata.items())},
        }
    elif isinstance(val, (str, int, float, bool)) or val is None:
        return val
    elif isinstance(val, (list, tuple, set, frozenset)):
        return [_canonical_value(x) for x in val]
    elif isinstance(val, dict):
        return {str(k): _canonical_value(v) for k, v in sorted(val.items())}
    elif isinstance(val, Enum):
        return val.value
    elif callable(val):
        raise ValueError("Cannot serialize callable value in reflection data.")
    else:
        return repr(val)


def _restore_value(val: Any) -> Any:
    """Restore values from serialized JSON primitives, restoring TaintedValue instances."""
    if isinstance(val, dict):
        if val.get("__tainted__") is True and "raw_value" in val:
            return wrap_tainted(
                value=_restore_value(val.get("raw_value")),
                is_untrusted=bool(val.get("is_untrusted", True)),
                source_type=str(val.get("source_type", "external_web")),
                originating_step_id=val.get("originating_step_id"),
                source_urls=val.get("source_urls", ()),
                metadata=dict(val.get("metadata", {})),
            )
        return {k: _restore_value(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [_restore_value(x) for x in val]
    return val


class FailureIssueType(str, Enum):
    """Categorization of failures during execution."""

    NONE = "none"
    TOOL_PAYLOAD_ERROR = "tool_payload_error"
    TOOL_EXECUTION_FAILURE = "tool_execution_failure"
    TIMEOUT = "timeout"
    POLICY_DENIAL = "policy_denial"
    DEPENDENCY_FAILURE = "dependency_failure"
    RESOURCE_LIMIT_EXCEEDED = "resource_limit_exceeded"
    UNKNOWN = "unknown"


class CritiqueSeverity(str, Enum):
    """Severity level for step critiques and execution reflections."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ConsolidationSourceType(str, Enum):
    """Source of experiences or knowledge consolidated into semantic memory."""

    EPISODIC_RUNS = "episodic_runs"
    RESEARCH_REPORT = "research_report"
    DIRECT_OBSERVATION = "direct_observation"
    MANUAL = "manual"


class ResolutionStrategy(str, Enum):
    """Strategy used to resolve belief contradictions."""

    REPLACE_NEWER_CONFIDENT = "replace_newer_confident"
    PRESERVE_EXISTING_CONFIDENT = "preserve_existing_confident"
    MERGE_ATTRIBUTES = "merge_attributes"
    MANUAL_REVIEW = "manual_review"


@dataclass(frozen=True)
class StepCritique:
    """Diagnostic critique for a single executed plan step."""

    step_id: str
    skill_name: str
    success: bool
    efficiency_score: float = 1.0
    issue_type: str | None = None
    severity: CritiqueSeverity = CritiqueSeverity.INFO
    diagnosis: str = ""
    remedy_suggestion: str | None = None
    is_untrusted: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.step_id, str) or not self.step_id.strip():
            raise ValueError("step_id must be a non-empty string.")
        object.__setattr__(self, "step_id", self.step_id.strip())

        if not isinstance(self.skill_name, str) or not self.skill_name.strip():
            raise ValueError("skill_name must be a non-empty string.")
        object.__setattr__(self, "skill_name", self.skill_name.strip())

        if not isinstance(self.success, bool):
            raise TypeError("success must be a boolean.")

        if not isinstance(self.efficiency_score, (int, float)):
            raise TypeError("efficiency_score must be a float between 0.0 and 1.0.")
        eff = max(0.0, min(1.0, float(self.efficiency_score)))
        object.__setattr__(self, "efficiency_score", eff)

        if self.issue_type is not None and not isinstance(self.issue_type, str):
            raise TypeError("issue_type must be a string or None.")

        if isinstance(self.severity, str):
            object.__setattr__(self, "severity", CritiqueSeverity(self.severity))
        elif not isinstance(self.severity, CritiqueSeverity):
            raise TypeError("severity must be an instance of CritiqueSeverity.")

        if not isinstance(self.diagnosis, str):
            raise TypeError("diagnosis must be a string.")

        if self.remedy_suggestion is not None and not isinstance(self.remedy_suggestion, str):
            raise TypeError("remedy_suggestion must be a string or None.")

        if not isinstance(self.is_untrusted, bool):
            raise TypeError("is_untrusted must be a boolean.")

        object.__setattr__(self, "metadata", strip_forbidden_metadata_keys(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "skill_name": self.skill_name,
            "success": self.success,
            "efficiency_score": self.efficiency_score,
            "issue_type": self.issue_type,
            "severity": self.severity.value,
            "diagnosis": self.diagnosis,
            "remedy_suggestion": self.remedy_suggestion,
            "is_untrusted": self.is_untrusted,
            "metadata": _canonical_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StepCritique:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        return cls(
            step_id=str(data.get("step_id", "")),
            skill_name=str(data.get("skill_name", "")),
            success=bool(data.get("success", True)),
            efficiency_score=float(data.get("efficiency_score", 1.0)),
            issue_type=data.get("issue_type"),
            severity=CritiqueSeverity(data.get("severity", CritiqueSeverity.INFO.value)),
            diagnosis=str(data.get("diagnosis", "")),
            remedy_suggestion=data.get("remedy_suggestion"),
            is_untrusted=bool(data.get("is_untrusted", False)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class ReflectionRule:
    """Actionable heuristic rule distilled from successful or failed executions."""

    rule_id: str
    trigger_condition: str
    guidance: str | TaintedValue
    confidence: float = 1.0
    source_trace_id: str | None = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.rule_id, str) or not self.rule_id.strip():
            raise ValueError("rule_id must be a non-empty string.")
        object.__setattr__(self, "rule_id", self.rule_id.strip())

        if not isinstance(self.trigger_condition, str) or not self.trigger_condition.strip():
            raise ValueError("trigger_condition must be a non-empty string.")
        object.__setattr__(self, "trigger_condition", self.trigger_condition.strip())

        if not isinstance(self.guidance, (str, TaintedValue)):
            raise TypeError("guidance must be a string or TaintedValue.")

        if not isinstance(self.confidence, (int, float)):
            raise TypeError("confidence must be a float between 0.0 and 1.0.")
        conf = max(0.0, min(1.0, float(self.confidence)))
        object.__setattr__(self, "confidence", conf)

        if self.source_trace_id is not None and not isinstance(self.source_trace_id, str):
            raise TypeError("source_trace_id must be a string or None.")

        if not isinstance(self.tags, (list, tuple)):
            raise TypeError("tags must be a list or tuple of strings.")
        object.__setattr__(self, "tags", [str(t) for t in self.tags])

        object.__setattr__(self, "metadata", strip_forbidden_metadata_keys(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "trigger_condition": self.trigger_condition,
            "guidance": _canonical_value(self.guidance),
            "confidence": self.confidence,
            "source_trace_id": self.source_trace_id,
            "tags": list(self.tags),
            "metadata": _canonical_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReflectionRule:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        guidance_raw = data.get("guidance", "")
        guidance = _restore_value(guidance_raw)
        return cls(
            rule_id=str(data.get("rule_id", "")),
            trigger_condition=str(data.get("trigger_condition", "")),
            guidance=guidance,
            confidence=float(data.get("confidence", 1.0)),
            source_trace_id=data.get("source_trace_id"),
            tags=list(data.get("tags", [])),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class ReflectionAssessment:
    """Overall diagnostic assessment for an executed plan or task."""

    success: bool
    efficiency_score: float = 1.0
    steps_executed: int = 0
    steps_failed: int = 0
    retried_steps_count: int = 0
    replan_count: int = 0
    failure_issue_type: FailureIssueType = FailureIssueType.NONE
    root_cause: str | None = None
    was_retry_useful: bool = False
    was_replan_useful: bool = False
    lessons_learned: list[str] = field(default_factory=list)
    rules_distilled: list[ReflectionRule] = field(default_factory=list)
    step_critiques: list[StepCritique] = field(default_factory=list)
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.success, bool):
            raise TypeError("success must be a boolean.")

        if not isinstance(self.efficiency_score, (int, float)):
            raise TypeError("efficiency_score must be a float between 0.0 and 1.0.")
        eff = max(0.0, min(1.0, float(self.efficiency_score)))
        object.__setattr__(self, "efficiency_score", eff)

        if not isinstance(self.steps_executed, int) or self.steps_executed < 0:
            raise ValueError("steps_executed must be a non-negative integer.")

        if not isinstance(self.steps_failed, int) or self.steps_failed < 0:
            raise ValueError("steps_failed must be a non-negative integer.")

        if not isinstance(self.retried_steps_count, int) or self.retried_steps_count < 0:
            raise ValueError("retried_steps_count must be a non-negative integer.")

        if not isinstance(self.replan_count, int) or self.replan_count < 0:
            raise ValueError("replan_count must be a non-negative integer.")

        if isinstance(self.failure_issue_type, str):
            object.__setattr__(self, "failure_issue_type", FailureIssueType(self.failure_issue_type))
        elif not isinstance(self.failure_issue_type, FailureIssueType):
            raise TypeError("failure_issue_type must be a FailureIssueType instance.")

        if self.root_cause is not None and not isinstance(self.root_cause, str):
            raise TypeError("root_cause must be a string or None.")

        if not isinstance(self.was_retry_useful, bool):
            raise TypeError("was_retry_useful must be a boolean.")

        if not isinstance(self.was_replan_useful, bool):
            raise TypeError("was_replan_useful must be a boolean.")

        if not isinstance(self.lessons_learned, (list, tuple)):
            raise TypeError("lessons_learned must be a list or tuple of strings.")
        object.__setattr__(self, "lessons_learned", [str(l) for l in self.lessons_learned])

        if not isinstance(self.rules_distilled, (list, tuple)):
            raise TypeError("rules_distilled must be a list or tuple of ReflectionRule instances.")
        for r in self.rules_distilled:
            if not isinstance(r, ReflectionRule):
                raise TypeError("rules_distilled items must be ReflectionRule instances.")
        object.__setattr__(self, "rules_distilled", list(self.rules_distilled))

        if not isinstance(self.step_critiques, (list, tuple)):
            raise TypeError("step_critiques must be a list or tuple of StepCritique instances.")
        for sc in self.step_critiques:
            if not isinstance(sc, StepCritique):
                raise TypeError("step_critiques items must be StepCritique instances.")
        object.__setattr__(self, "step_critiques", list(self.step_critiques))

        if not isinstance(self.summary, str):
            raise TypeError("summary must be a string.")

        object.__setattr__(self, "metadata", strip_forbidden_metadata_keys(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "efficiency_score": self.efficiency_score,
            "steps_executed": self.steps_executed,
            "steps_failed": self.steps_failed,
            "retried_steps_count": self.retried_steps_count,
            "replan_count": self.replan_count,
            "failure_issue_type": self.failure_issue_type.value,
            "root_cause": self.root_cause,
            "was_retry_useful": self.was_retry_useful,
            "was_replan_useful": self.was_replan_useful,
            "lessons_learned": list(self.lessons_learned),
            "rules_distilled": [r.to_dict() for r in self.rules_distilled],
            "step_critiques": [sc.to_dict() for sc in self.step_critiques],
            "summary": self.summary,
            "metadata": _canonical_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReflectionAssessment:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        rules = [ReflectionRule.from_dict(r) for r in data.get("rules_distilled", []) if isinstance(r, dict)]
        critiques = [StepCritique.from_dict(sc) for sc in data.get("step_critiques", []) if isinstance(sc, dict)]
        return cls(
            success=bool(data.get("success", True)),
            efficiency_score=float(data.get("efficiency_score", 1.0)),
            steps_executed=int(data.get("steps_executed", 0)),
            steps_failed=int(data.get("steps_failed", 0)),
            retried_steps_count=int(data.get("retried_steps_count", 0)),
            replan_count=int(data.get("replan_count", 0)),
            failure_issue_type=FailureIssueType(data.get("failure_issue_type", FailureIssueType.NONE.value)),
            root_cause=data.get("root_cause"),
            was_retry_useful=bool(data.get("was_retry_useful", False)),
            was_replan_useful=bool(data.get("was_replan_useful", False)),
            lessons_learned=list(data.get("lessons_learned", [])),
            rules_distilled=rules,
            step_critiques=critiques,
            summary=str(data.get("summary", "")),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class ReflectionRecord:
    """Historical record of an execution reflection pass."""

    reflection_id: str
    target_id: str
    target_type: str
    assessment: ReflectionAssessment
    trace_id: str | None = None
    plan_id: str | None = None
    goal_id: str | None = None
    heuristics_distilled: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.reflection_id, str) or not self.reflection_id.strip():
            raise ValueError("reflection_id must be a non-empty string.")
        object.__setattr__(self, "reflection_id", self.reflection_id.strip())

        if not isinstance(self.target_id, str) or not self.target_id.strip():
            raise ValueError("target_id must be a non-empty string.")
        object.__setattr__(self, "target_id", self.target_id.strip())

        if not isinstance(self.target_type, str) or not self.target_type.strip():
            raise ValueError("target_type must be a non-empty string.")
        object.__setattr__(self, "target_type", self.target_type.strip())

        if not isinstance(self.assessment, ReflectionAssessment):
            raise TypeError("assessment must be a ReflectionAssessment instance.")

        if not isinstance(self.heuristics_distilled, (list, tuple)):
            raise TypeError("heuristics_distilled must be a list or tuple of strings.")
        object.__setattr__(self, "heuristics_distilled", [str(h) for h in self.heuristics_distilled])

        if not isinstance(self.timestamp, (int, float)):
            raise TypeError("timestamp must be numeric.")

        object.__setattr__(self, "metadata", strip_forbidden_metadata_keys(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "reflection_id": self.reflection_id,
            "target_id": self.target_id,
            "target_type": self.target_type,
            "assessment": self.assessment.to_dict(),
            "trace_id": self.trace_id,
            "plan_id": self.plan_id,
            "goal_id": self.goal_id,
            "heuristics_distilled": list(self.heuristics_distilled),
            "timestamp": self.timestamp,
            "metadata": _canonical_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReflectionRecord:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        assessment_data = data.get("assessment", {})
        assessment = ReflectionAssessment.from_dict(assessment_data) if isinstance(assessment_data, dict) else ReflectionAssessment(success=True)
        return cls(
            reflection_id=str(data.get("reflection_id", "")),
            target_id=str(data.get("target_id", "")),
            target_type=str(data.get("target_type", "plan")),
            assessment=assessment,
            trace_id=data.get("trace_id"),
            plan_id=data.get("plan_id"),
            goal_id=data.get("goal_id"),
            heuristics_distilled=list(data.get("heuristics_distilled", [])),
            timestamp=float(data.get("timestamp", time.time())),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class ContradictionRecord:
    """Record of a contradiction detected and resolved between existing memory and incoming facts."""

    subject: str
    existing_value: Any
    existing_confidence: float
    new_value: Any
    new_confidence: float
    resolution: ResolutionStrategy
    chosen_value: Any
    rationale: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.subject, str) or not self.subject.strip():
            raise ValueError("subject must be a non-empty string.")
        object.__setattr__(self, "subject", self.subject.strip())

        if not isinstance(self.existing_confidence, (int, float)):
            raise TypeError("existing_confidence must be numeric.")
        if not isinstance(self.new_confidence, (int, float)):
            raise TypeError("new_confidence must be numeric.")

        if isinstance(self.resolution, str):
            object.__setattr__(self, "resolution", ResolutionStrategy(self.resolution))
        elif not isinstance(self.resolution, ResolutionStrategy):
            raise TypeError("resolution must be a ResolutionStrategy instance.")

        if not isinstance(self.rationale, str):
            raise TypeError("rationale must be a string.")

        if not isinstance(self.timestamp, (int, float)):
            raise TypeError("timestamp must be numeric.")

        object.__setattr__(self, "metadata", strip_forbidden_metadata_keys(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "existing_value": _canonical_value(self.existing_value),
            "existing_confidence": self.existing_confidence,
            "new_value": _canonical_value(self.new_value),
            "new_confidence": self.new_confidence,
            "resolution": self.resolution.value,
            "chosen_value": _canonical_value(self.chosen_value),
            "rationale": self.rationale,
            "timestamp": self.timestamp,
            "metadata": _canonical_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContradictionRecord:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        return cls(
            subject=str(data.get("subject", "")),
            existing_value=_restore_value(data.get("existing_value")),
            existing_confidence=float(data.get("existing_confidence", 1.0)),
            new_value=_restore_value(data.get("new_value")),
            new_confidence=float(data.get("new_confidence", 1.0)),
            resolution=ResolutionStrategy(data.get("resolution", ResolutionStrategy.REPLACE_NEWER_CONFIDENT.value)),
            chosen_value=_restore_value(data.get("chosen_value")),
            rationale=str(data.get("rationale", "")),
            timestamp=float(data.get("timestamp", time.time())),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class ConsolidationRecord:
    """Record of a memory consolidation event."""

    consolidation_id: str
    source_type: ConsolidationSourceType
    episodes_analyzed: int
    facts_created: int
    facts_updated: int
    contradictions_resolved: list[ContradictionRecord] = field(default_factory=list)
    heuristics_extracted: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.consolidation_id, str) or not self.consolidation_id.strip():
            raise ValueError("consolidation_id must be a non-empty string.")
        object.__setattr__(self, "consolidation_id", self.consolidation_id.strip())

        if isinstance(self.source_type, str):
            object.__setattr__(self, "source_type", ConsolidationSourceType(self.source_type))
        elif not isinstance(self.source_type, ConsolidationSourceType):
            raise TypeError("source_type must be a ConsolidationSourceType instance.")

        if not isinstance(self.episodes_analyzed, int) or self.episodes_analyzed < 0:
            raise ValueError("episodes_analyzed must be a non-negative integer.")

        if not isinstance(self.facts_created, int) or self.facts_created < 0:
            raise ValueError("facts_created must be a non-negative integer.")

        if not isinstance(self.facts_updated, int) or self.facts_updated < 0:
            raise ValueError("facts_updated must be a non-negative integer.")

        if not isinstance(self.contradictions_resolved, (list, tuple)):
            raise TypeError("contradictions_resolved must be a list or tuple of ContradictionRecord instances.")
        for cr in self.contradictions_resolved:
            if not isinstance(cr, ContradictionRecord):
                raise TypeError("contradictions_resolved items must be ContradictionRecord instances.")
        object.__setattr__(self, "contradictions_resolved", list(self.contradictions_resolved))

        if not isinstance(self.heuristics_extracted, (list, tuple)):
            raise TypeError("heuristics_extracted must be a list or tuple of strings.")
        object.__setattr__(self, "heuristics_extracted", [str(h) for h in self.heuristics_extracted])

        if not isinstance(self.timestamp, (int, float)):
            raise TypeError("timestamp must be numeric.")

        object.__setattr__(self, "metadata", strip_forbidden_metadata_keys(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "consolidation_id": self.consolidation_id,
            "source_type": self.source_type.value,
            "episodes_analyzed": self.episodes_analyzed,
            "facts_created": self.facts_created,
            "facts_updated": self.facts_updated,
            "contradictions_resolved": [c.to_dict() for c in self.contradictions_resolved],
            "heuristics_extracted": list(self.heuristics_extracted),
            "timestamp": self.timestamp,
            "metadata": _canonical_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConsolidationRecord:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        contradictions = [
            ContradictionRecord.from_dict(c) for c in data.get("contradictions_resolved", []) if isinstance(c, dict)
        ]
        return cls(
            consolidation_id=str(data.get("consolidation_id", "")),
            source_type=ConsolidationSourceType(data.get("source_type", ConsolidationSourceType.EPISODIC_RUNS.value)),
            episodes_analyzed=int(data.get("episodes_analyzed", 0)),
            facts_created=int(data.get("facts_created", 0)),
            facts_updated=int(data.get("facts_updated", 0)),
            contradictions_resolved=contradictions,
            heuristics_extracted=list(data.get("heuristics_extracted", [])),
            timestamp=float(data.get("timestamp", time.time())),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class DistillationResult:
    """Result of distilling a single source (research report, episode set, etc.) into structured knowledge."""

    source_id: str
    source_type: ConsolidationSourceType
    facts_distilled: list[Any] = field(default_factory=list)
    heuristics: list[str] = field(default_factory=list)
    rules: list[ReflectionRule] = field(default_factory=list)
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.source_id, str) or not self.source_id.strip():
            raise ValueError("source_id must be a non-empty string.")
        object.__setattr__(self, "source_id", self.source_id.strip())

        if isinstance(self.source_type, str):
            object.__setattr__(self, "source_type", ConsolidationSourceType(self.source_type))
        elif not isinstance(self.source_type, ConsolidationSourceType):
            raise TypeError("source_type must be a ConsolidationSourceType instance.")

        if not isinstance(self.heuristics, (list, tuple)):
            raise TypeError("heuristics must be a list or tuple of strings.")
        object.__setattr__(self, "heuristics", [str(h) for h in self.heuristics])

        if not isinstance(self.rules, (list, tuple)):
            raise TypeError("rules must be a list or tuple of ReflectionRule instances.")
        for r in self.rules:
            if not isinstance(r, ReflectionRule):
                raise TypeError("rules items must be ReflectionRule instances.")
        object.__setattr__(self, "rules", list(self.rules))

        if not isinstance(self.summary, str):
            raise TypeError("summary must be a string.")

        object.__setattr__(self, "metadata", strip_forbidden_metadata_keys(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type.value,
            "facts_distilled": [_canonical_value(f) for f in self.facts_distilled],
            "heuristics": list(self.heuristics),
            "rules": [r.to_dict() for r in self.rules],
            "summary": self.summary,
            "metadata": _canonical_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DistillationResult:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        rules = [ReflectionRule.from_dict(r) for r in data.get("rules", []) if isinstance(r, dict)]
        facts = [_restore_value(f) for f in data.get("facts_distilled", [])]
        return cls(
            source_id=str(data.get("source_id", "")),
            source_type=ConsolidationSourceType(data.get("source_type", ConsolidationSourceType.RESEARCH_REPORT.value)),
            facts_distilled=facts,
            heuristics=list(data.get("heuristics", [])),
            rules=rules,
            summary=str(data.get("summary", "")),
            metadata=dict(data.get("metadata", {})),
        )
