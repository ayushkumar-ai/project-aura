"""M56 — Cognitive Memory Contradiction Detection & Multi-Strategy Resolution Engine.

Implements automated conflict detection between active memories within the same tenant,
and resolves disputes using authoritative provenance hierarchies, user override infallibility,
and recency/confidence arbitration.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from core.cognitive_memory.types import (
    PROVENANCE_AUTHORITY,
    CognitiveMemory,
    ContradictionStatus,
    LifecycleState,
    MemoryContradiction,
    ProvenanceType,
    ResolutionStrategy,
)

logger = logging.getLogger("aura.cognitive_memory.contradiction")


class ContradictionDetector:
    """Detects factual, preference, and semantic contradictions across memory entries."""

    def detect_conflicts(
        self,
        candidate: CognitiveMemory,
        existing_memories: list[CognitiveMemory],
    ) -> list[tuple[CognitiveMemory, str]]:
        """Identify existing active memories that conflict with the incoming candidate memory."""
        conflicts: list[tuple[CognitiveMemory, str]] = []

        for existing in existing_memories:
            if existing.memory_id == candidate.memory_id:
                continue
            if existing.lifecycle_state != LifecycleState.ACTIVE:
                continue
            if existing.tenant_id != candidate.tenant_id:
                continue

            # 1. Exact Key Collision with different content/structured data
            if candidate.key and existing.key and candidate.key.lower() == existing.key.lower():
                if candidate.content.strip().lower() != existing.content.strip().lower():
                    conflicts.append((existing, f"Key collision on '{candidate.key}' with divergent content."))
                    continue
                if candidate.structured_data != existing.structured_data:
                    conflicts.append((existing, f"Key collision on '{candidate.key}' with divergent structured data."))
                    continue

            # 2. Semantic Subject-Predicate clash
            cand_subj = candidate.metadata.get("subject")
            cand_pred = candidate.metadata.get("predicate")
            exist_subj = existing.metadata.get("subject")
            exist_pred = existing.metadata.get("predicate")

            if (
                cand_subj and cand_pred and exist_subj and exist_pred
                and str(cand_subj).lower() == str(exist_subj).lower()
                and str(cand_pred).lower() == str(exist_pred).lower()
            ):
                cand_obj = candidate.metadata.get("object_value", candidate.content)
                exist_obj = existing.metadata.get("object_value", existing.content)
                if str(cand_obj).strip().lower() != str(exist_obj).strip().lower():
                    conflicts.append(
                        (existing, f"Subject-Predicate clash on '{cand_subj}:{cand_pred}'.")
                    )
                    continue

        return conflicts


class ContradictionResolver:
    """Resolves memory contradictions deterministically based on formal architectural invariants."""

    def resolve(
        self,
        existing: CognitiveMemory,
        candidate: CognitiveMemory,
    ) -> tuple[CognitiveMemory, CognitiveMemory, MemoryContradiction]:
        """Resolve a conflict between an existing active memory and a candidate memory.
        
        Returns: (retained_active_memory, superseded_memory, contradiction_record)
        """
        cand_auth = PROVENANCE_AUTHORITY.get(candidate.provenance_type, 1)
        exist_auth = PROVENANCE_AUTHORITY.get(existing.provenance_type, 1)

        # Invariant M56-F05: User Explicit Override Authority
        if candidate.provenance_type == ProvenanceType.USER_EXPLICIT and existing.provenance_type != ProvenanceType.USER_EXPLICIT:
            strategy = ResolutionStrategy.USER_OVERRIDE
            candidate_wins = True
            reason = "Explicit user statement unconditionally supersedes inferred or system memory."
        elif existing.provenance_type == ProvenanceType.USER_EXPLICIT and candidate.provenance_type != ProvenanceType.USER_EXPLICIT:
            strategy = ResolutionStrategy.USER_OVERRIDE
            candidate_wins = False
            reason = "Existing explicit user statement takes precedence over incoming non-explicit memory."
        # Invariant M56-F04: Provenance Precedence Ordering
        elif cand_auth > exist_auth:
            strategy = ResolutionStrategy.PROVENANCE_PRECEDENCE
            candidate_wins = True
            reason = f"Candidate provenance ({candidate.provenance_type.value}) outranks existing ({existing.provenance_type.value})."
        elif exist_auth > cand_auth:
            strategy = ResolutionStrategy.PROVENANCE_PRECEDENCE
            candidate_wins = False
            reason = f"Existing provenance ({existing.provenance_type.value}) outranks candidate ({candidate.provenance_type.value})."
        # Equal Provenance: Confidence & Recency Arbitration (Invariant M56-F06)
        else:
            if candidate.confidence > existing.confidence:
                strategy = ResolutionStrategy.CONFIDENCE_THRESHOLD
                candidate_wins = True
                reason = f"Candidate higher confidence ({candidate.confidence} > {existing.confidence})."
            elif candidate.confidence < existing.confidence:
                strategy = ResolutionStrategy.CONFIDENCE_THRESHOLD
                candidate_wins = False
                reason = f"Existing higher confidence ({existing.confidence} > {candidate.confidence})."
            else:
                # Same confidence -> Newer candidate wins
                strategy = ResolutionStrategy.RECENCY
                candidate_wins = True
                reason = "Candidate recency arbitration preferred for matching provenance and confidence."

        now = time.time()

        if candidate_wins:
            # Existing memory is superseded (Invariant M56-F06)
            superseded_existing_dict = existing.to_dict()
            superseded_existing_dict["lifecycle_state"] = LifecycleState.SUPERSEDED.value
            superseded_existing_dict["updated_at"] = now
            superseded_existing = CognitiveMemory.from_dict(superseded_existing_dict)

            # Candidate memory becomes active with incremented version
            active_candidate_dict = candidate.to_dict()
            active_candidate_dict["lifecycle_state"] = LifecycleState.ACTIVE.value
            active_candidate_dict["supersedes_id"] = existing.memory_id
            active_candidate_dict["version"] = existing.version + 1
            active_candidate_dict["updated_at"] = now
            active_candidate = CognitiveMemory.from_dict(active_candidate_dict)

            contradiction = MemoryContradiction(
                tenant_id=candidate.tenant_id,
                memory_a_id=existing.memory_id,
                memory_b_id=candidate.memory_id,
                contradiction_type="semantic_clash",
                resolution_status=ContradictionStatus.AUTO_RESOLVED,
                resolution_strategy=strategy,
                resolved_by="system:contradiction_resolver",
                resolution_details={"winner": candidate.memory_id, "loser": existing.memory_id, "rationale": reason},
                detected_at=now,
                resolved_at=now,
            )
            return active_candidate, superseded_existing, contradiction

        else:
            # Candidate memory is superseded / rejected
            superseded_candidate_dict = candidate.to_dict()
            superseded_candidate_dict["lifecycle_state"] = LifecycleState.SUPERSEDED.value
            superseded_candidate_dict["updated_at"] = now
            superseded_candidate = CognitiveMemory.from_dict(superseded_candidate_dict)

            retained_existing = existing

            contradiction = MemoryContradiction(
                tenant_id=existing.tenant_id,
                memory_a_id=existing.memory_id,
                memory_b_id=candidate.memory_id,
                contradiction_type="semantic_clash",
                resolution_status=ContradictionStatus.AUTO_RESOLVED,
                resolution_strategy=strategy,
                resolved_by="system:contradiction_resolver",
                resolution_details={"winner": existing.memory_id, "loser": candidate.memory_id, "rationale": reason},
                detected_at=now,
                resolved_at=now,
            )
            return retained_existing, superseded_candidate, contradiction
