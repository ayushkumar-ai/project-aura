"""Memory Lifecycle, Temporal Decay, and Heuristic Calibration Data Models (M15).

Defines structured contracts for mathematical temporal decay, utility scoring,
memory compaction, lifecycle policies, and empirical heuristic efficacy calibration.
Enforces non-authorizing metadata isolation and preserves provenance grounding.
"""

from __future__ import annotations

import copy
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
        raise ValueError("Cannot serialize callable value in lifecycle data.")
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


class DecayModel(str, Enum):
    """Mathematical decay functions for confidence attenuation over time."""

    EXPONENTIAL = "exponential"
    LINEAR = "linear"
    STEP = "step"
    NONE = "none"


class LifecycleAction(str, Enum):
    """Action to take on a memory entry based on utility evaluation."""

    RETAIN = "retain"
    PROMOTE = "promote"
    COMPACT = "compact"
    EVICT = "evict"
    ARCHIVE = "archive"


class RuleStatus(str, Enum):
    """Empirical lifecycle status of a distilled heuristic rule."""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    PROMOTED = "promoted"
    DEPRECATED = "deprecated"


@dataclass(frozen=True)
class DecayConfig:
    """Configuration for temporal confidence decay across memory namespaces."""

    enabled: bool = True
    decay_model: DecayModel = DecayModel.EXPONENTIAL
    default_half_life_days: float = 30.0
    namespace_half_lives: dict[str, float] = field(default_factory=lambda: {
        "user_profile": 365.0,
        "user_preferences": 180.0,
        "domain_knowledge": 30.0,
        "system_facts": 14.0,
        "task_scratchpad": 1.0,
        "execution_history": 60.0,
    })
    min_confidence_floor: float = 0.05

    def get_half_life_days(self, namespace: str) -> float:
        """Get the configured half-life in days for a specific namespace."""
        clean_ns = str(namespace).strip().lower()
        if clean_ns.startswith("task:"):
            return self.namespace_half_lives.get("task_scratchpad", 1.0)
        return self.namespace_half_lives.get(clean_ns, self.default_half_life_days)


@dataclass(frozen=True)
class MemoryUtilityScore:
    """Diagnostic utility evaluation for a single memory entry."""

    entry_id: str
    key: str
    namespace: str
    tier: str
    base_confidence: float
    decayed_confidence: float
    access_count: int = 0
    recency_score: float = 1.0
    utility_score: float = 1.0
    recommended_action: LifecycleAction = LifecycleAction.RETAIN
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.entry_id, str) or not self.entry_id.strip():
            raise ValueError("entry_id must be a non-empty string.")
        object.__setattr__(self, "entry_id", self.entry_id.strip())

        if not isinstance(self.key, str) or not self.key.strip():
            raise ValueError("key must be a non-empty string.")
        object.__setattr__(self, "key", self.key.strip())

        if not isinstance(self.namespace, str) or not self.namespace.strip():
            raise ValueError("namespace must be a non-empty string.")
        object.__setattr__(self, "namespace", self.namespace.strip())

        if not isinstance(self.tier, str) or not self.tier.strip():
            raise ValueError("tier must be a non-empty string.")
        object.__setattr__(self, "tier", self.tier.strip())

        object.__setattr__(self, "base_confidence", max(0.0, min(1.0, float(self.base_confidence))))
        object.__setattr__(self, "decayed_confidence", max(0.0, min(1.0, float(self.decayed_confidence))))
        object.__setattr__(self, "access_count", max(0, int(self.access_count)))
        object.__setattr__(self, "recency_score", max(0.0, min(1.0, float(self.recency_score))))
        object.__setattr__(self, "utility_score", max(0.0, float(self.utility_score)))

        if isinstance(self.recommended_action, str):
            object.__setattr__(self, "recommended_action", LifecycleAction(self.recommended_action))
        elif not isinstance(self.recommended_action, LifecycleAction):
            raise TypeError("recommended_action must be an instance of LifecycleAction.")

        object.__setattr__(self, "metadata", strip_forbidden_metadata_keys(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "key": self.key,
            "namespace": self.namespace,
            "tier": self.tier,
            "base_confidence": self.base_confidence,
            "decayed_confidence": self.decayed_confidence,
            "access_count": self.access_count,
            "recency_score": self.recency_score,
            "utility_score": self.utility_score,
            "recommended_action": self.recommended_action.value,
            "metadata": _canonical_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryUtilityScore:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        return cls(
            entry_id=str(data.get("entry_id", "")),
            key=str(data.get("key", "")),
            namespace=str(data.get("namespace", "general")),
            tier=str(data.get("tier", "semantic")),
            base_confidence=float(data.get("base_confidence", 1.0)),
            decayed_confidence=float(data.get("decayed_confidence", 1.0)),
            access_count=int(data.get("access_count", 0)),
            recency_score=float(data.get("recency_score", 1.0)),
            utility_score=float(data.get("utility_score", 1.0)),
            recommended_action=LifecycleAction(data.get("recommended_action", LifecycleAction.RETAIN.value)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class RuleEfficacyRecord:
    """Empirical calibration metrics and validation state for a distilled heuristic rule."""

    rule_id: str
    trigger_condition: str
    status: RuleStatus = RuleStatus.CANDIDATE
    trigger_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    replan_count: int = 0
    base_confidence: float = 0.85
    calibrated_confidence: float = 0.85
    efficacy_score: float = 1.0
    last_evaluated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.rule_id, str) or not self.rule_id.strip():
            raise ValueError("rule_id must be a non-empty string.")
        object.__setattr__(self, "rule_id", self.rule_id.strip())

        if not isinstance(self.trigger_condition, str) or not self.trigger_condition.strip():
            raise ValueError("trigger_condition must be a non-empty string.")
        object.__setattr__(self, "trigger_condition", self.trigger_condition.strip())

        if isinstance(self.status, str):
            object.__setattr__(self, "status", RuleStatus(self.status))
        elif not isinstance(self.status, RuleStatus):
            raise TypeError("status must be an instance of RuleStatus.")

        object.__setattr__(self, "trigger_count", max(0, int(self.trigger_count)))
        object.__setattr__(self, "success_count", max(0, int(self.success_count)))
        object.__setattr__(self, "failure_count", max(0, int(self.failure_count)))
        object.__setattr__(self, "replan_count", max(0, int(self.replan_count)))
        object.__setattr__(self, "base_confidence", max(0.0, min(1.0, float(self.base_confidence))))
        object.__setattr__(self, "calibrated_confidence", max(0.0, min(1.0, float(self.calibrated_confidence))))
        object.__setattr__(self, "efficacy_score", max(0.0, min(1.0, float(self.efficacy_score))))
        object.__setattr__(self, "last_evaluated_at", float(self.last_evaluated_at))
        object.__setattr__(self, "metadata", strip_forbidden_metadata_keys(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "trigger_condition": self.trigger_condition,
            "status": self.status.value,
            "trigger_count": self.trigger_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "replan_count": self.replan_count,
            "base_confidence": self.base_confidence,
            "calibrated_confidence": self.calibrated_confidence,
            "efficacy_score": self.efficacy_score,
            "last_evaluated_at": self.last_evaluated_at,
            "metadata": _canonical_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuleEfficacyRecord:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        return cls(
            rule_id=str(data.get("rule_id", "")),
            trigger_condition=str(data.get("trigger_condition", "")),
            status=RuleStatus(data.get("status", RuleStatus.CANDIDATE.value)),
            trigger_count=int(data.get("trigger_count", 0)),
            success_count=int(data.get("success_count", 0)),
            failure_count=int(data.get("failure_count", 0)),
            replan_count=int(data.get("replan_count", 0)),
            base_confidence=float(data.get("base_confidence", 0.85)),
            calibrated_confidence=float(data.get("calibrated_confidence", 0.85)),
            efficacy_score=float(data.get("efficacy_score", 1.0)),
            last_evaluated_at=float(data.get("last_evaluated_at", time.time())),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class CompactionRecord:
    """Audited result of a memory compaction and pruning pass."""

    compaction_id: str
    namespace: str
    facts_analyzed: int
    facts_merged: int
    facts_evicted: int
    memory_reclaimed_entries: int
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.compaction_id, str) or not self.compaction_id.strip():
            raise ValueError("compaction_id must be a non-empty string.")
        object.__setattr__(self, "compaction_id", self.compaction_id.strip())

        if not isinstance(self.namespace, str) or not self.namespace.strip():
            raise ValueError("namespace must be a non-empty string.")
        object.__setattr__(self, "namespace", self.namespace.strip())

        object.__setattr__(self, "facts_analyzed", max(0, int(self.facts_analyzed)))
        object.__setattr__(self, "facts_merged", max(0, int(self.facts_merged)))
        object.__setattr__(self, "facts_evicted", max(0, int(self.facts_evicted)))
        object.__setattr__(self, "memory_reclaimed_entries", max(0, int(self.memory_reclaimed_entries)))
        object.__setattr__(self, "timestamp", float(self.timestamp))
        object.__setattr__(self, "metadata", strip_forbidden_metadata_keys(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "compaction_id": self.compaction_id,
            "namespace": self.namespace,
            "facts_analyzed": self.facts_analyzed,
            "facts_merged": self.facts_merged,
            "facts_evicted": self.facts_evicted,
            "memory_reclaimed_entries": self.memory_reclaimed_entries,
            "timestamp": self.timestamp,
            "metadata": _canonical_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompactionRecord:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        return cls(
            compaction_id=str(data.get("compaction_id", "")),
            namespace=str(data.get("namespace", "general")),
            facts_analyzed=int(data.get("facts_analyzed", 0)),
            facts_merged=int(data.get("facts_merged", 0)),
            facts_evicted=int(data.get("facts_evicted", 0)),
            memory_reclaimed_entries=int(data.get("memory_reclaimed_entries", 0)),
            timestamp=float(data.get("timestamp", time.time())),
            metadata=dict(data.get("metadata", {})),
        )
