"""M40 — Integrated Personal Intelligence Types.

Defines schemas for autonomous cycle execution, lifecycle stages, trace records,
and unified intelligence responses.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class LifecycleStage(str, Enum):
    INGESTION = "ingestion"
    RETRIEVAL_RAG = "retrieval_rag"
    CONTEXT_ASSEMBLY = "context_assembly"
    PLANNING = "planning"
    POLICY_VERIFICATION = "policy_verification"
    ACTION_EXECUTION = "action_execution"
    EVALUATION = "evaluation"
    DISTILLATION = "distillation"
    STATE_SYNC = "state_sync"


@dataclass
class LifecycleTraceRecord:
    stage: LifecycleStage
    timestamp: float
    duration_seconds: float
    status: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["stage"] = self.stage.value
        return d


@dataclass
class UnifiedCycleResult:
    cycle_id: str
    user_prompt: str
    user_name: str
    response_content: str
    is_success: bool
    plan_id: str = ""
    steps_executed: int = 0
    tools_used: list[str] = field(default_factory=list)
    devices_acted_on: list[str] = field(default_factory=list)
    distilled_heuristic_id: str | None = None
    sync_delta_id: str | None = None
    total_duration_seconds: float = 0.0
    lifecycle_trace: list[LifecycleTraceRecord] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "user_prompt": self.user_prompt,
            "user_name": self.user_name,
            "response_content": self.response_content,
            "is_success": self.is_success,
            "plan_id": self.plan_id,
            "steps_executed": self.steps_executed,
            "tools_used": self.tools_used,
            "devices_acted_on": self.devices_acted_on,
            "distilled_heuristic_id": self.distilled_heuristic_id,
            "sync_delta_id": self.sync_delta_id,
            "total_duration_seconds": round(self.total_duration_seconds, 4),
            "lifecycle_trace": [t.to_dict() for t in self.lifecycle_trace],
            "metadata": self.metadata,
        }
