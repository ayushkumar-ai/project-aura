"""M56 — Cognitive Memory Domain Types, Enums, Models & Data Contracts.

Defines the multi-category cognitive memory hierarchy, provenance classifications,
lifecycle state machine enums, contradiction tracking records, user cognitive profiles,
experience patterns, and continuous learning feedback event schemas.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class CognitiveMemoryType(str, Enum):
    """Authoritative cognitive memory classifications."""
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PREFERENCE = "preference"
    EXPERIENCE = "experience"
    USER_PROFILE = "user_profile"


class ProvenanceType(str, Enum):
    """Source authority and provenance categorization."""
    USER_EXPLICIT = "user_explicit"
    TOOL_OBSERVED = "tool_observed"
    SYSTEM_DERIVED = "system_derived"
    MODEL_INFERRED = "model_inferred"
    EXTERNAL_IMPORTED = "external_imported"


class LifecycleState(str, Enum):
    """Cognitive memory lifecycle state machine."""
    ACTIVE = "active"
    STALE = "stale"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    DELETED = "deleted"


class ContradictionStatus(str, Enum):
    """Status of detected semantic contradictions."""
    DETECTED = "detected"
    AUTO_RESOLVED = "auto_resolved"
    MANUAL_PENDING = "manual_pending"
    RESOLVED = "resolved"


class ResolutionStrategy(str, Enum):
    """Strategies for resolving conflicting cognitive memories."""
    PROVENANCE_PRECEDENCE = "provenance_precedence"
    USER_OVERRIDE = "user_override"
    RECENCY = "recency"
    CONFIDENCE_THRESHOLD = "confidence_threshold"
    MANUAL = "manual"


class FeedbackType(str, Enum):
    """User feedback classification for continuous learning."""
    POSITIVE = "positive"
    NEGATIVE = "negative"
    CORRECTION = "correction"
    OVERRIDE = "override"


# Strict authority ranking for conflict resolution
PROVENANCE_AUTHORITY: dict[ProvenanceType, int] = {
    ProvenanceType.USER_EXPLICIT: 5,
    ProvenanceType.TOOL_OBSERVED: 4,
    ProvenanceType.SYSTEM_DERIVED: 3,
    ProvenanceType.MODEL_INFERRED: 2,
    ProvenanceType.EXTERNAL_IMPORTED: 1,
}

# Regex patterns for sensitive credential/secret scrubbing
_SECRET_PATTERNS = [
    re.compile(r"(?i)(bearer\s+[a-zA-Z0-9_\-\.]{20,})"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*['\"]?[a-zA-Z0-9_\-]{16,}['\"]?)"),
    re.compile(r"(?i)(password\s*[:=]\s*['\"]?[^\s'\"]{6,}['\"]?)"),
    re.compile(r"(?i)(token\s*[:=]\s*['\"]?[a-zA-Z0-9_\-]{20,}['\"]?)"),
    re.compile(r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----[\s\S]*?-----END\s+(?:RSA\s+)?PRIVATE\s+KEY-----"),
]


def scrub_sensitive_content(text: str) -> str:
    """Scrub tokens, API keys, private keys, and passwords before memory persistence."""
    if not isinstance(text, str):
        return str(text)
    scrubbed = text
    for pattern in _SECRET_PATTERNS:
        scrubbed = pattern.sub("[REDACTED_SECRET]", scrubbed)
    return scrubbed


def _sanitize_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Recursively sanitize metadata dictionary to ensure no callables or permission overrides."""
    if not isinstance(meta, dict):
        return {}
    forbidden_keys = frozenset({
        "approved",
        "approval_status",
        "is_approved",
        "auto_approve",
        "permission",
        "authorized",
        "bypass_policy",
        "role_override",
        "system_override",
    })
    cleaned: dict[str, Any] = {}
    for k, v in meta.items():
        k_str = str(k)
        if k_str.lower() in forbidden_keys or callable(v):
            continue
        if isinstance(v, dict):
            cleaned[k_str] = _sanitize_meta(v)
        elif isinstance(v, (list, tuple)):
            cleaned[k_str] = [
                _sanitize_meta(x) if isinstance(x, dict) else (str(x) if not callable(x) else "")
                for x in v
            ]
        elif isinstance(v, (str, int, float, bool)) or v is None:
            cleaned[k_str] = v
        else:
            cleaned[k_str] = str(v)
    return cleaned


@dataclass
class CognitiveMemory:
    """Authoritative cognitive memory entry representing episodic, semantic, preference, or experience records."""
    memory_id: str = field(default_factory=lambda: str(uuid4()))
    tenant_id: str = "default"
    memory_type: CognitiveMemoryType = CognitiveMemoryType.SEMANTIC
    category: str = "general"
    key: str = ""
    content: str = ""
    structured_data: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    provenance_type: ProvenanceType = ProvenanceType.SYSTEM_DERIVED
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE
    version: int = 1
    supersedes_id: str | None = None
    taint_status: bool = False
    source_urls: tuple[str, ...] = field(default_factory=tuple)
    tags: tuple[str, ...] = field(default_factory=tuple)
    embedding: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    access_count: int = 0
    last_accessed_at: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float | None = None

    def __post_init__(self):
        if not isinstance(self.memory_id, str) or not self.memory_id.strip():
            raise ValueError("memory_id must be a non-empty string.")
        self.memory_id = self.memory_id.strip()

        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string.")
        self.tenant_id = self.tenant_id.strip()

        if isinstance(self.memory_type, str):
            self.memory_type = CognitiveMemoryType(self.memory_type)
        if isinstance(self.provenance_type, str):
            self.provenance_type = ProvenanceType(self.provenance_type)
        if isinstance(self.lifecycle_state, str):
            self.lifecycle_state = LifecycleState(self.lifecycle_state)

        # Enforce confidence range bounds [0.0, 1.0]
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

        # Scrub sensitive content
        self.content = scrub_sensitive_content(str(self.content or ""))
        self.key = str(self.key or "").strip()
        self.category = str(self.category or "general").strip()

        # Sanitize metadata
        self.metadata = _sanitize_meta(self.metadata)

        # Source URLs and tags normalization
        if isinstance(self.source_urls, (list, tuple, set)):
            self.source_urls = tuple(str(u).strip() for u in self.source_urls if str(u).strip())
        else:
            self.source_urls = ()

        if isinstance(self.tags, (list, tuple, set)):
            self.tags = tuple(str(t).strip() for t in self.tags if str(t).strip())
        else:
            self.tags = ()

        if self.provenance_type == ProvenanceType.EXTERNAL_IMPORTED:
            self.taint_status = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "tenant_id": self.tenant_id,
            "memory_type": self.memory_type.value,
            "category": self.category,
            "key": self.key,
            "content": self.content,
            "structured_data": dict(self.structured_data),
            "confidence": self.confidence,
            "provenance_type": self.provenance_type.value,
            "lifecycle_state": self.lifecycle_state.value,
            "version": self.version,
            "supersedes_id": self.supersedes_id,
            "taint_status": self.taint_status,
            "source_urls": list(self.source_urls),
            "tags": list(self.tags),
            "embedding": self.embedding,
            "metadata": dict(self.metadata),
            "access_count": self.access_count,
            "last_accessed_at": self.last_accessed_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CognitiveMemory":
        d = dict(data)
        if "memory_type" in d and isinstance(d["memory_type"], str):
            d["memory_type"] = CognitiveMemoryType(d["memory_type"])
        if "provenance_type" in d and isinstance(d["provenance_type"], str):
            d["provenance_type"] = ProvenanceType(d["provenance_type"])
        if "lifecycle_state" in d and isinstance(d["lifecycle_state"], str):
            d["lifecycle_state"] = LifecycleState(d["lifecycle_state"])
        if "source_urls" in d and isinstance(d["source_urls"], list):
            d["source_urls"] = tuple(d["source_urls"])
        if "tags" in d and isinstance(d["tags"], list):
            d["tags"] = tuple(d["tags"])
        valid_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in d.items() if k in valid_fields}
        return cls(**filtered)


@dataclass
class MemoryContradiction:
    """Record of a detected contradiction between cognitive memories."""
    contradiction_id: str = field(default_factory=lambda: str(uuid4()))
    tenant_id: str = "default"
    memory_a_id: str = ""
    memory_b_id: str = ""
    contradiction_type: str = "fact_conflict"
    resolution_status: ContradictionStatus = ContradictionStatus.DETECTED
    resolution_strategy: ResolutionStrategy = ResolutionStrategy.PROVENANCE_PRECEDENCE
    resolved_by: str | None = None
    resolution_details: dict[str, Any] = field(default_factory=dict)
    detected_at: float = field(default_factory=time.time)
    resolved_at: float | None = None

    def __post_init__(self):
        if isinstance(self.resolution_status, str):
            self.resolution_status = ContradictionStatus(self.resolution_status)
        if isinstance(self.resolution_strategy, str):
            self.resolution_strategy = ResolutionStrategy(self.resolution_strategy)
        self.resolution_details = _sanitize_meta(self.resolution_details)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contradiction_id": self.contradiction_id,
            "tenant_id": self.tenant_id,
            "memory_a_id": self.memory_a_id,
            "memory_b_id": self.memory_b_id,
            "contradiction_type": self.contradiction_type,
            "resolution_status": self.resolution_status.value,
            "resolution_strategy": self.resolution_strategy.value,
            "resolved_by": self.resolved_by,
            "resolution_details": dict(self.resolution_details),
            "detected_at": self.detected_at,
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryContradiction":
        d = dict(data)
        if "resolution_status" in d and isinstance(d["resolution_status"], str):
            d["resolution_status"] = ContradictionStatus(d["resolution_status"])
        if "resolution_strategy" in d and isinstance(d["resolution_strategy"], str):
            d["resolution_strategy"] = ResolutionStrategy(d["resolution_strategy"])
        valid_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in d.items() if k in valid_fields}
        return cls(**filtered)


@dataclass
class UserCognitiveProfile:
    """Structured and dynamic cognitive user profile."""
    profile_id: str = field(default_factory=lambda: str(uuid4()))
    tenant_id: str = "default"
    preferences: dict[str, Any] = field(default_factory=dict)
    inferred_traits: dict[str, Any] = field(default_factory=dict)
    interaction_metrics: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self):
        self.preferences = _sanitize_meta(self.preferences)
        self.inferred_traits = _sanitize_meta(self.inferred_traits)
        self.interaction_metrics = _sanitize_meta(self.interaction_metrics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "tenant_id": self.tenant_id,
            "preferences": dict(self.preferences),
            "inferred_traits": dict(self.inferred_traits),
            "interaction_metrics": dict(self.interaction_metrics),
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "UserCognitiveProfile":
        valid_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


@dataclass
class ExperiencePattern:
    """Aggregated patterns derived from execution episodes."""
    pattern_id: str = field(default_factory=lambda: str(uuid4()))
    tenant_id: str = "default"
    context_key: str = ""
    success_count: int = 0
    failure_count: int = 0
    average_latency_ms: float = 0.0
    optimal_tools: list[str] = field(default_factory=list)
    failure_modes: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    confidence: float = 1.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self):
        self.success_count = max(0, int(self.success_count))
        self.failure_count = max(0, int(self.failure_count))
        self.average_latency_ms = max(0.0, float(self.average_latency_ms))
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

    @property
    def total_attempts(self) -> int:
        return self.success_count + self.failure_count

    @property
    def success_rate(self) -> float:
        if self.total_attempts == 0:
            return 1.0
        return self.success_count / self.total_attempts

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "tenant_id": self.tenant_id,
            "context_key": self.context_key,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "average_latency_ms": self.average_latency_ms,
            "optimal_tools": list(self.optimal_tools),
            "failure_modes": list(self.failure_modes),
            "recommendations": list(self.recommendations),
            "confidence": self.confidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ExperiencePattern":
        valid_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


@dataclass
class MemoryFeedbackEvent:
    """Feedback event captured from user interaction."""
    event_id: str = field(default_factory=lambda: str(uuid4()))
    tenant_id: str = "default"
    target_memory_id: str | None = None
    feedback_type: FeedbackType = FeedbackType.POSITIVE
    correction_content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    applied: bool = False
    created_at: float = field(default_factory=time.time)

    def __post_init__(self):
        if isinstance(self.feedback_type, str):
            self.feedback_type = FeedbackType(self.feedback_type)
        if self.correction_content:
            self.correction_content = scrub_sensitive_content(str(self.correction_content))
        self.metadata = _sanitize_meta(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "tenant_id": self.tenant_id,
            "target_memory_id": self.target_memory_id,
            "feedback_type": self.feedback_type.value,
            "correction_content": self.correction_content,
            "metadata": dict(self.metadata),
            "applied": self.applied,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryFeedbackEvent":
        d = dict(data)
        if "feedback_type" in d and isinstance(d["feedback_type"], str):
            d["feedback_type"] = FeedbackType(d["feedback_type"])
        valid_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in d.items() if k in valid_fields}
        return cls(**filtered)


@dataclass
class ScoredMemoryResult:
    """Search / retrieval result with composite scoring breakdown."""
    memory: CognitiveMemory
    score: float
    relevance_score: float
    confidence_score: float
    recency_score: float


@dataclass
class PersonalizationContext:
    """Synthesized context payload ready for prompt injection."""
    tenant_id: str
    explicit_preferences: list[dict[str, Any]] = field(default_factory=list)
    inferred_traits: list[dict[str, Any]] = field(default_factory=list)
    relevant_facts: list[str] = field(default_factory=list)
    recommended_tools: list[str] = field(default_factory=list)
    formatted_prompt_block: str = ""
