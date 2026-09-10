"""Experience Consolidation and Grounded Knowledge Distillation (M14).

Distills episodic execution traces and research findings into durable,
semantic memory facts, resolves contradictions with confidence-weighted
belief revision, and enforces strict non-authorizing metadata sanitization.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from app.config import settings
from core.memory_types import (
    EpisodicRecord,
    MemoryEntry,
    MemoryNamespace,
    MemoryTier,
    SemanticFact,
)
from core.provenance import (
    TaintedValue,
    is_tainted,
    unwrap_tainted,
    wrap_tainted,
)
from core.reflection_types import (
    ConsolidationRecord,
    ConsolidationSourceType,
    ContradictionRecord,
    DistillationResult,
    ReflectionRule,
    ResolutionStrategy,
    strip_forbidden_metadata_keys,
)

logger = logging.getLogger("aura.memory_consolidation")


class MemoryConsolidator:
    """Consolidates episodic experience and research reports into structured semantic memory."""

    def __init__(
        self,
        memory_store: Any | None = None,
        memory_manager: Any | None = None,
        belief_revision_threshold: float | None = None,
        max_consolidation_batch: int | None = None,
        max_distilled_facts_per_run: int | None = None,
        max_research_ingested_claims: int | None = None,
        timeout_seconds: float | None = None,
    ):
        self.memory_store = memory_store
        self.memory_manager = memory_manager
        if self.memory_store is None and self.memory_manager is not None:
            self.memory_store = getattr(self.memory_manager, "store", None)

        self.belief_revision_threshold = (
            belief_revision_threshold
            if belief_revision_threshold is not None
            else getattr(settings, "aura_belief_revision_threshold", 0.85)
        )
        self.max_consolidation_batch = (
            max_consolidation_batch
            if max_consolidation_batch is not None
            else getattr(settings, "aura_max_consolidation_batch", 10)
        )
        self.max_distilled_facts_per_run = (
            max_distilled_facts_per_run
            if max_distilled_facts_per_run is not None
            else getattr(settings, "aura_max_distilled_facts_per_run", 5)
        )
        self.max_research_ingested_claims = (
            max_research_ingested_claims
            if max_research_ingested_claims is not None
            else getattr(settings, "aura_max_research_ingested_claims", 10)
        )
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else getattr(settings, "aura_consolidation_timeout_seconds", 15.0)
        )

    def resolve_contradiction(
        self,
        existing_fact: SemanticFact,
        incoming_fact: SemanticFact,
    ) -> tuple[ResolutionStrategy, Any, str]:
        """Resolve contradiction between existing belief and incoming fact using confidence-weighted revision."""
        ex_val = existing_fact.object_value
        in_val = incoming_fact.object_value

        # Identical values -> no real contradiction
        if ex_val == in_val:
            return ResolutionStrategy.PRESERVE_EXISTING_CONFIDENT, ex_val, "Values are identical."

        # Check if both are dictionaries and can be merged
        if isinstance(ex_val, dict) and isinstance(in_val, dict):
            merged = dict(ex_val)
            merged.update(in_val)
            return ResolutionStrategy.MERGE_ATTRIBUTES, merged, "Merged attributes from existing and incoming facts."

        # Confidence delta comparison
        conf_delta = incoming_fact.confidence - existing_fact.confidence
        if incoming_fact.confidence >= self.belief_revision_threshold and conf_delta >= -0.05:
            return (
                ResolutionStrategy.REPLACE_NEWER_CONFIDENT,
                in_val,
                f"Incoming fact confidence ({incoming_fact.confidence:.2f}) meets belief revision threshold ({self.belief_revision_threshold:.2f}).",
            )
        elif incoming_fact.confidence > existing_fact.confidence:
            return (
                ResolutionStrategy.REPLACE_NEWER_CONFIDENT,
                in_val,
                f"Incoming fact confidence ({incoming_fact.confidence:.2f}) exceeds existing confidence ({existing_fact.confidence:.2f}).",
            )
        else:
            return (
                ResolutionStrategy.PRESERVE_EXISTING_CONFIDENT,
                ex_val,
                f"Preserved existing fact confidence ({existing_fact.confidence:.2f}) over incoming ({incoming_fact.confidence:.2f}).",
            )

    def consolidate_fact(
        self,
        fact: SemanticFact,
        namespace: str = MemoryNamespace.DOMAIN_KNOWLEDGE.value,
    ) -> tuple[SemanticFact, ContradictionRecord | None]:
        """Consolidate a single SemanticFact, checking against memory store for contradictions."""
        if self.memory_store is None:
            return fact, None

        key = fact.key
        existing_entry: MemoryEntry | None = None
        try:
            if hasattr(self.memory_store, "get_by_key"):
                existing_entry = self.memory_store.get_by_key(
                    tier=MemoryTier.SEMANTIC,
                    namespace=namespace,
                    key=key,
                )
            elif hasattr(self.memory_store, "get_semantic_fact"):
                existing_entry = self.memory_store.get_semantic_fact(key, namespace=namespace)
        except Exception as ex:
            logger.warning("Error fetching existing fact '%s' for consolidation: %s", key, ex)

        if existing_entry is None or existing_entry.value is None:
            # New fact, write to store
            try:
                if hasattr(self.memory_store, "store"):
                    self.memory_store.store(fact.to_memory_entry(namespace=namespace))
                elif hasattr(self.memory_store, "put"):
                    self.memory_store.put(fact.to_memory_entry(namespace=namespace))
            except Exception as ex:
                logger.warning("Error storing fact '%s': %s", key, ex)
            return fact, None

        # Existing fact found, check for contradiction
        existing_fact = SemanticFact.from_memory_entry(existing_entry)
        if existing_fact.object_value == fact.object_value:
            return existing_fact, None

        strategy, chosen_value, rationale = self.resolve_contradiction(existing_fact, fact)

        contradiction = ContradictionRecord(
            subject=fact.subject,
            existing_value=existing_fact.object_value,
            existing_confidence=existing_fact.confidence,
            new_value=fact.object_value,
            new_confidence=fact.confidence,
            resolution=strategy,
            chosen_value=chosen_value,
            rationale=rationale,
            timestamp=time.time(),
        )

        chosen_conf = fact.confidence if strategy == ResolutionStrategy.REPLACE_NEWER_CONFIDENT else existing_fact.confidence
        is_untrusted = existing_fact.is_untrusted or fact.is_untrusted
        source_urls = tuple(sorted(set(existing_fact.source_urls + fact.source_urls)))

        resolved_fact = SemanticFact(
            subject=fact.subject,
            predicate=fact.predicate,
            object_value=chosen_value,
            confidence=chosen_conf,
            is_untrusted=is_untrusted,
            source_urls=source_urls,
            metadata=strip_forbidden_metadata_keys(fact.metadata),
            id=existing_entry.entry_id,
        )

        try:
            if hasattr(self.memory_store, "store"):
                self.memory_store.store(resolved_fact.to_memory_entry(namespace=namespace))
            elif hasattr(self.memory_store, "put"):
                self.memory_store.put(resolved_fact.to_memory_entry(namespace=namespace))
        except Exception as ex:
            logger.warning("Error storing resolved fact '%s': %s", key, ex)

        return resolved_fact, contradiction

    def consolidate_episodes(
        self,
        episodes: list[EpisodicRecord] | list[Any],
    ) -> ConsolidationRecord:
        """Analyze a batch of episodic records, extract skill reliability patterns, and consolidate facts."""
        batch = episodes[: self.max_consolidation_batch]
        if not batch:
            return ConsolidationRecord(
                consolidation_id=f"cons-{uuid.uuid4().hex[:12]}",
                source_type=ConsolidationSourceType.EPISODIC_RUNS,
                episodes_analyzed=0,
                facts_created=0,
                facts_updated=0,
                contradictions_resolved=[],
                heuristics_extracted=[],
                timestamp=time.time(),
            )

        # Aggregate skill performance metrics
        skill_stats: dict[str, dict[str, Any]] = {}
        for ep in batch:
            skills = getattr(ep, "executed_skills", ())
            success = bool(getattr(ep, "success", True))
            for sk in skills:
                if sk not in skill_stats:
                    skill_stats[sk] = {"successes": 0, "total": 0}
                skill_stats[sk]["total"] += 1
                if success:
                    skill_stats[sk]["successes"] += 1

        facts_created = 0
        facts_updated = 0
        contradictions: list[ContradictionRecord] = []
        heuristics: list[str] = []

        # Generate reliability facts
        for skill_name, stats in list(skill_stats.items())[: self.max_distilled_facts_per_run]:
            rate = stats["successes"] / max(1, stats["total"])
            fact = SemanticFact(
                subject="skill_reliability",
                predicate=skill_name,
                object_value={"success_rate": round(rate, 2), "sample_size": stats["total"]},
                confidence=min(1.0, 0.5 + (stats["total"] * 0.1)),
                is_untrusted=False,
                metadata={"distilled_from": "episodic_runs"},
            )
            _, contra = self.consolidate_fact(fact, namespace=MemoryNamespace.SYSTEM_FACTS.value)
            if contra is not None:
                contradictions.append(contra)
                facts_updated += 1
            else:
                facts_created += 1

            if rate >= 0.8 and stats["total"] >= 2:
                heuristics.append(f"Skill '{skill_name}' has demonstrated high reliability ({rate * 100:.0f}% success rate).")
            elif rate < 0.5 and stats["total"] >= 2:
                heuristics.append(f"Skill '{skill_name}' has low reliability ({rate * 100:.0f}% success rate); verify preconditions.")

        return ConsolidationRecord(
            consolidation_id=f"cons-{uuid.uuid4().hex[:12]}",
            source_type=ConsolidationSourceType.EPISODIC_RUNS,
            episodes_analyzed=len(batch),
            facts_created=facts_created,
            facts_updated=facts_updated,
            contradictions_resolved=contradictions,
            heuristics_extracted=heuristics,
            timestamp=time.time(),
        )

    def distill_research_report(
        self,
        report: Any,
        query: str = "",
    ) -> DistillationResult:
        """Distill grounded factual assertions from a completed ResearchReport into semantic memory."""
        claims_to_ingest: list[Any] = []
        source_id = getattr(report, "report_id", getattr(report, "query", f"rep-{uuid.uuid4().hex[:8]}"))

        # Look for verified claims on report or assembled_answer
        verified_claims = getattr(report, "verified_claims", None)
        if not verified_claims and hasattr(report, "assembled_answer"):
            verified_claims = getattr(report.assembled_answer, "verified_claims", None)

        if verified_claims:
            for vc in verified_claims:
                status = getattr(vc, "verification_status", "")
                status_str = status.value if hasattr(status, "value") else str(status)
                conf = float(getattr(vc, "confidence_score", 0.0))
                if status_str in ("supported", "partially_supported") and conf >= 0.6:
                    claims_to_ingest.append(vc)

        distilled_facts: list[SemanticFact] = []
        heuristics: list[str] = []

        for vc in claims_to_ingest[: self.max_research_ingested_claims]:
            statement = getattr(vc, "statement", "")
            claim_id = getattr(vc, "claim_id", f"claim-{uuid.uuid4().hex[:6]}")
            conf = float(getattr(vc, "confidence_score", 0.8))

            # Extract source URLs from supporting evidence
            urls: list[str] = []
            ev_list = getattr(vc, "supporting_evidence", ())
            for ev in ev_list:
                url = getattr(ev, "source_url", None)
                if url:
                    urls.append(url)

            # Web findings are external web provenance
            fact = SemanticFact(
                subject=query.strip() or "research_knowledge",
                predicate=claim_id,
                object_value=wrap_tainted(statement, is_untrusted=True, source_urls=tuple(urls)),
                confidence=conf,
                is_untrusted=True,
                source_urls=tuple(urls),
                metadata={"source_report_id": source_id, "ingested_at": time.time()},
            )

            resolved_fact, _ = self.consolidate_fact(fact, namespace=MemoryNamespace.DOMAIN_KNOWLEDGE.value)
            distilled_facts.append(resolved_fact)
            heuristics.append(f"Verified research claim [{claim_id}]: {statement[:120]}")

        summary = f"Distilled {len(distilled_facts)} verified claims from research report '{source_id}'."

        return DistillationResult(
            source_id=source_id,
            source_type=ConsolidationSourceType.RESEARCH_REPORT,
            facts_distilled=distilled_facts,
            heuristics=heuristics,
            rules=[],
            summary=summary,
            metadata={"query": query},
        )
