"""Goal Stagnation Monitoring & Convergence Diagnostics (M16).

Tracks goal progress convergence over consecutive evaluation cycles.
Detects stagnant execution (zero or sub-threshold progress delta over N cycles),
synthesizes diagnostic StagnationReports, triggers strategy pivoting, and
safely identifies irrecoverable goals for graceful diagnostic abandonment.
"""

from __future__ import annotations

import copy
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from app.config import settings
from core.goal import Goal, GoalProgress, GoalStatus
from core.meta_policy import MetaPolicyEngine
from core.strategy_lineage import StrategyLineageStore
from core.strategy_types import (
    StagnationReport,
    StrategyAttemptOutcome,
    StrategyType,
    strip_forbidden_metadata_keys,
)

logger = logging.getLogger("aura.goal_stagnation")


@dataclass(frozen=True)
class GoalProgressEvaluation:
    """Historical checkpoint of a goal's progress percentage at a given time."""

    evaluation_index: int
    progress_percentage: float
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "evaluation_index", max(0, int(self.evaluation_index)))
        object.__setattr__(self, "progress_percentage", max(0.0, min(1.0, float(self.progress_percentage))))
        object.__setattr__(self, "timestamp", float(self.timestamp))
        object.__setattr__(self, "metadata", strip_forbidden_metadata_keys(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluation_index": self.evaluation_index,
            "progress_percentage": self.progress_percentage,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoalProgressEvaluation:
        if not isinstance(data, dict):
            raise TypeError("data must be a dict.")
        return cls(
            evaluation_index=int(data.get("evaluation_index", 0)),
            progress_percentage=float(data.get("progress_percentage", 0.0)),
            timestamp=float(data.get("timestamp", time.time())),
            metadata=dict(data.get("metadata", {})),
        )


class GoalStagnationMonitor:
    """Monitors goal convergence across evaluation cycles and generates stagnation diagnostics."""

    def __init__(
        self,
        max_stagnation_evaluations: int | None = None,
        min_progress_delta: float = 0.001,
        lineage_store: StrategyLineageStore | None = None,
        meta_policy: MetaPolicyEngine | None = None,
        persistence_path: Path | str | None = None,
    ) -> None:
        self.max_stagnation_evaluations = (
            max_stagnation_evaluations
            if max_stagnation_evaluations is not None
            else getattr(settings, "aura_max_goal_stagnation_evaluations", 5)
        )
        self.min_progress_delta = max(0.0, float(min_progress_delta))
        self.lineage_store = lineage_store or StrategyLineageStore()
        self.meta_policy = meta_policy or MetaPolicyEngine(lineage_store=self.lineage_store)
        self.persistence_path = Path(persistence_path) if persistence_path else None

        self._history: dict[str, list[GoalProgressEvaluation]] = {}

        if self.persistence_path and self.persistence_path.exists():
            self.load_from_file()

    def record_progress(
        self,
        goal_id: str,
        progress_percentage: float,
        metadata: dict[str, Any] | None = None,
    ) -> GoalProgressEvaluation:
        """Record a progress checkpoint for a goal."""
        clean_id = str(goal_id).strip()
        if not clean_id:
            raise ValueError("goal_id must be a non-empty string.")

        history = self._history.setdefault(clean_id, [])
        eval_idx = len(history) + 1
        record = GoalProgressEvaluation(
            evaluation_index=eval_idx,
            progress_percentage=progress_percentage,
            timestamp=time.time(),
            metadata=strip_forbidden_metadata_keys(metadata or {}),
        )
        history.append(record)
        # Bound in-memory history to last 50 evaluations per goal
        if len(history) > 50:
            self._history[clean_id] = history[-50:]

        self._auto_save()
        return record

    def get_progress_history(self, goal_id: str) -> list[GoalProgressEvaluation]:
        """Get all recorded progress evaluations for a goal."""
        return list(self._history.get(str(goal_id).strip(), []))

    def check_stagnation(
        self,
        goal: Goal,
        current_progress_percentage: float | None = None,
        context: dict[str, Any] | None = None,
    ) -> StagnationReport:
        """Evaluate convergence and determine whether goal is stagnant, requires strategy pivot, or abandonment."""
        if not isinstance(goal, Goal):
            raise TypeError("goal must be a Goal instance.")

        goal_id = goal.goal_id
        progress = (
            float(current_progress_percentage)
            if current_progress_percentage is not None
            else goal.progress.percentage
        )

        # Record this evaluation
        self.record_progress(goal_id, progress)
        history = self._history.get(goal_id, [])

        report_id = f"stag-{uuid.uuid4().hex[:8]}"
        now = time.time()

        if len(history) < 2:
            return StagnationReport(
                report_id=report_id,
                goal_id=goal_id,
                consecutive_stagnant_evaluations=0,
                progress_delta=0.0,
                current_progress_percentage=progress,
                is_stagnant=False,
                should_pivot_strategy=False,
                should_abandon_goal=False,
                diagnostic_summary="Insufficient evaluation history to determine stagnation.",
                recommended_strategy=None,
                timestamp=now,
            )

        # Count consecutive stagnant transitions from latest backwards
        consecutive_stagnant_transitions = 0

        for i in range(len(history) - 1, 0, -1):
            curr_p = history[i].progress_percentage
            prev_p = history[i - 1].progress_percentage
            delta = curr_p - prev_p
            if delta <= self.min_progress_delta:
                consecutive_stagnant_transitions += 1
            else:
                break

        consecutive_stagnant_evals = (consecutive_stagnant_transitions + 1) if consecutive_stagnant_transitions > 0 else 0

        # Calculate delta over the evaluation window
        window_size = min(len(history), self.max_stagnation_evaluations)
        window_start_p = history[-window_size].progress_percentage
        window_delta = progress - window_start_p

        is_stagnant = consecutive_stagnant_evals >= self.max_stagnation_evaluations
        should_pivot = is_stagnant
        should_abandon = False
        summary = ""
        rec_strategy: StrategyType | None = None

        if is_stagnant:
            # Check if all strategies have been exhausted or retry limit reached
            lineage_rec = self.lineage_store.get_record(goal_id)
            failed_strats = lineage_rec.failed_strategy_types if lineage_rec else ()

            # If stagnant for double the max evaluations or all strategies exhausted
            if consecutive_stagnant_evals >= (self.max_stagnation_evaluations * 2) or len(failed_strats) >= len(StrategyType):
                should_abandon = True
                should_pivot = False
                summary = (
                    f"Goal '{goal_id}' is irrecoverably stagnant (0 progress across {consecutive_stagnant_evals} evaluations). "
                    f"All viable strategies exhausted ({len(failed_strats)} modalities failed). Abandonment recommended."
                )
            else:
                # Ask MetaPolicyEngine for recommended pivot strategy
                decision = self.meta_policy.select_strategy(
                    goal=goal,
                    failure_category="stagnation",
                    context={"consecutive_stagnant": consecutive_stagnant_evals},
                )
                rec_strategy = decision.selected_strategy
                summary = (
                    f"Goal '{goal_id}' stagnant for {consecutive_stagnant_evals} evaluations (delta: {window_delta:.3f}). "
                    f"Strategy pivot recommended -> {rec_strategy.value}."
                )
        else:
            summary = f"Goal convergence nominal ({consecutive_stagnant_evals}/{self.max_stagnation_evaluations} stagnant cycles, delta: {window_delta:.3f})."

        return StagnationReport(
            report_id=report_id,
            goal_id=goal_id,
            consecutive_stagnant_evaluations=consecutive_stagnant_evals,
            progress_delta=window_delta,
            current_progress_percentage=progress,
            is_stagnant=is_stagnant,
            should_pivot_strategy=should_pivot,
            should_abandon_goal=should_abandon,
            diagnostic_summary=summary,
            recommended_strategy=rec_strategy,
            timestamp=now,
            metadata=strip_forbidden_metadata_keys(context or {}),
        )

    def clear_goal(self, goal_id: str) -> None:
        """Clear progress history for a goal."""
        clean_id = str(goal_id).strip()
        if clean_id in self._history:
            del self._history[clean_id]
            self._auto_save()

    def _auto_save(self) -> None:
        if self.persistence_path:
            self.save_to_file(self.persistence_path)

    def save_to_file(self, path: Path | str | None = None) -> None:
        """Persist stagnation history to disk."""
        target = Path(path) if path else self.persistence_path
        if not target:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "max_stagnation_evaluations": self.max_stagnation_evaluations,
            "min_progress_delta": self.min_progress_delta,
            "history": {
                k: [rec.to_dict() for rec in recs]
                for k, recs in self._history.items()
            },
        }
        with open(target, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def load_from_file(self, path: Path | str | None = None) -> None:
        """Restore stagnation history from disk."""
        target = Path(path) if path else self.persistence_path
        if not target or not target.exists():
            return
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.max_stagnation_evaluations = int(data.get("max_stagnation_evaluations", self.max_stagnation_evaluations))
        self.min_progress_delta = float(data.get("min_progress_delta", self.min_progress_delta))
        raw_history = data.get("history", {})
        self._history = {
            k: [GoalProgressEvaluation.from_dict(d) for d in v]
            for k, v in raw_history.items()
        }
