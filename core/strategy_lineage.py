"""Goal Strategy Lineage Store and Transition Tracker (M16).

Tracks strategy attempts, transitions, outcomes, and failure history across goals.
Enforces bounded history per goal, detects oscillations, and provides strategy exclusion
rules based on prior execution failure and environmental evidence.
Non-authorizing: strips all permission/approval bypass keys.
"""

from __future__ import annotations

import copy
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Sequence

from app.config import settings
from core.strategy_types import (
    GoalStrategyRecord,
    StrategyAttempt,
    StrategyAttemptOutcome,
    StrategyType,
    strip_forbidden_metadata_keys,
)

logger = logging.getLogger("aura.strategy_lineage")


class StrategyLineageStore:
    """In-memory and file-backed store for tracking strategy progression and lineage per goal."""

    def __init__(
        self,
        max_history_per_goal: int | None = None,
        max_strategy_retries: int | None = None,
        records: dict[str, GoalStrategyRecord] | None = None,
        persistence_path: Path | str | None = None,
    ) -> None:
        self.max_history_per_goal = (
            max_history_per_goal
            if max_history_per_goal is not None
            else getattr(settings, "aura_max_strategy_history_per_goal", 10)
        )
        self.max_strategy_retries = (
            max_strategy_retries
            if max_strategy_retries is not None
            else getattr(settings, "aura_max_strategy_retries", 3)
        )
        self._records: dict[str, GoalStrategyRecord] = dict(records) if records else {}
        self.persistence_path = Path(persistence_path) if persistence_path else None

        if self.persistence_path and self.persistence_path.exists() and not self._records:
            self.load_from_file()

    @property
    def records(self) -> dict[str, GoalStrategyRecord]:
        return dict(self._records)

    def get_record(self, goal_id: str) -> GoalStrategyRecord | None:
        """Retrieve the strategy lineage record for a goal."""
        return self._records.get(str(goal_id).strip())

    def get_active_strategy(self, goal_id: str) -> StrategyType | None:
        """Get the currently active strategy for a goal."""
        rec = self.get_record(goal_id)
        return rec.active_strategy if rec else None

    def get_attempts(self, goal_id: str) -> tuple[StrategyAttempt, ...]:
        """Get all strategy attempts for a goal in chronological order."""
        rec = self.get_record(goal_id)
        return rec.attempts if rec else ()

    def get_last_attempt(self, goal_id: str) -> StrategyAttempt | None:
        """Get the most recent strategy attempt for a goal if any exists."""
        rec = self.get_record(goal_id)
        return rec.attempts[-1] if rec and rec.attempts else None

    def get_failed_strategies(self, goal_id: str) -> tuple[StrategyType, ...]:
        """Get all distinct strategy types that have failed for a goal."""
        rec = self.get_record(goal_id)
        return rec.failed_strategy_types if rec else ()

    def is_strategy_excluded(
        self,
        goal_id: str,
        strategy_type: StrategyType,
        has_new_observation_evidence: bool = False,
    ) -> bool:
        """Determine if a strategy should be excluded from selection for a goal.

        Rules:
        1. If strategy previously failed and no new observation evidence exists -> EXCLUDED.
        2. If strategy failed max_strategy_retries times -> STRICTLY EXCLUDED regardless of evidence.
        3. If selecting strategy would cause immediate oscillation -> EXCLUDED.
        """
        rec = self.get_record(goal_id)
        if not rec:
            return False

        # Count failures for this strategy type
        failures = [
            a for a in rec.attempts
            if a.strategy_type == strategy_type and a.outcome == StrategyAttemptOutcome.FAILURE
        ]
        failure_count = len(failures)

        if failure_count >= self.max_strategy_retries:
            return True

        if failure_count > 0 and not has_new_observation_evidence:
            return True

        # Oscillation check: check if the strategy was just attempted and failed in the previous step
        if rec.attempts and rec.attempts[-1].strategy_type == strategy_type and rec.attempts[-1].outcome == StrategyAttemptOutcome.FAILURE:
            if not has_new_observation_evidence:
                return True

        return False

    def get_consecutive_failures(self, goal_id: str) -> int:
        """Count consecutive failed attempts from the latest attempt backwards."""
        rec = self.get_record(goal_id)
        if not rec or not rec.attempts:
            return 0

        count = 0
        for attempt in reversed(rec.attempts):
            if attempt.outcome in (StrategyAttemptOutcome.FAILURE, StrategyAttemptOutcome.STAGNANT):
                count += 1
            else:
                break
        return count

    def get_consecutive_failure_count(self, goal_id: str) -> int:
        """Alias for get_consecutive_failures."""
        return self.get_consecutive_failures(goal_id)

    def record_attempt(
        self,
        goal_id: str,
        strategy_type: StrategyType | str | StrategyAttempt,
        outcome: StrategyAttemptOutcome | str = StrategyAttemptOutcome.FAILURE,
        plan_id: str | None = None,
        subgoal_id: str | None = None,
        failure_category: str | None = None,
        execution_cost: float = 0.0,
        rationale: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> StrategyAttempt:
        """Record an execution strategy attempt for a goal and update strategy state."""
        clean_goal_id = str(goal_id).strip()
        if not clean_goal_id:
            raise ValueError("goal_id must be a non-empty string.")

        if isinstance(strategy_type, StrategyAttempt):
            attempt_obj = strategy_type
            st = attempt_obj.strategy_type
            out = attempt_obj.outcome
            plan_id = attempt_obj.plan_id or plan_id
            subgoal_id = attempt_obj.subgoal_id or subgoal_id
            failure_category = attempt_obj.failure_category or failure_category
            execution_cost = attempt_obj.execution_cost or execution_cost
            rationale = attempt_obj.rationale or rationale
            metadata = dict(attempt_obj.metadata) if metadata is None else metadata
        else:
            st = StrategyType(strategy_type) if isinstance(strategy_type, str) else strategy_type
            out = StrategyAttemptOutcome(outcome) if isinstance(outcome, str) else outcome

        rec = self._records.get(clean_goal_id)
        existing_attempts = list(rec.attempts) if rec else []
        
        last_attempt = existing_attempts[-1] if existing_attempts else None
        attempt_number = (last_attempt.attempt_number + 1) if last_attempt else 1

        strategy_id = f"strat_{clean_goal_id[:8]}_{attempt_number}_{uuid.uuid4().hex[:6]}"
        now = time.time()

        attempt = StrategyAttempt(
            strategy_id=strategy_id,
            goal_id=clean_goal_id,
            strategy_type=st,
            attempt_number=attempt_number,
            plan_id=plan_id,
            subgoal_id=subgoal_id,
            outcome=out,
            failure_category=failure_category,
            execution_cost=execution_cost,
            rationale=rationale,
            created_at=now,
            completed_at=now,
            metadata=strip_forbidden_metadata_keys(metadata or {}),
        )

        existing_attempts.append(attempt)

        # Enforce max history bounds per goal
        if len(existing_attempts) > self.max_history_per_goal:
            existing_attempts = existing_attempts[-self.max_history_per_goal:]

        # Recalculate failed and successful sets
        failed_set: list[StrategyType] = []
        succ_set: list[StrategyType] = []
        for a in existing_attempts:
            if a.outcome == StrategyAttemptOutcome.FAILURE and a.strategy_type not in failed_set:
                failed_set.append(a.strategy_type)
            elif a.outcome == StrategyAttemptOutcome.SUCCESS and a.strategy_type not in succ_set:
                succ_set.append(a.strategy_type)

        pivots = rec.total_strategy_pivots if rec else 0
        if rec and rec.active_strategy and rec.active_strategy != st:
            pivots += 1

        updated_record = GoalStrategyRecord(
            goal_id=clean_goal_id,
            active_strategy=st,
            attempts=tuple(existing_attempts),
            failed_strategy_types=tuple(failed_set),
            successful_strategy_types=tuple(succ_set),
            total_strategy_pivots=pivots,
            last_updated_at=now,
            metadata=strip_forbidden_metadata_keys(rec.metadata if rec else {}),
        )

        self._records[clean_goal_id] = updated_record
        self._auto_save()
        return attempt

    def update_attempt(self, goal_id: str, attempt: StrategyAttempt) -> None:
        """Update an existing attempt by strategy_id in place."""
        clean_goal_id = str(goal_id).strip()
        rec = self._records.get(clean_goal_id)
        if not rec:
            return
        attempts = list(rec.attempts)
        replaced = False
        for i, a in enumerate(attempts):
            if a.strategy_id == attempt.strategy_id:
                attempts[i] = attempt
                replaced = True
                break
        if not replaced:
            attempts.append(attempt)

        # Recalculate failed and successful sets
        failed_set: list[StrategyType] = []
        succ_set: list[StrategyType] = []
        for a in attempts:
            if a.outcome == StrategyAttemptOutcome.FAILURE and a.strategy_type not in failed_set:
                failed_set.append(a.strategy_type)
            elif a.outcome == StrategyAttemptOutcome.SUCCESS and a.strategy_type not in succ_set:
                succ_set.append(a.strategy_type)

        self._records[clean_goal_id] = GoalStrategyRecord(
            goal_id=clean_goal_id,
            active_strategy=rec.active_strategy,
            attempts=tuple(attempts),
            failed_strategy_types=tuple(failed_set),
            successful_strategy_types=tuple(succ_set),
            total_strategy_pivots=rec.total_strategy_pivots,
            last_updated_at=time.time(),
            metadata=rec.metadata,
        )
        self._auto_save()

    def clear_goal(self, goal_id: str) -> None:
        """Clear all strategy history for a goal."""
        clean_goal_id = str(goal_id).strip()
        if clean_goal_id in self._records:
            del self._records[clean_goal_id]
            self._auto_save()

    def _auto_save(self) -> None:
        if self.persistence_path:
            self.save_to_file(self.persistence_path)

    def save_to_file(self, path: Path | str | None = None) -> None:
        """Persist lineage store state to JSON."""
        target = Path(path) if path else self.persistence_path
        if not target:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "max_history_per_goal": self.max_history_per_goal,
            "max_strategy_retries": self.max_strategy_retries,
            "records": {k: v.to_dict() for k, v in self._records.items()},
        }
        with open(target, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def load_from_file(self, path: Path | str | None = None) -> None:
        """Load lineage store state from JSON."""
        target = Path(path) if path else self.persistence_path
        if not target or not target.exists():
            return
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.max_history_per_goal = int(data.get("max_history_per_goal", self.max_history_per_goal))
        self.max_strategy_retries = int(data.get("max_strategy_retries", self.max_strategy_retries))
        raw_recs = data.get("records", {})
        self._records = {k: GoalStrategyRecord.from_dict(v) for k, v in raw_recs.items()}
