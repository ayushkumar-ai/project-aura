"""Milestone 28: Epistemic Knowledge Graph & Experience Distillation Type Contracts.

Defines strongly typed, immutable data structures for knowledge entities,
directed typed relations, graph queries, subgraphs, and metadata sanitization.
All structures are frozen to guarantee audit integrity and deterministic indexing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from core.provenance import TaintedValue, is_tainted

# ---------------------------------------------------------------------------
# Security Blocklist & Sanitization Constants
# ---------------------------------------------------------------------------
FORBIDDEN_GRAPH_METADATA_KEYS = frozenset({
    "approved",
    "approval_status",
    "is_approved",
    "auto_approve",
    "permission",
    "authorized",
    "is_admin",
    "is_authorized",
    "bypass_policy",
    "sudo",
    "override",
    "system_override",
})

MAX_METADATA_ENTRIES = 32
MAX_STRING_LENGTH = 2048


def _sanitize_graph_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Sanitize metadata dictionary, stripping privilege-escalation keys."""
    if not isinstance(meta, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for k, v in list(meta.items())[:MAX_METADATA_ENTRIES]:
        k_str = str(k).strip()
        if not k_str or k_str.lower() in FORBIDDEN_GRAPH_METADATA_KEYS or callable(v):
            continue
        if isinstance(v, (str, int, float, bool)) or v is None:
            cleaned[k_str] = str(v)[:MAX_STRING_LENGTH] if isinstance(v, str) else v
        elif isinstance(v, TaintedValue):
            cleaned[k_str] = v
        elif isinstance(v, dict):
            cleaned[k_str] = _sanitize_graph_metadata(v)
        elif isinstance(v, (list, tuple, set)):
            cleaned[k_str] = [
                str(x)[:MAX_STRING_LENGTH] if isinstance(x, str) else x
                for x in v if not callable(x)
            ]
        else:
            cleaned[k_str] = repr(v)[:512]
    return cleaned


# ---------------------------------------------------------------------------
# Entity & Relation Taxonomy
# ---------------------------------------------------------------------------

class EntityType(str, Enum):
    """Categorical taxonomy for Epistemic Knowledge Graph entities."""

    GOAL = "goal"
    """Execution goal or task specification."""

    ROLE = "role"
    """Multi-agent persona, team member role, or specialized capability provider."""

    SKILL = "skill"
    """Synthesized programmatic tool, static skill, or composite pipeline."""

    ARTIFACT = "artifact"
    """Versioned CAS content, dataset, report, or intermediate result."""

    FAULT_PATTERN = "fault_pattern"
    """Classified causal failure mode or recurring diagnostic signature."""

    REMEDIATION_RECIPE = "remediation_recipe"
    """Proven sequence of remediation actions that successfully recovered a fault."""

    EXECUTION_PATTERN = "execution_pattern"
    """Proven multi-phase mission DAG topology or successful delegation strategy."""


class RelationType(str, Enum):
    """Directed relational semantics between knowledge entities."""

    PRODUCED_BY = "produced_by"
    """Artifact or Result -> Producer Goal/Skill/Role."""

    CONSUMED_BY = "consumed_by"
    """Artifact -> Consumer Goal/Skill."""

    DELEGATED_TO = "delegated_to"
    """Parent Goal/Role -> Delegatee Goal/Role."""

    RESOLVED_BY = "resolved_by"
    """Fault Pattern -> Remediation Recipe."""

    CAUSED_BY = "caused_by"
    """Fault Pattern -> Defective Skill/Span/Resource."""

    SPECIALIZES = "specializes"
    """Derived Skill/Role -> Base Skill/Role."""

    DEPENDS_ON = "depends_on"
    """Phase/Goal -> Prerequisite Phase/Goal/Artifact."""

    COLLABORATED_WITH = "collaborated_with"
    """Role -> Peer Role in a team execution."""

    SIMILAR_TO = "similar_to"
    """Goal/Skill -> Analogous Goal/Skill in capability space."""


# ---------------------------------------------------------------------------
# Core Knowledge Graph Data Structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class KnowledgeEntity:
    """Immutable vertex in the Epistemic Knowledge Graph."""

    entity_id: str
    entity_type: EntityType
    name: str
    properties: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    created_at: float = field(default_factory=time.time)
    access_count: int = 0
    last_accessed_at: float = field(default_factory=time.time)
    is_untrusted: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        eid = str(self.entity_id).strip()
        if not eid:
            raise ValueError("entity_id must be a non-empty string.")
        object.__setattr__(self, "entity_id", eid)

        if isinstance(self.entity_type, str):
            object.__setattr__(self, "entity_type", EntityType(self.entity_type))
        elif not isinstance(self.entity_type, EntityType):
            raise TypeError("entity_type must be an EntityType instance.")

        nm = str(self.name).strip()
        object.__setattr__(self, "name", nm if nm else "unnamed_entity")

        props = _sanitize_graph_metadata(self.properties or {})
        object.__setattr__(self, "properties", props)

        conf = max(0.0, min(1.0, float(self.confidence)))
        object.__setattr__(self, "confidence", round(conf, 4))

        object.__setattr__(self, "created_at", float(self.created_at))
        object.__setattr__(self, "access_count", max(0, int(self.access_count)))
        object.__setattr__(self, "last_accessed_at", float(self.last_accessed_at))

        # Check if any properties contain tainted values
        untrusted = bool(self.is_untrusted)
        if any(is_tainted(v) for v in props.values()):
            untrusted = True
        object.__setattr__(self, "is_untrusted", untrusted)
        object.__setattr__(self, "metadata", _sanitize_graph_metadata(self.metadata or {}))

    def with_access(self) -> "KnowledgeEntity":
        """Return a copy with incremented access count and updated timestamp."""
        return KnowledgeEntity(
            entity_id=self.entity_id,
            entity_type=self.entity_type,
            name=self.name,
            properties=dict(self.properties),
            confidence=self.confidence,
            created_at=self.created_at,
            access_count=self.access_count + 1,
            last_accessed_at=time.time(),
            is_untrusted=self.is_untrusted,
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type.value,
            "name": self.name,
            "properties": dict(self.properties),
            "confidence": self.confidence,
            "created_at": self.created_at,
            "access_count": self.access_count,
            "last_accessed_at": self.last_accessed_at,
            "is_untrusted": self.is_untrusted,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KnowledgeEntity":
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        return cls(
            entity_id=str(data.get("entity_id", "")),
            entity_type=EntityType(data.get("entity_type", EntityType.GOAL.value)),
            name=str(data.get("name", "")),
            properties=data.get("properties", {}),
            confidence=float(data.get("confidence", 1.0)),
            created_at=float(data.get("created_at", time.time())),
            access_count=int(data.get("access_count", 0)),
            last_accessed_at=float(data.get("last_accessed_at", time.time())),
            is_untrusted=bool(data.get("is_untrusted", False)),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True)
class KnowledgeRelation:
    """Immutable directed edge connecting two entities in the knowledge graph."""

    relation_id: str
    source_id: str
    target_id: str
    relation_type: RelationType
    weight: float = 1.0
    confidence: float = 1.0
    evidence_count: int = 1
    created_at: float = field(default_factory=time.time)
    is_untrusted: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        rid = str(self.relation_id).strip()
        if not rid:
            raise ValueError("relation_id must be a non-empty string.")
        object.__setattr__(self, "relation_id", rid)

        src = str(self.source_id).strip()
        tgt = str(self.target_id).strip()
        if not src or not tgt:
            raise ValueError("source_id and target_id must both be non-empty strings.")
        object.__setattr__(self, "source_id", src)
        object.__setattr__(self, "target_id", tgt)

        if isinstance(self.relation_type, str):
            object.__setattr__(self, "relation_type", RelationType(self.relation_type))
        elif not isinstance(self.relation_type, RelationType):
            raise TypeError("relation_type must be a RelationType instance.")

        wt = max(0.0, float(self.weight))
        object.__setattr__(self, "weight", round(wt, 4))

        conf = max(0.0, min(1.0, float(self.confidence)))
        object.__setattr__(self, "confidence", round(conf, 4))

        object.__setattr__(self, "evidence_count", max(1, int(self.evidence_count)))
        object.__setattr__(self, "created_at", float(self.created_at))
        object.__setattr__(self, "is_untrusted", bool(self.is_untrusted))
        object.__setattr__(self, "metadata", _sanitize_graph_metadata(self.metadata or {}))

    def with_reinforcement(self, weight_delta: float = 0.1, confidence_boost: float = 0.05) -> "KnowledgeRelation":
        """Return a reinforced relation with increased weight, confidence, and evidence count."""
        return KnowledgeRelation(
            relation_id=self.relation_id,
            source_id=self.source_id,
            target_id=self.target_id,
            relation_type=self.relation_type,
            weight=self.weight + weight_delta,
            confidence=min(1.0, self.confidence + confidence_boost),
            evidence_count=self.evidence_count + 1,
            created_at=self.created_at,
            is_untrusted=self.is_untrusted,
            metadata=dict(self.metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_id": self.relation_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation_type": self.relation_type.value,
            "weight": self.weight,
            "confidence": self.confidence,
            "evidence_count": self.evidence_count,
            "created_at": self.created_at,
            "is_untrusted": self.is_untrusted,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KnowledgeRelation":
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        return cls(
            relation_id=str(data.get("relation_id", "")),
            source_id=str(data.get("source_id", "")),
            target_id=str(data.get("target_id", "")),
            relation_type=RelationType(data.get("relation_type", RelationType.DEPENDS_ON.value)),
            weight=float(data.get("weight", 1.0)),
            confidence=float(data.get("confidence", 1.0)),
            evidence_count=int(data.get("evidence_count", 1)),
            created_at=float(data.get("created_at", time.time())),
            is_untrusted=bool(data.get("is_untrusted", False)),
            metadata=data.get("metadata", {}),
        )


# ---------------------------------------------------------------------------
# Query & Subgraph Envelopes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class KnowledgeGraphQuery:
    """Multi-criteria search query over the knowledge graph."""

    entity_types: tuple[EntityType, ...] = ()
    relation_types: tuple[RelationType, ...] = ()
    keyword: str = ""
    root_entity_id: str | None = None
    max_depth: int = 2
    min_confidence: float = 0.0
    limit: int = 50

    def __post_init__(self) -> None:
        if isinstance(self.entity_types, (list, set)):
            object.__setattr__(self, "entity_types", tuple(EntityType(t) for t in self.entity_types))
        if isinstance(self.relation_types, (list, set)):
            object.__setattr__(self, "relation_types", tuple(RelationType(r) for r in self.relation_types))
        object.__setattr__(self, "keyword", str(self.keyword or "").strip().lower())
        if self.root_entity_id is not None:
            object.__setattr__(self, "root_entity_id", str(self.root_entity_id).strip() or None)
        object.__setattr__(self, "max_depth", max(1, min(5, int(self.max_depth))))
        object.__setattr__(self, "min_confidence", max(0.0, min(1.0, float(self.min_confidence))))
        object.__setattr__(self, "limit", max(1, min(500, int(self.limit))))


@dataclass(frozen=True)
class KnowledgeGraphSubgraph:
    """Traversed sub-graph extraction containing nodes and interconnecting edges."""

    entities: tuple[KnowledgeEntity, ...]
    relations: tuple[KnowledgeRelation, ...]
    root_entity_id: str | None = None
    query_keyword: str = ""
    extracted_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entities": [e.to_dict() for e in self.entities],
            "relations": [r.to_dict() for r in self.relations],
            "root_entity_id": self.root_entity_id,
            "query_keyword": self.query_keyword,
            "extracted_at": self.extracted_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KnowledgeGraphSubgraph":
        return cls(
            entities=tuple(KnowledgeEntity.from_dict(e) for e in data.get("entities", [])),
            relations=tuple(KnowledgeRelation.from_dict(r) for r in data.get("relations", [])),
            root_entity_id=data.get("root_entity_id"),
            query_keyword=str(data.get("query_keyword", "")),
            extracted_at=float(data.get("extracted_at", time.time())),
        )
