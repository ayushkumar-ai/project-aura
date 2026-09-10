"""Goal Strategy, Meta-Policy, and Failure Recovery Data Models (M16).

Defines structured, immutable, and validated contracts for strategy taxonomy,
strategy attempt tracking, goal strategy history, and goal stagnation monitoring.
Strictly strips authorization metadata and preserves provenance / TaintedValue boundaries.
"""

from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from core.provenance import TaintedValue, is_tainted, unwrap_tainted, wrap_tainted

FORBIDDEN_METADATA_KEYS = frozenset({
    "approved",
    "approval_status",
    "is_approved",
    "auto_approve",
    "permission",
    "authorized",
    "bypass_policy",
    "role_override",
    "system_override",
})


def strip_forbidden_metadata_keys(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Recursively strip any authorization, permission, or approval override keys."""
    if meta is None:
        return {}
    if not isinstance(meta, dict):
        return {}

    cleaned: dict[str, Any] = {}
    for k, v in meta.items():
        k_str = str(k)
        if k_str.lower() in FORBIDDEN_METADATA_KEYS:
            continue
        if callable(v):
            continue
        if isinstance(v, dict):
            cleaned[k_str] = strip_forbidden_metadata_keys(v)
        elif isinstance(v, (list, tuple)):
            cleaned[k_str] = [
                strip_forbidden_metadata_keys(item) if isinstance(item, dict) else (str(item) if not callable(item) else "")
                for item in v
            ]
        elif isinstance(v, (str, int, float, bool)) or v is None or isinstance(v, TaintedValue):
            cleaned[k_str] = v
        else:
            cleaned[k_str] = repr(v)
    return cleaned


def _canonical_value(val: Any) -> Any:
    """Convert values into deterministic JSON-serializable primitives, preserving TaintedValue."""
    if isinstance(val, TaintedValue):
        return {
            "__tainted__": True,
            "raw_value": _canonical_value(val.raw_value),
            "is_untrusted": val.is_untrusted,
            "source_type": val.source_type,
            "originating_step_id": val.originating_step_id,
            "source_urls": list(val.source_urls),
            "metadata": {str(k): _canonical_value(v) for k, v in sorted(val.metadata.items())},
        }
    elif isinstance(val, (str, int, float, bool)) or val is None:
        return val
    elif isinstance(val, (list, tuple, set, frozenset)):
        return [_canonical_value(x) for x in val]
    elif isinstance(val, dict):
        return {str(k): _canonical_value(v) for k, v in sorted(val.items())}
    elif isinstance(val, Enum):
        return val.value
    elif callable(val):
        raise ValueError("Cannot serialize callable value in strategy data.")
    else:
        return repr(val)


def _restore_value(val: Any) -> Any:
    """Restore values from serialized JSON primitives, restoring TaintedValue instances."""
    if isinstance(val, dict):
        if val.get("__tainted__") is True and "raw_value" in val:
            return wrap_tainted(
                value=_restore_value(val.get("raw_value")),
                is_untrusted=bool(val.get("is_untrusted", True)),
                source_type=str(val.get("source_type", "external_web")),
                originating_step_id=val.get("originating_step_id"),
                source_urls=val.get("source_urls", ()),
                metadata=dict(val.get("metadata", {})),
            )
        return {k: _restore_value(v) for k, v in val.items()}
    elif isinstance(val, list):
        return [_restore_value(x) for x in val]
    return val


class StrategyType(str, Enum):
    """Execution strategy modalities supported by AURA MetaPolicyEngine."""

    DIRECT_SKILL = "direct_skill"
    DECOMPOSED_HIERARCHICAL = "decomposed_hierarchical"
    RESEARCH_ASSISTED_SYNTHESIS = "research_assisted_synthesis"
    FALLBACK_TOOL_ROUTING = "fallback_tool_routing"
    HUMAN_INTERACTIVE_CLARIFICATION = "human_interactive_clarification"


class StrategyAttemptOutcome(str, Enum):
    """Outcome states for an executed strategy attempt."""

    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    PAUSED = "paused"
    STAGNANT = "stagnant"
    ABANDONED = "abandoned"


@dataclass(frozen=True)
class StrategyAttempt:
    """Immutable audit record of an execution strategy attempt for a goal."""

    strategy_id: str
    goal_id: str
    strategy_type: StrategyType
    attempt_number: int = 1
    plan_id: str | None = None
    subgoal_id: str | None = None
    outcome: StrategyAttemptOutcome = StrategyAttemptOutcome.FAILURE
    failure_category: str | None = None
    execution_cost: float = 0.0
    rationale: str = ""
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.strategy_id, str) or not self.strategy_id.strip():
            raise ValueError("strategy_id must be a non-empty string.")
        object.__setattr__(self, "strategy_id", self.strategy_id.strip())

        if not isinstance(self.goal_id, str) or not self.goal_id.strip():
            raise ValueError("goal_id must be a non-empty string.")
        object.__setattr__(self, "goal_id", self.goal_id.strip())

        if isinstance(self.strategy_type, str):
            object.__setattr__(self, "strategy_type", StrategyType(self.strategy_type))
        elif not isinstance(self.strategy_type, StrategyType):
            raise TypeError("strategy_type must be a StrategyType instance.")

        if not isinstance(self.attempt_number, int) or self.attempt_number < 1:
            raise ValueError("attempt_number must be an integer >= 1.")

        if isinstance(self.outcome, str):
            object.__setattr__(self, "outcome", StrategyAttemptOutcome(self.outcome))
        elif not isinstance(self.outcome, StrategyAttemptOutcome):
            raise TypeError("outcome must be a StrategyAttemptOutcome instance.")

        if self.plan_id is not None:
            object.__setattr__(self, "plan_id", str(self.plan_id).strip() or None)

        if self.subgoal_id is not None:
            object.__setattr__(self, "subgoal_id", str(self.subgoal_id).strip() or None)

        if self.failure_category is not None:
            object.__setattr__(self, "failure_category", str(self.failure_category).strip() or None)

        object.__setattr__(self, "execution_cost", max(0.0, float(self.execution_cost)))
        object.__setattr__(self, "rationale", str(self.rationale or "").strip())
        object.__setattr__(self, "created_at", float(self.created_at))

        if self.completed_at is not None:
            object.__setattr__(self, "completed_at", float(self.completed_at))

        object.__setattr__(self, "metadata", strip_forbidden_metadata_keys(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "goal_id": self.goal_id,
            "strategy_type": self.strategy_type.value,
            "attempt_number": self.attempt_number,
            "plan_id": self.plan_id,
            "subgoal_id": self.subgoal_id,
            "outcome": self.outcome.value,
            "failure_category": self.failure_category,
            "execution_cost": self.execution_cost,
            "rationale": self.rationale,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "metadata": _canonical_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StrategyAttempt:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        return cls(
            strategy_id=str(data.get("strategy_id", "")),
            goal_id=str(data.get("goal_id", "")),
            strategy_type=StrategyType(data.get("strategy_type", StrategyType.DIRECT_SKILL.value)),
            attempt_number=int(data.get("attempt_number", 1)),
            plan_id=data.get("plan_id"),
            subgoal_id=data.get("subgoal_id"),
            outcome=StrategyAttemptOutcome(data.get("outcome", StrategyAttemptOutcome.FAILURE.value)),
            failure_category=data.get("failure_category"),
            execution_cost=float(data.get("execution_cost", 0.0)),
            rationale=str(data.get("rationale", "")),
            created_at=float(data.get("created_at", time.time())),
            completed_at=data.get("completed_at"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class GoalStrategyRecord:
    """Bounded strategy history and state for a goal."""

    goal_id: str
    active_strategy: StrategyType | None = None
    attempts: tuple[StrategyAttempt, ...] = field(default_factory=tuple)
    failed_strategy_types: tuple[StrategyType, ...] = field(default_factory=tuple)
    successful_strategy_types: tuple[StrategyType, ...] = field(default_factory=tuple)
    total_strategy_pivots: int = 0
    last_updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.goal_id, str) or not self.goal_id.strip():
            raise ValueError("goal_id must be a non-empty string.")
        object.__setattr__(self, "goal_id", self.goal_id.strip())

        if self.active_strategy is not None:
            if isinstance(self.active_strategy, str):
                object.__setattr__(self, "active_strategy", StrategyType(self.active_strategy))
            elif not isinstance(self.active_strategy, StrategyType):
                raise TypeError("active_strategy must be a StrategyType or None.")

        # Normalize attempts
        if isinstance(self.attempts, (list, tuple)):
            for a in self.attempts:
                if not isinstance(a, StrategyAttempt):
                    raise TypeError("All items in attempts must be StrategyAttempt instances.")
            object.__setattr__(self, "attempts", tuple(self.attempts))
        else:
            raise TypeError("attempts must be a sequence of StrategyAttempt instances.")

        # Normalize strategy sets
        norm_failed = []
        for s in self.failed_strategy_types:
            st = StrategyType(s) if isinstance(s, str) else s
            if not isinstance(st, StrategyType):
                raise TypeError("failed_strategy_types items must be StrategyType.")
            if st not in norm_failed:
                norm_failed.append(st)
        object.__setattr__(self, "failed_strategy_types", tuple(norm_failed))

        norm_succ = []
        for s in self.successful_strategy_types:
            st = StrategyType(s) if isinstance(s, str) else s
            if not isinstance(st, StrategyType):
                raise TypeError("successful_strategy_types items must be StrategyType.")
            if st not in norm_succ:
                norm_succ.append(st)
        object.__setattr__(self, "successful_strategy_types", tuple(norm_succ))

        object.__setattr__(self, "total_strategy_pivots", max(0, int(self.total_strategy_pivots)))
        object.__setattr__(self, "last_updated_at", float(self.last_updated_at))
        object.__setattr__(self, "metadata", strip_forbidden_metadata_keys(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "active_strategy": self.active_strategy.value if self.active_strategy else None,
            "attempts": [a.to_dict() for a in self.attempts],
            "failed_strategy_types": [s.value for s in self.failed_strategy_types],
            "successful_strategy_types": [s.value for s in self.successful_strategy_types],
            "total_strategy_pivots": self.total_strategy_pivots,
            "last_updated_at": self.last_updated_at,
            "metadata": _canonical_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoalStrategyRecord:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        active = data.get("active_strategy")
        active_st = StrategyType(active) if active else None
        raw_attempts = data.get("attempts", [])
        attempts = [StrategyAttempt.from_dict(a) for a in raw_attempts]
        failed = [StrategyType(s) for s in data.get("failed_strategy_types", [])]
        succ = [StrategyType(s) for s in data.get("successful_strategy_types", [])]
        return cls(
            goal_id=str(data.get("goal_id", "")),
            active_strategy=active_st,
            attempts=tuple(attempts),
            failed_strategy_types=tuple(failed),
            successful_strategy_types=tuple(succ),
            total_strategy_pivots=int(data.get("total_strategy_pivots", 0)),
            last_updated_at=float(data.get("last_updated_at", time.time())),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class StagnationReport:
    """Convergence and stagnation diagnostics for an evaluated goal."""

    report_id: str
    goal_id: str
    consecutive_stagnant_evaluations: int
    progress_delta: float = 0.0
    current_progress_percentage: float = 0.0
    is_stagnant: bool = True
    should_pivot_strategy: bool = True
    should_abandon_goal: bool = False
    diagnostic_summary: str = ""
    recommended_strategy: StrategyType | None = None
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.report_id, str) or not self.report_id.strip():
            raise ValueError("report_id must be a non-empty string.")
        object.__setattr__(self, "report_id", self.report_id.strip())

        if not isinstance(self.goal_id, str) or not self.goal_id.strip():
            raise ValueError("goal_id must be a non-empty string.")
        object.__setattr__(self, "goal_id", self.goal_id.strip())

        object.__setattr__(self, "consecutive_stagnant_evaluations", max(0, int(self.consecutive_stagnant_evaluations)))
        object.__setattr__(self, "progress_delta", float(self.progress_delta))
        object.__setattr__(self, "current_progress_percentage", max(0.0, min(1.0, float(self.current_progress_percentage))))
        object.__setattr__(self, "is_stagnant", bool(self.is_stagnant))
        object.__setattr__(self, "should_pivot_strategy", bool(self.should_pivot_strategy))
        object.__setattr__(self, "should_abandon_goal", bool(self.should_abandon_goal))
        object.__setattr__(self, "diagnostic_summary", str(self.diagnostic_summary or "").strip())

        if self.recommended_strategy is not None:
            if isinstance(self.recommended_strategy, str):
                object.__setattr__(self, "recommended_strategy", StrategyType(self.recommended_strategy))
            elif not isinstance(self.recommended_strategy, StrategyType):
                raise TypeError("recommended_strategy must be a StrategyType or None.")

        object.__setattr__(self, "timestamp", float(self.timestamp))
        object.__setattr__(self, "metadata", strip_forbidden_metadata_keys(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "goal_id": self.goal_id,
            "consecutive_stagnant_evaluations": self.consecutive_stagnant_evaluations,
            "progress_delta": self.progress_delta,
            "current_progress_percentage": self.current_progress_percentage,
            "is_stagnant": self.is_stagnant,
            "should_pivot_strategy": self.should_pivot_strategy,
            "should_abandon_goal": self.should_abandon_goal,
            "diagnostic_summary": self.diagnostic_summary,
            "recommended_strategy": self.recommended_strategy.value if self.recommended_strategy else None,
            "timestamp": self.timestamp,
            "metadata": _canonical_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StagnationReport:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        rec_st = data.get("recommended_strategy")
        return cls(
            report_id=str(data.get("report_id", "")),
            goal_id=str(data.get("goal_id", "")),
            consecutive_stagnant_evaluations=int(data.get("consecutive_stagnant_evaluations", 0)),
            progress_delta=float(data.get("progress_delta", 0.0)),
            current_progress_percentage=float(data.get("current_progress_percentage", 0.0)),
            is_stagnant=bool(data.get("is_stagnant", True)),
            should_pivot_strategy=bool(data.get("should_pivot_strategy", True)),
            should_abandon_goal=bool(data.get("should_abandon_goal", False)),
            diagnostic_summary=str(data.get("diagnostic_summary", "")),
            recommended_strategy=StrategyType(rec_st) if rec_st else None,
            timestamp=float(data.get("timestamp", time.time())),
            metadata=dict(data.get("metadata", {})),
        )
