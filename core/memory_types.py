import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from core.provenance import TaintedValue, is_tainted, unwrap_tainted, wrap_tainted


def _sanitize_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Recursively sanitize metadata dictionary to ensure no callables or untrusted permission overrides."""
    if not isinstance(meta, dict):
        raise TypeError("metadata must be a dictionary.")

    forbidden_keys = frozenset({
        "approved",
        "approval_status",
        "is_approved",
        "auto_approve",
        "permission",
        "authorized",
    })

    cleaned: dict[str, Any] = {}
    for k, v in meta.items():
        k_str = str(k)
        if k_str.lower() in forbidden_keys:
            continue
        if callable(v):
            continue
        if isinstance(v, dict):
            cleaned[k_str] = _sanitize_metadata(v)
        elif isinstance(v, (list, tuple)):
            cleaned[k_str] = [
                _sanitize_metadata(item) if isinstance(item, dict) else (str(item) if not callable(item) else "")
                for item in v
            ]
        elif isinstance(v, (str, int, float, bool)) or v is None:
            cleaned[k_str] = v
        elif isinstance(v, TaintedValue):
            cleaned[k_str] = v
        else:
            cleaned[k_str] = repr(v)
    return cleaned


def _canonical_value(val: Any) -> Any:
    """Convert values into deterministic, JSON-serializable primitives preserving TaintedValue."""
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
    elif callable(val):
        raise ValueError("Cannot serialize callable value in memory entry.")
    else:
        return repr(val)


def _restore_value(val: Any) -> Any:
    """Restore values from serialized JSON primitives restoring TaintedValue envelopes."""
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


class MemoryTier(str, Enum):
    """Memory hierarchy tiers for agent storage and recall."""

    WORKING = "working"      # Short-term scratchpad scoped to a task or execution cycle
    SEMANTIC = "semantic"    # Durable user facts, preferences, domain concepts, and entity profiles
    EPISODIC = "episodic"    # Historical records of past task runs, plans, observations, and replans


class MemoryNamespace(str, Enum):
    """Namespaces for organizing and isolating memory entries."""

    USER_PROFILE = "user_profile"
    USER_PREFERENCES = "user_preferences"
    DOMAIN_KNOWLEDGE = "domain_knowledge"
    SYSTEM_FACTS = "system_facts"
    TASK_SCRATCHPAD = "task_scratchpad"
    EXECUTION_HISTORY = "execution_history"
    GENERAL = "general"
    CUSTOM = "custom"


@dataclass(frozen=True)
class MemoryEntry:
    """Immutable record stored in the agent's multi-tier memory system."""

    entry_id: str = field(default_factory=lambda: str(uuid4()))
    tier: MemoryTier = MemoryTier.SEMANTIC
    namespace: str = MemoryNamespace.CUSTOM.value
    key: str = ""
    value: Any = None
    confidence: float = 1.0
    is_untrusted: bool = False
    source_urls: tuple[str, ...] = field(default_factory=tuple)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.entry_id, str) or not self.entry_id.strip():
            raise ValueError("entry_id must be a non-empty string.")
        object.__setattr__(self, "entry_id", self.entry_id.strip())

        if isinstance(self.tier, str):
            object.__setattr__(self, "tier", MemoryTier(self.tier))
        elif not isinstance(self.tier, MemoryTier):
            raise TypeError("tier must be an instance of MemoryTier.")

        if isinstance(self.namespace, MemoryNamespace):
            object.__setattr__(self, "namespace", self.namespace.value)
        elif isinstance(self.namespace, str):
            if not self.namespace.strip():
                raise ValueError("namespace must be a non-empty string.")
            object.__setattr__(self, "namespace", self.namespace.strip())
        else:
            raise TypeError("namespace must be a string or MemoryNamespace.")

        if not isinstance(self.key, str) or not self.key.strip():
            raise ValueError("key must be a non-empty string.")
        object.__setattr__(self, "key", self.key.strip())

        if callable(self.value):
            raise ValueError("MemoryEntry value cannot be callable.")

        if not isinstance(self.confidence, (int, float)):
            raise TypeError("confidence must be a number between 0.0 and 1.0.")
        conf = max(0.0, min(1.0, float(self.confidence)))
        object.__setattr__(self, "confidence", conf)

        # Untrusted flag propagation
        untrusted = bool(self.is_untrusted)
        urls: list[str] = []
        if isinstance(self.source_urls, (list, tuple, set)):
            urls.extend(str(u).strip() for u in self.source_urls if str(u).strip())
        elif self.source_urls is not None:
            raise TypeError("source_urls must be a sequence of strings.")

        if isinstance(self.value, TaintedValue) or is_tainted(self.value):
            untrusted = True
            if isinstance(self.value, TaintedValue):
                urls.extend(self.value.source_urls)

        object.__setattr__(self, "is_untrusted", untrusted)
        object.__setattr__(self, "source_urls", tuple(sorted(set(urls))))

        if not isinstance(self.created_at, (int, float)):
            raise TypeError("created_at must be numeric.")
        if not isinstance(self.updated_at, (int, float)):
            raise TypeError("updated_at must be numeric.")

        if self.expires_at is not None:
            if not isinstance(self.expires_at, (int, float)):
                raise TypeError("expires_at must be numeric or None.")
            object.__setattr__(self, "expires_at", float(self.expires_at))

        object.__setattr__(self, "metadata", _sanitize_metadata(self.metadata))

    @property
    def id(self) -> str:
        return self.entry_id

    def is_expired(self, current_time: float | None = None) -> bool:
        """Check if memory entry has passed its expiration time."""
        if self.expires_at is None:
            return False
        now = current_time if current_time is not None else time.time()
        return now >= self.expires_at

    def with_value(self, value: Any, confidence: float | None = None, updated_at: float | None = None) -> "MemoryEntry":
        """Return a new immutable MemoryEntry with updated value and timestamp."""
        now = updated_at if updated_at is not None else time.time()
        conf = confidence if confidence is not None else self.confidence
        return MemoryEntry(
            entry_id=self.entry_id,
            tier=self.tier,
            namespace=self.namespace,
            key=self.key,
            value=value,
            confidence=conf,
            is_untrusted=self.is_untrusted,
            source_urls=self.source_urls,
            created_at=self.created_at,
            updated_at=now,
            expires_at=self.expires_at,
            metadata=dict(self.metadata),
        )


@dataclass
class SemanticFact:
    """Structured semantic subject-predicate-object knowledge assertion."""

    subject: str
    predicate: str
    object_value: Any = None
    confidence: float = 1.0
    is_untrusted: bool = False
    source_urls: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str | None = None

    def __post_init__(self):
        if not isinstance(self.subject, str) or not self.subject.strip():
            raise ValueError("subject must be a non-empty string.")
        self.subject = self.subject.strip()

        if not isinstance(self.predicate, str) or not self.predicate.strip():
            raise ValueError("predicate must be a non-empty string.")
        self.predicate = self.predicate.strip()

        if callable(self.object_value):
            raise ValueError("SemanticFact object_value cannot be callable.")

        if not isinstance(self.confidence, (int, float)):
            raise TypeError("confidence must be a number between 0.0 and 1.0.")
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

        untrusted = bool(self.is_untrusted)
        urls: list[str] = []
        if isinstance(self.source_urls, (list, tuple, set)):
            urls.extend(str(u).strip() for u in self.source_urls if str(u).strip())
        if isinstance(self.object_value, TaintedValue) or is_tainted(self.object_value):
            untrusted = True
            if isinstance(self.object_value, TaintedValue):
                urls.extend(self.object_value.source_urls)

        self.is_untrusted = untrusted
        self.source_urls = tuple(sorted(set(urls)))
        self.metadata = _sanitize_metadata(self.metadata)

    @property
    def key(self) -> str:
        """Deterministic canonical key for the semantic fact."""
        return f"{self.subject}:{self.predicate}"

    @property
    def object_val(self) -> Any:
        return self.object_value

    def to_memory_entry(self, namespace: str | MemoryNamespace = MemoryNamespace.USER_PROFILE) -> MemoryEntry:
        """Convert the semantic fact into a MemoryEntry."""
        ns = namespace.value if isinstance(namespace, MemoryNamespace) else namespace
        entry_kwargs = {
            "tier": MemoryTier.SEMANTIC,
            "namespace": ns,
            "key": self.key,
            "value": self.object_value,
            "confidence": self.confidence,
            "is_untrusted": self.is_untrusted,
            "source_urls": self.source_urls,
            "metadata": {
                "subject": self.subject,
                "predicate": self.predicate,
                **self.metadata,
            },
        }
        if self.id:
            entry_kwargs["entry_id"] = self.id
        return MemoryEntry(**entry_kwargs)

    @classmethod
    def from_memory_entry(cls, entry: MemoryEntry) -> "SemanticFact":
        meta = dict(entry.metadata)
        subject = meta.get("subject", "")
        predicate = meta.get("predicate", "")
        if not subject or not predicate:
            if ":" in entry.key:
                parts = entry.key.split(":", 1)
                subject, predicate = parts[0], parts[1]
            else:
                subject = entry.key
                predicate = "value"
        return cls(
            subject=subject,
            predicate=predicate,
            object_value=entry.value,
            confidence=entry.confidence,
            is_untrusted=entry.is_untrusted,
            source_urls=entry.source_urls,
            metadata=meta,
            id=entry.entry_id,
        )


@dataclass
class EpisodicRecord:
    """Historical record of an executed task or plan episode for experience reuse."""

    task_id: str
    plan_id: str = ""
    task_goal: str = ""
    success: bool = True
    executed_skills: tuple[str, ...] = field(default_factory=tuple)
    error: str | None = None
    execution_time_ms: float = 0.0
    trace_summary: dict[str, Any] = field(default_factory=dict)
    is_untrusted: bool = False
    source_urls: tuple[str, ...] = field(default_factory=tuple)
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str | None = None

    def __post_init__(self):
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise ValueError("task_id must be a non-empty string.")
        self.task_id = self.task_id.strip()

        if not isinstance(self.plan_id, str):
            raise TypeError("plan_id must be a string.")
        self.plan_id = self.plan_id.strip()

        if not isinstance(self.task_goal, str):
            raise TypeError("task_goal must be a string.")
        self.task_goal = self.task_goal.strip()

        if not isinstance(self.success, bool):
            raise TypeError("success must be a boolean.")

        if isinstance(self.executed_skills, (list, tuple, set)):
            self.executed_skills = tuple(str(s).strip() for s in self.executed_skills if str(s).strip())
        else:
            raise TypeError("executed_skills must be a sequence of strings.")

        if self.error is not None and not isinstance(self.error, str):
            raise TypeError("error must be a string or None.")

        if not isinstance(self.execution_time_ms, (int, float)) or self.execution_time_ms < 0:
            raise ValueError("execution_time_ms must be non-negative.")
        self.execution_time_ms = float(self.execution_time_ms)

        if not isinstance(self.trace_summary, dict):
            raise TypeError("trace_summary must be a dictionary.")

        if isinstance(self.source_urls, (list, tuple, set)):
            self.source_urls = tuple(str(u).strip() for u in self.source_urls if str(u).strip())
        else:
            raise TypeError("source_urls must be a sequence of strings.")

        if not isinstance(self.created_at, (int, float)):
            raise TypeError("created_at must be numeric.")

        self.metadata = _sanitize_metadata(self.metadata)

    def to_memory_entry(self) -> MemoryEntry:
        """Convert the episodic record into an EPISODIC tier MemoryEntry."""
        entry_kwargs = {
            "tier": MemoryTier.EPISODIC,
            "namespace": MemoryNamespace.EXECUTION_HISTORY.value,
            "key": f"episode:{self.task_id}",
            "value": {
                "task_id": self.task_id,
                "plan_id": self.plan_id,
                "task_goal": self.task_goal,
                "success": self.success,
                "executed_skills": list(self.executed_skills),
                "error": self.error,
                "execution_time_ms": self.execution_time_ms,
                "trace_summary": _canonical_value(self.trace_summary),
            },
            "confidence": 1.0 if self.success else 0.5,
            "is_untrusted": self.is_untrusted,
            "source_urls": self.source_urls,
            "created_at": self.created_at,
            "metadata": {
                "task_id": self.task_id,
                "plan_id": self.plan_id,
                "success": self.success,
                "executed_skills": list(self.executed_skills),
                **self.metadata,
            },
        }
        if self.id:
            entry_kwargs["entry_id"] = self.id
        return MemoryEntry(**entry_kwargs)

    @classmethod
    def from_memory_entry(cls, entry: MemoryEntry) -> "EpisodicRecord":
        val = entry.value if isinstance(entry.value, dict) else {}
        meta = dict(entry.metadata)
        task_id = str(val.get("task_id", meta.get("task_id", entry.key.replace("episode:", ""))))
        plan_id = str(val.get("plan_id", meta.get("plan_id", "")))
        task_goal = str(val.get("task_goal", meta.get("task_goal", "")))
        success = bool(val.get("success", meta.get("success", entry.confidence >= 0.8)))
        executed_skills = val.get("executed_skills", meta.get("executed_skills", ()))
        error = val.get("error", meta.get("error"))
        execution_time_ms = float(val.get("execution_time_ms", meta.get("execution_time_ms", 0.0)))
        trace_summary = val.get("trace_summary", meta.get("trace_summary", {}))
        if not isinstance(trace_summary, dict):
            trace_summary = {}

        return cls(
            task_id=task_id,
            plan_id=plan_id,
            task_goal=task_goal,
            success=success,
            executed_skills=tuple(executed_skills),
            error=error,
            execution_time_ms=execution_time_ms,
            trace_summary=trace_summary,
            is_untrusted=entry.is_untrusted,
            source_urls=entry.source_urls,
            created_at=entry.created_at,
            metadata=meta,
            id=entry.entry_id,
        )


def serialize_memory_entry(entry: MemoryEntry) -> dict[str, Any]:
    """Serialize a MemoryEntry into a deterministic JSON-serializable dictionary."""
    if not isinstance(entry, MemoryEntry):
        raise TypeError("entry must be a MemoryEntry instance.")

    return {
        "entry_id": entry.entry_id,
        "tier": entry.tier.value,
        "namespace": entry.namespace,
        "key": entry.key,
        "value": _canonical_value(entry.value),
        "confidence": entry.confidence,
        "is_untrusted": entry.is_untrusted,
        "source_urls": list(entry.source_urls),
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
        "expires_at": entry.expires_at,
        "metadata": _canonical_value(entry.metadata),
    }


def deserialize_memory_entry(data: dict[str, Any]) -> MemoryEntry:
    """Deserialize a MemoryEntry from a dictionary restoring TaintedValue envelopes."""
    if not isinstance(data, dict):
        raise TypeError("data must be a dictionary.")

    return MemoryEntry(
        entry_id=str(data.get("entry_id", uuid4())),
        tier=MemoryTier(data.get("tier", MemoryTier.SEMANTIC.value)),
        namespace=str(data.get("namespace", MemoryNamespace.CUSTOM.value)),
        key=str(data.get("key", "")),
        value=_restore_value(data.get("value")),
        confidence=float(data.get("confidence", 1.0)),
        is_untrusted=bool(data.get("is_untrusted", False)),
        source_urls=tuple(data.get("source_urls", ())),
        created_at=float(data.get("created_at", time.time())),
        updated_at=float(data.get("updated_at", time.time())),
        expires_at=data.get("expires_at"),
        metadata=dict(data.get("metadata", {})),
    )
