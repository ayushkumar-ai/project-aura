"""M56 — Cognitive Personalization Engine & Context Injector.

Ranks user preferences, traits, domain facts, and experience patterns based on composite
relevance, confidence, and recency, assembling sandboxed prompt blocks for LLM execution.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from core.cognitive_memory.lifecycle import MemoryLifecycleManager
from core.cognitive_memory.types import (
    CognitiveMemory,
    CognitiveMemoryType,
    ExperiencePattern,
    LifecycleState,
    PersonalizationContext,
    ProvenanceType,
    ScoredMemoryResult,
    UserCognitiveProfile,
)

logger = logging.getLogger("aura.cognitive_memory.personalization")


class PersonalizationEngine:
    """Ranks and injects tenant-specific cognitive context into agent execution pipelines."""

    def __init__(
        self,
        lifecycle_manager: MemoryLifecycleManager | None = None,
        weight_relevance: float = 0.5,
        weight_confidence: float = 0.3,
        weight_recency: float = 0.2,
    ):
        self.lifecycle_manager = lifecycle_manager or MemoryLifecycleManager()
        self.w_rel = weight_relevance
        self.w_conf = weight_confidence
        self.w_rec = weight_recency

    def score_memory(
        self,
        memory: CognitiveMemory,
        query: str = "",
        current_time: float | None = None,
    ) -> ScoredMemoryResult:
        """Compute composite relevance, confidence, and recency score for a memory (Invariant M56-F22)."""
        now = current_time if current_time is not None else time.time()

        # 1. Relevance Score based on token overlap
        rel_score = 0.5  # Base default relevance
        if query and query.strip():
            query_tokens = set(query.lower().replace(":", " ").replace("_", " ").split())
            if query_tokens:
                mem_text = f"{memory.key} {memory.content} {' '.join(memory.tags)}".lower()
                mem_tokens = set(mem_text.replace(":", " ").replace("_", " ").split())
                overlap = len(query_tokens.intersection(mem_tokens))
                rel_score = min(1.0, overlap / max(1, len(query_tokens)))

        # 2. Confidence Score with decay applied
        conf_score = self.lifecycle_manager.calculate_decayed_confidence(memory, current_time=now)

        # 3. Recency Score (hyperbolic decay over days)
        elapsed_days = max(0.0, (now - memory.last_accessed_at) / 86400.0)
        rec_score = 1.0 / (1.0 + elapsed_days)

        # Explicit user memories receive an authority boost
        if memory.provenance_type == ProvenanceType.USER_EXPLICIT:
            conf_score = 1.0

        composite_score = round(
            (self.w_rel * rel_score) + (self.w_conf * conf_score) + (self.w_rec * rec_score),
            4,
        )

        return ScoredMemoryResult(
            memory=memory,
            score=composite_score,
            relevance_score=rel_score,
            confidence_score=conf_score,
            recency_score=rec_score,
        )

    def rank_memories(
        self,
        memories: list[CognitiveMemory],
        query: str = "",
        limit: int = 10,
        current_time: float | None = None,
    ) -> list[ScoredMemoryResult]:
        """Rank active memories deterministically."""
        active_memories = self.lifecycle_manager.filter_active_for_retrieval(
            memories, current_time=current_time
        )
        scored = [
            self.score_memory(m, query=query, current_time=current_time)
            for m in active_memories
        ]
        # Sort by score descending, then by created_at descending for stability
        scored.sort(key=lambda s: (s.score, s.memory.created_at), reverse=True)
        return scored[:limit]

    def build_personalization_context(
        self,
        tenant_id: str,
        profile: UserCognitiveProfile | None = None,
        memories: list[CognitiveMemory] | None = None,
        experience_patterns: list[ExperiencePattern] | None = None,
        query: str = "",
        max_context_chars: int = 3000,
    ) -> PersonalizationContext:
        """Construct structured prompt block and context container (Invariant M56-F18)."""
        explicit_prefs: list[dict[str, Any]] = []
        inferred_traits: list[dict[str, Any]] = []
        relevant_facts: list[str] = []
        recommended_tools: list[str] = []

        # 1. Profile information
        if profile is not None:
            for k, v in sorted(profile.preferences.items()):
                explicit_prefs.append({"preference": k, "value": v})
            for k, v in sorted(profile.inferred_traits.items()):
                inferred_traits.append({"trait": k, "value": v})

        # 2. Memories ranking
        if memories:
            ranked = self.rank_memories(memories, query=query, limit=10)
            for item in ranked:
                mem = item.memory
                if mem.memory_type == CognitiveMemoryType.PREFERENCE:
                    explicit_prefs.append({
                        "key": mem.key or "pref",
                        "value": mem.content,
                        "confidence": mem.confidence,
                    })
                elif mem.memory_type in (CognitiveMemoryType.SEMANTIC, CognitiveMemoryType.EPISODIC):
                    fact_str = f"[{mem.category}] {mem.content}" if mem.category else mem.content
                    if fact_str not in relevant_facts:
                        relevant_facts.append(fact_str)

        # 3. Experience Patterns for optimal tools
        if experience_patterns:
            for pat in sorted(experience_patterns, key=lambda p: p.success_rate, reverse=True):
                for t in pat.optimal_tools:
                    if t and t not in recommended_tools:
                        recommended_tools.append(t)

        # 4. Assemble formatted prompt block
        lines = ["[USER PERSONALIZATION & COGNITIVE CONTEXT]"]
        if explicit_prefs:
            lines.append("• User Preferences:")
            for p in explicit_prefs[:5]:
                k = p.get("preference") or p.get("key", "")
                v = p.get("value", "")
                lines.append(f"  - {k}: {v}")

        if inferred_traits:
            lines.append("• Inferred Cognitive Traits:")
            for t in inferred_traits[:3]:
                lines.append(f"  - {t.get('trait')}: {t.get('value')}")

        if relevant_facts:
            lines.append("• Relevant Verified Knowledge:")
            for f in relevant_facts[:5]:
                lines.append(f"  - {f}")

        if recommended_tools:
            lines.append("• Recommended Experience-Optimized Tools:")
            lines.append(f"  - Tools: {', '.join(recommended_tools[:5])}")

        prompt_block = "\n".join(lines)
        if len(prompt_block) > max_context_chars:
            prompt_block = prompt_block[:max_context_chars] + "\n...[TRUNCATED]"

        return PersonalizationContext(
            tenant_id=tenant_id,
            explicit_preferences=explicit_prefs,
            inferred_traits=inferred_traits,
            relevant_facts=relevant_facts,
            recommended_tools=recommended_tools,
            formatted_prompt_block=prompt_block,
        )
