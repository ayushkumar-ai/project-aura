"""M31 — Advanced Retrieval & Multi-Source RAG Pipeline for Project AURA.

Unifies knowledge base retrieval, personal memory, epistemic graph queries,
episodic experiences, and artifacts into a coherent, ranked context bundle.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from typing import Any
from uuid import uuid4

from core.personal_state_types import MemoryCategory
from core.retrieval_types import (
    AuthorityTier,
    RetrievalCandidate,
    RetrievalContextBundle,
    RetrievalEvaluationMetrics,
    RetrievalQuery,
    RetrievalSourceType,
)
from core.security_scrubber import scrub_string

logger = logging.getLogger("aura.retrieval_pipeline")


class AdvancedRetrievalPipeline:
    """Multi-source retrieval and RAG pipeline with deterministic ranking and security scrubbing."""

    def __init__(
        self,
        durable_state_store: Any | None = None,
        epistemic_graph: Any | None = None,
        artifact_manager: Any | None = None,
        knowledge_documents: list[dict[str, Any]] | None = None,
    ):
        self.durable_state_store = durable_state_store
        self.epistemic_graph = epistemic_graph
        self.artifact_manager = artifact_manager
        self._knowledge_documents = list(knowledge_documents or [])
        self._lock = threading.RLock()

    def add_knowledge_document(
        self,
        doc_id: str,
        title: str,
        content: str,
        tags: list[str] | None = None,
        authority: AuthorityTier = AuthorityTier.VERIFIED,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Add a controlled knowledge base document."""
        with self._lock:
            self._knowledge_documents.append({
                "doc_id": doc_id,
                "title": title,
                "content": content,
                "tags": tags or [],
                "authority": authority,
                "metadata": metadata or {},
            })

    def _tokenize(self, text: str) -> list[str]:
        """Simple deterministic whitespace/punctuation tokenizer."""
        return [w.lower() for w in re.findall(r"\b\w{2,}\b", text)]

    def _compute_relevance_score(self, query_tokens: list[str], text: str, title: str = "") -> float:
        """Compute term-matching and semantic relevance score."""
        if not query_tokens or not text:
            return 0.0

        full_content = f"{title} {text}".lower()
        content_tokens = self._tokenize(full_content)
        if not content_tokens:
            return 0.0

        score = 0.0

        # Token overlap / Term Frequency
        matches = sum(1 for t in query_tokens if t in full_content)
        overlap_ratio = matches / len(query_tokens)
        score += overlap_ratio * 0.6

        # Exact phrase bonus
        query_phrase = " ".join(query_tokens)
        if query_phrase and query_phrase in full_content:
            score += 0.4

        # Title bonus
        title_lower = title.lower()
        title_matches = sum(1 for t in query_tokens if t in title_lower)
        if title_matches:
            score += (title_matches / len(query_tokens)) * 0.3

        return min(score, 1.0)

    def retrieve_candidates(self, query: RetrievalQuery) -> list[RetrievalCandidate]:
        """Fetch candidates across all requested and available sources."""
        candidates: list[RetrievalCandidate] = []
        q_tokens = self._tokenize(query.query_text)

        # 1. Knowledge Base Documents
        if RetrievalSourceType.KNOWLEDGE_BASE in query.source_types:
            for doc in self._knowledge_documents:
                score = self._compute_relevance_score(q_tokens, doc["content"], doc["title"])
                if score >= query.min_score:
                    candidates.append(
                        RetrievalCandidate(
                            candidate_id=doc["doc_id"],
                            source_type=RetrievalSourceType.KNOWLEDGE_BASE,
                            title=doc["title"],
                            text=doc["content"],
                            score=score,
                            confidence=0.95,
                            authority=doc.get("authority", AuthorityTier.VERIFIED),
                            tags=doc.get("tags", []),
                            metadata=doc.get("metadata", {}),
                        )
                    )

        # 2. Personal Memory
        if RetrievalSourceType.PERSONAL_MEMORY in query.source_types and self.durable_state_store:
            try:
                # Fetch all memories and score with token relevance
                memories = self.durable_state_store.query_memories(limit=100)
                for mem in memories:
                    score = self._compute_relevance_score(q_tokens, mem.content)
                    if score >= query.min_score:
                        candidates.append(
                            RetrievalCandidate(
                                candidate_id=mem.record_id,
                                source_type=RetrievalSourceType.PERSONAL_MEMORY,
                                title=f"Memory ({mem.category.value})",
                                text=mem.content,
                                score=score * mem.confidence,
                                confidence=mem.confidence,
                                authority=AuthorityTier.USER,
                                tags=mem.tags,
                                metadata=mem.metadata,
                            )
                        )
            except Exception as e:
                logger.warning(f"Error querying personal memory for retrieval: {e}")

        # 3. Episodic Experiences
        if RetrievalSourceType.EXPERIENCES in query.source_types and self.durable_state_store:
            try:
                # Fetch all experiences and score with token relevance
                experiences = self.durable_state_store.query_experiences(limit=50)
                for exp in experiences:
                    exp_text = f"Task: {exp.task_description}\nPlan: {exp.plan_summary}\nOutcome: {exp.outcome}\nLessons: {'; '.join(exp.lessons_learned)}"
                    score = self._compute_relevance_score(q_tokens, exp_text)
                    if score >= query.min_score:
                        candidates.append(
                            RetrievalCandidate(
                                candidate_id=exp.experience_id,
                                source_type=RetrievalSourceType.EXPERIENCES,
                                title=f"Experience: {exp.task_description[:40]}",
                                text=exp_text,
                                score=score * max(0.1, exp.reward_score),
                                confidence=0.9,
                                authority=AuthorityTier.SYSTEM,
                                tags=["experience", exp.outcome],
                                metadata=exp.metadata,
                            )
                        )
            except Exception as e:
                logger.warning(f"Error querying experiences for retrieval: {e}")

        # 4. Epistemic Graph Entities
        if RetrievalSourceType.EPISTEMIC_GRAPH in query.source_types and self.epistemic_graph:
            try:
                entities = getattr(self.epistemic_graph, "entities", {})
                if isinstance(entities, dict):
                    for e_id, entity in entities.items():
                        e_name = getattr(entity, "name", str(entity))
                        e_desc = getattr(entity, "description", "")
                        score = self._compute_relevance_score(q_tokens, e_desc, e_name)
                        if score >= query.min_score:
                            candidates.append(
                                RetrievalCandidate(
                                    candidate_id=e_id,
                                    source_type=RetrievalSourceType.EPISTEMIC_GRAPH,
                                    title=f"Entity: {e_name}",
                                    text=e_desc or e_name,
                                    score=score,
                                    confidence=getattr(entity, "confidence", 0.9),
                                    authority=AuthorityTier.VERIFIED,
                                    tags=[getattr(getattr(entity, "entity_type", None), "value", "concept")],
                                    metadata={},
                                )
                            )
            except Exception as e:
                logger.warning(f"Error querying epistemic graph: {e}")

        # Filter by required tags if requested
        if query.required_tags:
            candidates = [
                c for c in candidates
                if any(tag in c.tags for tag in query.required_tags)
            ]

        # Security scrubbing
        if query.scrub_secrets:
            for c in candidates:
                c.text = scrub_string(c.text)

        # Sort by score descending and apply limit
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:query.limit]

    def execute_rag(
        self,
        query: str | RetrievalQuery,
        max_context_chars: int = 4000,
    ) -> RetrievalContextBundle:
        """Execute complete RAG pipeline and assemble structured context."""
        start_time = time.time()
        ret_query = query if isinstance(query, RetrievalQuery) else RetrievalQuery(query_text=str(query))

        candidates = self.retrieve_candidates(ret_query)

        source_counts: dict[str, int] = {}
        assembled_lines: list[str] = []
        current_chars = 0

        for idx, c in enumerate(candidates, 1):
            source_counts[c.source_type.value] = source_counts.get(c.source_type.value, 0) + 1
            entry = f"[{idx}] [{c.source_type.value.upper()}] {c.title} (Relevance: {c.score:.2f})\n{c.text}\n"
            if current_chars + len(entry) > max_context_chars and assembled_lines:
                break
            assembled_lines.append(entry)
            current_chars += len(entry)

        assembled_text = "\n".join(assembled_lines).strip()
        latency_ms = (time.time() - start_time) * 1000

        return RetrievalContextBundle(
            query=ret_query.query_text,
            candidates=candidates,
            assembled_text=assembled_text,
            source_counts=source_counts,
            retrieval_latency_ms=latency_ms,
            created_at=time.time(),
        )

    def evaluate_retrieval(
        self,
        test_queries: list[tuple[str, list[str]]],
        k: int = 5,
    ) -> RetrievalEvaluationMetrics:
        """Evaluate retrieval performance using Precision@K, Recall@K, and Mean Reciprocal Rank (MRR)."""
        precision_scores: list[float] = []
        recall_scores: list[float] = []
        reciprocal_ranks: list[float] = []

        for q_text, expected_relevant_ids in test_queries:
            q = RetrievalQuery(query_text=q_text, limit=k)
            candidates = self.retrieve_candidates(q)
            top_k_ids = [c.candidate_id for c in candidates[:k]]
            expected_set = set(expected_relevant_ids)

            # Precision@K
            if top_k_ids:
                hits = sum(1 for cid in top_k_ids if cid in expected_set)
                precision_scores.append(hits / len(top_k_ids))
            else:
                precision_scores.append(0.0)

            # Recall@K
            if expected_set:
                hits = sum(1 for cid in top_k_ids if cid in expected_set)
                recall_scores.append(hits / len(expected_set))
            else:
                recall_scores.append(1.0)

            # Reciprocal Rank
            rr = 0.0
            for rank_idx, cid in enumerate(top_k_ids, start=1):
                if cid in expected_set:
                    rr = 1.0 / rank_idx
                    break
            reciprocal_ranks.append(rr)

        avg_p = sum(precision_scores) / len(precision_scores) if precision_scores else 0.0
        avg_r = sum(recall_scores) / len(recall_scores) if recall_scores else 0.0
        avg_mrr = sum(reciprocal_ranks) / len(reciprocal_ranks) if reciprocal_ranks else 0.0

        return RetrievalEvaluationMetrics(
            precision_at_k=avg_p,
            recall_at_k=avg_r,
            mrr=avg_mrr,
            k=k,
        )
