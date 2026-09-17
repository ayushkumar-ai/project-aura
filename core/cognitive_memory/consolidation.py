"""M56 — Cognitive Memory Consolidation Engine.

Distills episodic execution traces and task replays into durable semantic rules
and aggregated experience patterns for tool selection and failure avoidance.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import uuid4

from core.cognitive_memory.types import (
    CognitiveMemory,
    CognitiveMemoryType,
    ExperiencePattern,
    LifecycleState,
    ProvenanceType,
)

logger = logging.getLogger("aura.cognitive_memory.consolidation")


class MemoryConsolidationEngine:
    """Distills episodic memories into durable knowledge and aggregated experience patterns."""

    def consolidate_episode_to_pattern(
        self,
        tenant_id: str,
        context_key: str,
        tools_used: list[str],
        success: bool,
        latency_ms: float = 0.0,
        error_mode: str | None = None,
        existing_pattern: ExperiencePattern | None = None,
    ) -> ExperiencePattern:
        """Aggregate a discrete episodic execution into an updated ExperiencePattern (Invariant M56-F14)."""
        clean_key = str(context_key or "general").strip().lower()
        now = time.time()

        if existing_pattern is None:
            succ = 1 if success else 0
            fail = 0 if success else 1
            optimal = [t for t in tools_used if t] if success else []
            failures = [error_mode] if (error_mode and not success) else []
            recs = [f"Preferred tools for {clean_key}: {', '.join(optimal)}"] if optimal else []

            return ExperiencePattern(
                pattern_id=str(uuid4()),
                tenant_id=tenant_id,
                context_key=clean_key,
                success_count=succ,
                failure_count=fail,
                average_latency_ms=max(0.0, latency_ms),
                optimal_tools=optimal,
                failure_modes=failures,
                recommendations=recs,
                confidence=0.8 if success else 0.5,
                created_at=now,
                updated_at=now,
            )

        # Update existing pattern
        prev_total = existing_pattern.total_attempts
        new_succ = existing_pattern.success_count + (1 if success else 0)
        new_fail = existing_pattern.failure_count + (0 if success else 1)
        new_total = new_succ + new_fail

        # Moving average latency
        if new_total > 0:
            new_latency = (
                (existing_pattern.average_latency_ms * prev_total) + max(0.0, latency_ms)
            ) / new_total
        else:
            new_latency = max(0.0, latency_ms)

        # Update optimal tools
        updated_tools = list(existing_pattern.optimal_tools)
        if success:
            for t in tools_used:
                if t and t not in updated_tools:
                    updated_tools.append(t)

        # Update failure modes
        updated_failures = list(existing_pattern.failure_modes)
        if not success and error_mode and error_mode not in updated_failures:
            updated_failures.append(error_mode)

        # Recommendations update
        recs = list(existing_pattern.recommendations)
        if updated_tools and not recs:
            recs.append(f"Recommended tools for {clean_key}: {', '.join(updated_tools)}")

        success_ratio = new_succ / max(1, new_total)
        updated_conf = min(1.0, max(0.2, round(0.5 + (0.5 * success_ratio), 3)))

        return ExperiencePattern(
            pattern_id=existing_pattern.pattern_id,
            tenant_id=tenant_id,
            context_key=clean_key,
            success_count=new_succ,
            failure_count=new_fail,
            average_latency_ms=round(new_latency, 2),
            optimal_tools=updated_tools,
            failure_modes=updated_failures,
            recommendations=recs,
            confidence=updated_conf,
            created_at=existing_pattern.created_at,
            updated_at=now,
        )

    def extract_semantic_facts_from_episodes(
        self,
        tenant_id: str,
        episodes: list[CognitiveMemory],
    ) -> list[CognitiveMemory]:
        """Synthesize repeated successful episodic execution insights into durable semantic facts."""
        synthesized: list[CognitiveMemory] = []
        tool_counts: dict[str, int] = {}

        for ep in episodes:
            if ep.lifecycle_state != LifecycleState.ACTIVE:
                continue
            if ep.memory_type != CognitiveMemoryType.EPISODIC:
                continue

            skills = ep.structured_data.get("executed_skills", [])
            for s in skills:
                tool_counts[s] = tool_counts.get(s, 0) + 1

        for tool, count in tool_counts.items():
            if count >= 3:  # Pattern threshold
                fact = CognitiveMemory(
                    memory_id=str(uuid4()),
                    tenant_id=tenant_id,
                    memory_type=CognitiveMemoryType.SEMANTIC,
                    category="tool_competency",
                    key=f"proven_tool:{tool}",
                    content=f"Agent has high competency and frequent success using tool '{tool}'.",
                    structured_data={"tool": tool, "success_observations": count},
                    confidence=0.85,
                    provenance_type=ProvenanceType.SYSTEM_DERIVED,
                    lifecycle_state=LifecycleState.ACTIVE,
                    tags=("consolidation", "tool_competency"),
                )
                synthesized.append(fact)

        return synthesized
