"""M31/M41 — Advanced Retrieval & Multi-Source RAG Types with User Isolation.

Defines schemas for queries, candidate items, ranking results, source categories,
context assembly bundles, and retrieval evaluation metrics.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class RetrievalSourceType(str, Enum):
    KNOWLEDGE_BASE = "knowledge_base"
    PERSONAL_MEMORY = "personal_memory"
    EPISTEMIC_GRAPH = "epistemic_graph"
    EXPERIENCES = "experiences"
    ARTIFACTS = "artifacts"
    CONVERSATION = "conversation"


class AuthorityTier(str, Enum):
    SYSTEM = "system"
    VERIFIED = "verified"
    USER = "user"
    INFERRED = "inferred"
    UNTRUSTED = "untrusted"


@dataclass
class RetrievalQuery:
    query_text: str
    user_id: str | None = None
    source_types: list[RetrievalSourceType] = field(
        default_factory=lambda: [
            RetrievalSourceType.KNOWLEDGE_BASE,
            RetrievalSourceType.PERSONAL_MEMORY,
            RetrievalSourceType.EPISTEMIC_GRAPH,
            RetrievalSourceType.EXPERIENCES,
            RetrievalSourceType.ARTIFACTS,
        ]
    )
    min_score: float = 0.1
    limit: int = 10
    required_tags: list[str] = field(default_factory=list)
    min_authority: AuthorityTier = AuthorityTier.INFERRED
    scrub_secrets: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_text": self.query_text,
            "user_id": self.user_id,
            "source_types": [s.value for s in self.source_types],
            "min_score": self.min_score,
            "limit": self.limit,
            "required_tags": self.required_tags,
            "min_authority": self.min_authority.value,
            "scrub_secrets": self.scrub_secrets,
            "metadata": self.metadata,
        }


@dataclass
class RetrievalCandidate:
    candidate_id: str
    source_type: RetrievalSourceType
    title: str
    text: str
    score: float = 0.0
    confidence: float = 1.0
    authority: AuthorityTier = AuthorityTier.VERIFIED
    tags: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["source_type"] = self.source_type.value
        d["authority"] = self.authority.value
        return d


@dataclass
class RetrievalContextBundle:
    query: str
    candidates: list[RetrievalCandidate] = field(default_factory=list)
    assembled_text: str = ""
    source_counts: dict[str, int] = field(default_factory=dict)
    retrieval_latency_ms: float = 0.0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "candidates": [c.to_dict() for c in self.candidates],
            "assembled_text": self.assembled_text,
            "source_counts": self.source_counts,
            "retrieval_latency_ms": self.retrieval_latency_ms,
            "created_at": self.created_at,
        }


@dataclass
class RetrievalEvaluationMetrics:
    precision_at_k: float
    recall_at_k: float
    mrr: float
    k: int
    evaluated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
