"""M31 — Advanced Retrieval & Multi-Source RAG Types.

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
    authority: AuthorityTier = AuthorityTier.USER
    provenance: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_type": self.source_type.value,
            "title": self.title,
            "text": self.text,
            "score": round(self.score, 4),
            "confidence": round(self.confidence, 4),
            "authority": self.authority.value,
            "provenance": self.provenance,
            "tags": self.tags,
            "metadata": self.metadata,
        }


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
            "retrieval_latency_ms": round(self.retrieval_latency_ms, 2),
            "created_at": self.created_at,
        }


@dataclass
class RetrievalEvaluationMetrics:
    precision_at_k: float = 0.0
    recall_at_k: float = 0.0
    mrr: float = 0.0  # Mean Reciprocal Rank
    k: int = 5

    def to_dict(self) -> dict[str, Any]:
        return {
            "precision_at_k": round(self.precision_at_k, 4),
            "recall_at_k": round(self.recall_at_k, 4),
            "mrr": round(self.mrr, 4),
            "k": self.k,
        }
