"""M59 — Memory Learning Bridge.

Emits structured execution feedback and experience patterns to M56 Cognitive Memory.
Preserves strict provenance boundaries and prevents autonomous security policy modification.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from core.cognitive_memory.types import (
    CognitiveMemory,
    CognitiveMemoryType,
    ProvenanceType,
    scrub_sensitive_content,
)

if TYPE_CHECKING:
    from core.agent_mesh.types import AgentRun
    from core.repositories.base_cognitive_memory import BaseCognitiveMemoryRepository

logger = logging.getLogger("aura.agent_mesh.learning")


class MemoryLearningBridge:
    """Bridges verified agent run outcomes into M56 persistent cognitive memory."""

    def __init__(self, memory_repo: BaseCognitiveMemoryRepository | None = None):
        self.memory_repo = memory_repo

    def record_run_experience(
        self,
        run: AgentRun,
        explicit_user_feedback: str | None = None,
    ) -> CognitiveMemory | None:
        """Record completed run trace into episodic cognitive memory (Invariants M59-F40, M59-F41)."""
        if not self.memory_repo:
            return None

        # Provenance assignment: user-confirmed feedback receives USER_EXPLICIT; auto-trace receives TOOL_OBSERVED
        provenance = ProvenanceType.USER_EXPLICIT if explicit_user_feedback else ProvenanceType.TOOL_OBSERVED
        confidence = 1.0 if explicit_user_feedback else 0.85

        summary = (
            f"Agent Run {run.run_id}: Intent='{scrub_sensitive_content(run.intent)}', "
            f"Status='{run.status.value}', Iterations={run.iteration_count}, Tools={run.tool_call_count}"
        )
        if explicit_user_feedback:
            summary += f" | User Feedback: '{scrub_sensitive_content(explicit_user_feedback)}'"

        try:
            mem, _ = self.memory_repo.record_memory(
                tenant_id=run.tenant_id,
                content=summary,
                memory_type=CognitiveMemoryType.EPISODIC,
                category="agent_run_history",
                key=f"run_exp_{run.run_id}",
                structured_data={
                    "run_id": run.run_id,
                    "correlation_id": run.correlation_id,
                    "status": run.status.value,
                    "cost_estimate": run.cost_estimate,
                },
                confidence=confidence,
                provenance_type=provenance,
                tags=["agent_run", run.status.value],
            )
            return mem
        except Exception as e:
            logger.warning(f"Failed to record run experience in cognitive memory for '{run.run_id}': {e}")
            return None
