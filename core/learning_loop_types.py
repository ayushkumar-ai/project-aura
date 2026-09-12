"""M36 — Experience & Learning Loop Types.

Defines schemas for interaction outcomes, distilled heuristics, confidence lifecycles,
and learning evaluation reports.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class PatternConfidenceStatus(str, Enum):
    CANDIDATE = "candidate"
    PROVEN = "proven"
    WEAKENED = "weakened"
    SUPERSEDED = "superseded"
    DEPRECATED = "deprecated"


@dataclass
class InteractionOutcome:
    interaction_id: str
    task_pattern: str
    input_prompt: str
    plan_id: str = ""
    tool_sequence: list[str] = field(default_factory=list)
    success: bool = True
    latency_seconds: float = 0.0
    user_feedback_score: float | None = None  # -1.0 (thumbs down) to +1.0 (thumbs up)
    error_details: str = ""
    timestamp: float = field(default_factory=time.time)
    provenance: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DistilledHeuristic:
    heuristic_id: str
    task_pattern: str
    recommended_strategy: str
    recommended_tools: list[str] = field(default_factory=list)
    confidence: float = 0.5
    support_count: int = 1
    failure_count: int = 0
    status: PatternConfidenceStatus = PatternConfidenceStatus.CANDIDATE
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    provenance: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DistilledHeuristic:
        d = dict(data)
        if isinstance(d.get("status"), str):
            d["status"] = PatternConfidenceStatus(d["status"])
        valid_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in d.items() if k in valid_fields}
        return cls(**filtered)


@dataclass
class LearningLoopEvaluationReport:
    total_interactions: int
    success_rate: float
    total_heuristics: int
    proven_heuristics_count: int
    deprecated_heuristics_count: int
    average_confidence: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
