"""M56 — Continuous Learning Feedback Loop.

Processes user feedback (positive, negative, correction, override) to adjust confidence
scores, supersede stale memories, and trigger experience adaptation without model fine-tuning.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import uuid4

from core.cognitive_memory.lifecycle import MemoryLifecycleManager
from core.cognitive_memory.types import (
    CognitiveMemory,
    CognitiveMemoryType,
    FeedbackType,
    LifecycleState,
    MemoryFeedbackEvent,
    ProvenanceType,
)

logger = logging.getLogger("aura.cognitive_memory.feedback")


class FeedbackLearningLoop:
    """Manages closed-loop continuous learning and memory adaptation from explicit feedback."""

    def __init__(self, lifecycle_manager: MemoryLifecycleManager | None = None):
        self.lifecycle_manager = lifecycle_manager or MemoryLifecycleManager()

    def process_feedback(
        self,
        event: MemoryFeedbackEvent,
        target_memory: CognitiveMemory | None = None,
    ) -> tuple[CognitiveMemory | None, CognitiveMemory | None, MemoryFeedbackEvent]:
        """Apply user feedback event to memory state (Invariant M56-F13).
        
        Returns: (updated_target_memory, newly_created_memory, updated_event)
        """
        if event.applied:
            # Idempotent return if already applied
            return target_memory, None, event

        now = time.time()
        updated_target = target_memory
        new_memory: CognitiveMemory | None = None

        if target_memory is not None:
            if event.feedback_type == FeedbackType.POSITIVE:
                # Boost confidence
                new_conf = min(1.0, round(target_memory.confidence + 0.1, 3))
                d = target_memory.to_dict()
                d["confidence"] = new_conf
                d["updated_at"] = now
                d["last_accessed_at"] = now
                updated_target = CognitiveMemory.from_dict(d)

            elif event.feedback_type == FeedbackType.NEGATIVE:
                # Penalize confidence
                new_conf = max(0.0, round(target_memory.confidence - 0.2, 3))
                d = target_memory.to_dict()
                d["confidence"] = new_conf
                d["updated_at"] = now
                if new_conf < self.lifecycle_manager.config.stale_confidence_threshold:
                    d["lifecycle_state"] = LifecycleState.STALE.value
                updated_target = CognitiveMemory.from_dict(d)

            elif event.feedback_type in (FeedbackType.CORRECTION, FeedbackType.OVERRIDE):
                # Supersede existing memory and create new explicit memory
                d_old = target_memory.to_dict()
                d_old["lifecycle_state"] = LifecycleState.SUPERSEDED.value
                d_old["updated_at"] = now
                updated_target = CognitiveMemory.from_dict(d_old)

                new_content = event.correction_content or target_memory.content
                new_memory = CognitiveMemory(
                    memory_id=str(uuid4()),
                    tenant_id=target_memory.tenant_id,
                    memory_type=target_memory.memory_type,
                    category=target_memory.category,
                    key=target_memory.key,
                    content=new_content,
                    structured_data=dict(target_memory.structured_data),
                    confidence=1.0,
                    provenance_type=ProvenanceType.USER_EXPLICIT,
                    lifecycle_state=LifecycleState.ACTIVE,
                    version=target_memory.version + 1,
                    supersedes_id=target_memory.memory_id,
                    tags=target_memory.tags,
                    created_at=now,
                    updated_at=now,
                )

        # Mark event applied
        event_dict = event.to_dict()
        event_dict["applied"] = True
        applied_event = MemoryFeedbackEvent.from_dict(event_dict)

        return updated_target, new_memory, applied_event
