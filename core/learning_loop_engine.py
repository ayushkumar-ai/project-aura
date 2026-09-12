"""M36 — Experience & Learning Loop Engine for Project AURA.

Distills behavioral outcomes into explicit heuristics, adjusting pattern confidence scores
incrementally without self-modifying code.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any
from uuid import uuid4

from core.learning_loop_types import (
    DistilledHeuristic,
    InteractionOutcome,
    LearningLoopEvaluationReport,
    PatternConfidenceStatus,
)

logger = logging.getLogger("aura.learning_loop")


class ExperienceLearningEngine:
    """Explicit, measurable experience distillation and reinforcement engine."""

    def __init__(
        self,
        durable_state_store: Any | None = None,
        epistemic_graph: Any | None = None,
    ):
        self.durable_state_store = durable_state_store
        self.epistemic_graph = epistemic_graph
        self._interactions: list[InteractionOutcome] = []
        self._heuristics: dict[str, DistilledHeuristic] = {}  # keyed by task_pattern
        self._lock = threading.RLock()

    def record_interaction(self, outcome: InteractionOutcome) -> DistilledHeuristic:
        """Record an execution outcome and update or distill learned heuristics."""
        with self._lock:
            self._interactions.append(outcome)
            pattern_key = outcome.task_pattern.strip().lower()

            heuristic = self._heuristics.get(pattern_key)
            now = time.time()

            if heuristic is None:
                # Initialize new candidate heuristic
                init_conf = 0.6 if outcome.success else 0.3
                heuristic = DistilledHeuristic(
                    heuristic_id=f"heur_{uuid4().hex[:12]}",
                    task_pattern=pattern_key,
                    recommended_strategy=f"Execute sequence: {', '.join(outcome.tool_sequence)}" if outcome.tool_sequence else "Standard workflow",
                    recommended_tools=list(outcome.tool_sequence),
                    confidence=init_conf,
                    support_count=1 if outcome.success else 0,
                    failure_count=0 if outcome.success else 1,
                    status=PatternConfidenceStatus.CANDIDATE,
                    created_at=now,
                    updated_at=now,
                    provenance={"first_interaction_id": outcome.interaction_id},
                )
            else:
                heuristic.updated_at = now
                if outcome.success:
                    heuristic.support_count += 1
                    # Factor in user feedback if present
                    feedback_boost = (outcome.user_feedback_score * 0.1) if outcome.user_feedback_score is not None else 0.0
                    heuristic.confidence = min(1.0, round(heuristic.confidence + 0.15 + feedback_boost, 4))

                    # Transition candidate to proven
                    if heuristic.support_count >= 3 and heuristic.confidence >= 0.7:
                        heuristic.status = PatternConfidenceStatus.PROVEN
                    elif heuristic.status == PatternConfidenceStatus.WEAKENED and heuristic.confidence >= 0.6:
                        heuristic.status = PatternConfidenceStatus.CANDIDATE

                    # Update recommended tools if sequence proved successful
                    if outcome.tool_sequence and not heuristic.recommended_tools:
                        heuristic.recommended_tools = list(outcome.tool_sequence)
                else:
                    heuristic.failure_count += 1
                    heuristic.confidence = max(0.0, round(heuristic.confidence - 0.25, 4))
                    if heuristic.confidence < 0.3:
                        heuristic.status = PatternConfidenceStatus.DEPRECATED
                    else:
                        heuristic.status = PatternConfidenceStatus.WEAKENED

            self._heuristics[pattern_key] = heuristic

            # Mirror to durable state store if available
            if self.durable_state_store and hasattr(self.durable_state_store, "record_experience"):
                try:
                    self.durable_state_store.record_experience(
                        task_description=outcome.input_prompt,
                        plan_summary=heuristic.recommended_strategy,
                        action_sequence=outcome.tool_sequence,
                        outcome="success" if outcome.success else "failure",
                        reward_score=heuristic.confidence,
                        lessons_learned=[f"Confidence: {heuristic.confidence:.2f} ({heuristic.status.value})"],
                    )
                except Exception as e:
                    logger.warning(f"Failed to mirror experience to durable state: {e}")

            return heuristic

    def query_heuristics(
        self,
        task_pattern: str = "",
        min_confidence: float = 0.5,
        only_proven: bool = False,
    ) -> list[DistilledHeuristic]:
        """Query distilled heuristics matching a task pattern."""
        with self._lock:
            q_lower = task_pattern.strip().lower()
            results: list[DistilledHeuristic] = []

            for h in self._heuristics.values():
                if only_proven and h.status != PatternConfidenceStatus.PROVEN:
                    continue
                if h.confidence < min_confidence:
                    continue
                if q_lower and q_lower not in h.task_pattern:
                    continue
                results.append(h)

            results.sort(key=lambda h: h.confidence, reverse=True)
            return results

    def generate_report(self) -> LearningLoopEvaluationReport:
        """Generate a quantitative evaluation report of system learning and adaptation."""
        with self._lock:
            total_ints = len(self._interactions)
            success_count = sum(1 for i in self._interactions if i.success)
            success_rate = (success_count / total_ints) if total_ints else 1.0

            total_h = len(self._heuristics)
            proven_count = sum(1 for h in self._heuristics.values() if h.status == PatternConfidenceStatus.PROVEN)
            dep_count = sum(1 for h in self._heuristics.values() if h.status == PatternConfidenceStatus.DEPRECATED)
            avg_conf = (
                sum(h.confidence for h in self._heuristics.values()) / total_h
                if total_h
                else 0.0
            )

            return LearningLoopEvaluationReport(
                total_interactions=total_ints,
                success_rate=round(success_rate, 4),
                total_heuristics=total_h,
                proven_heuristics_count=proven_count,
                deprecated_heuristics_count=dep_count,
                average_confidence=round(avg_conf, 4),
                timestamp=time.time(),
            )
