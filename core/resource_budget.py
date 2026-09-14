import logging
import threading
import time
from typing import Any

from app.config import settings
from core.scheduling_types import (
    ResourceAllocationResult,
    ResourceQuota,
)

logger = logging.getLogger("aura.resource_budget")


class ResourceBudgetManager:
    """Global and per-goal resource budget governor enforcing token rates, tool limits, and concurrency."""

    def __init__(
        self,
        max_concurrent_goals: int | None = None,
        global_max_tool_calls_per_minute: int | None = None,
        global_max_tokens_per_minute: int | None = None,
        default_goal_quota: ResourceQuota | None = None,
    ):
        self.max_concurrent_goals = (
            max_concurrent_goals
            if max_concurrent_goals is not None
            else getattr(settings, "aura_max_concurrent_active_goals", 4)
        )
        self.global_max_tool_calls_per_minute = (
            global_max_tool_calls_per_minute
            if global_max_tool_calls_per_minute is not None
            else getattr(settings, "aura_global_max_tool_calls_per_minute", 120)
        )
        self.global_max_tokens_per_minute = (
            global_max_tokens_per_minute
            if global_max_tokens_per_minute is not None
            else getattr(settings, "aura_global_max_tokens_per_minute", 100000)
        )
        self.default_goal_quota = (
            default_goal_quota if default_goal_quota is not None else ResourceQuota()
        )

        self._lock = threading.RLock()

        # Active goal tracking: goal_id -> start_time
        self._active_goals: dict[str, float] = {}

        # Sliding window records: list of (timestamp, count)
        self._global_tool_call_history: list[tuple[float, int]] = []
        self._global_token_history: list[tuple[float, int]] = []

        # Per-goal consumption: goal_id -> {"tokens": int, "tool_calls": int, "started_at": float}
        self._goal_usage: dict[str, dict[str, Any]] = {}

    def _prune_sliding_window(self, history: list[tuple[float, int]], current_time: float) -> list[tuple[float, int]]:
        """Remove entries older than 60.0 seconds from sliding window."""
        cutoff = current_time - 60.0
        return [entry for entry in history if entry[0] >= cutoff]

    def acquire_quota(
        self,
        goal_id: str,
        estimated_tokens: int = 0,
        estimated_tool_calls: int = 1,
        quota: ResourceQuota | None = None,
        current_time: float | None = None,
    ) -> ResourceAllocationResult:
        """Evaluate resource limits and allocate quota if within global and per-goal bounds."""
        clean_goal_id = str(goal_id).strip()
        if not clean_goal_id:
            raise ValueError("goal_id must be a non-empty string.")

        eff_quota = quota if quota is not None else self.default_goal_quota
        now = current_time if current_time is not None else time.time()

        with self._lock:
            # 1. Prune sliding window rate counters
            self._global_tool_call_history = self._prune_sliding_window(self._global_tool_call_history, now)
            self._global_token_history = self._prune_sliding_window(self._global_token_history, now)

            # 2. Check global concurrent goal slots
            if clean_goal_id not in self._active_goals:
                if len(self._active_goals) >= self.max_concurrent_goals:
                    try:
                        from core.metrics import get_metrics_registry
                        get_metrics_registry().get_counter("aura_rate_limits_throttled_total").inc(
                            labels={"resource": "goals"}
                        )
                    except Exception:
                        pass
                    return ResourceAllocationResult(
                        is_granted=False,
                        reason=f"Global active goals limit reached ({len(self._active_goals)}/{self.max_concurrent_goals}).",
                        quota_remaining={"active_goals": len(self._active_goals), "max": self.max_concurrent_goals},
                    )

            # 3. Check global tool call rate limit
            current_tool_rate = sum(count for _, count in self._global_tool_call_history)
            if current_tool_rate + estimated_tool_calls > self.global_max_tool_calls_per_minute:
                try:
                    from core.metrics import get_metrics_registry
                    get_metrics_registry().get_counter("aura_rate_limits_throttled_total").inc(
                        labels={"resource": "tools"}
                    )
                except Exception:
                    pass
                return ResourceAllocationResult(
                    is_granted=False,
                    reason=(
                        f"Global tool calls rate limit exceeded "
                        f"({current_tool_rate + estimated_tool_calls}/{self.global_max_tool_calls_per_minute} per minute)."
                    ),
                    quota_remaining={"tool_calls_rate": current_tool_rate, "max_rate": self.global_max_tool_calls_per_minute},
                )

            # 4. Check global token rate limit
            current_token_rate = sum(count for _, count in self._global_token_history)
            if current_token_rate + estimated_tokens > self.global_max_tokens_per_minute:
                try:
                    from core.metrics import get_metrics_registry
                    get_metrics_registry().get_counter("aura_rate_limits_throttled_total").inc(
                        labels={"resource": "tokens"}
                    )
                except Exception:
                    pass
                return ResourceAllocationResult(
                    is_granted=False,
                    reason=(
                        f"Global token rate limit exceeded "
                        f"({current_token_rate + estimated_tokens}/{self.global_max_tokens_per_minute} per minute)."
                    ),
                    quota_remaining={"token_rate": current_token_rate, "max_rate": self.global_max_tokens_per_minute},
                )

            # 5. Check per-goal quota
            usage = self._goal_usage.setdefault(
                clean_goal_id,
                {"tokens": 0, "tool_calls": 0, "started_at": now},
            )

            if usage["tool_calls"] + estimated_tool_calls > eff_quota.max_tool_calls:
                try:
                    from core.metrics import get_metrics_registry
                    get_metrics_registry().get_counter("aura_rate_limits_throttled_total").inc(
                        labels={"resource": "tools"}
                    )
                except Exception:
                    pass
                return ResourceAllocationResult(
                    is_granted=False,
                    reason=(
                        f"Goal '{clean_goal_id}' tool calls quota exceeded "
                        f"({usage['tool_calls'] + estimated_tool_calls}/{eff_quota.max_tool_calls})."
                    ),
                    quota_remaining={"goal_tool_calls": usage["tool_calls"], "max": eff_quota.max_tool_calls},
                )

            if usage["tokens"] + estimated_tokens > eff_quota.max_tokens:
                try:
                    from core.metrics import get_metrics_registry
                    get_metrics_registry().get_counter("aura_rate_limits_throttled_total").inc(
                        labels={"resource": "tokens"}
                    )
                except Exception:
                    pass
                return ResourceAllocationResult(
                    is_granted=False,
                    reason=(
                        f"Goal '{clean_goal_id}' tokens quota exceeded "
                        f"({usage['tokens'] + estimated_tokens}/{eff_quota.max_tokens})."
                    ),
                    quota_remaining={"goal_tokens": usage["tokens"], "max": eff_quota.max_tokens},
                )


            # Mark goal as active
            if clean_goal_id not in self._active_goals:
                self._active_goals[clean_goal_id] = now

            return ResourceAllocationResult(
                is_granted=True,
                allocated_tokens=estimated_tokens,
                allocated_tool_calls=estimated_tool_calls,
                reason="Quota granted successfully.",
                quota_remaining={
                    "remaining_goal_tool_calls": eff_quota.max_tool_calls - usage["tool_calls"] - estimated_tool_calls,
                    "remaining_goal_tokens": eff_quota.max_tokens - usage["tokens"] - estimated_tokens,
                },
            )

    def release_quota(
        self,
        goal_id: str,
        actual_tokens: int = 0,
        actual_tool_calls: int = 0,
        current_time: float | None = None,
    ) -> None:
        """Record actual resource usage and replenish sliding window counters."""
        clean_goal_id = str(goal_id).strip()
        if not clean_goal_id:
            return

        now = current_time if current_time is not None else time.time()

        with self._lock:
            safe_tokens = max(0, actual_tokens)
            safe_tool_calls = max(0, actual_tool_calls)

            if safe_tool_calls > 0:
                self._global_tool_call_history.append((now, safe_tool_calls))
            if safe_tokens > 0:
                self._global_token_history.append((now, safe_tokens))

            if clean_goal_id in self._goal_usage:
                self._goal_usage[clean_goal_id]["tokens"] += safe_tokens
                self._goal_usage[clean_goal_id]["tool_calls"] += safe_tool_calls

    def register_goal_start(self, goal_id: str, current_time: float | None = None) -> bool:
        """Register active goal start. Returns True if concurrency slot is available."""
        clean_goal_id = str(goal_id).strip()
        if not clean_goal_id:
            return False

        now = current_time if current_time is not None else time.time()
        with self._lock:
            if clean_goal_id in self._active_goals:
                return True
            if len(self._active_goals) >= self.max_concurrent_goals:
                return False
            self._active_goals[clean_goal_id] = now
            return True

    def register_goal_finish(self, goal_id: str) -> None:
        """Release active goal slot upon completion or termination."""
        clean_goal_id = str(goal_id).strip()
        with self._lock:
            self._active_goals.pop(clean_goal_id, None)

    def get_global_utilization(self, current_time: float | None = None) -> dict[str, Any]:
        """Query current global resource usage metrics."""
        now = current_time if current_time is not None else time.time()
        with self._lock:
            self._global_tool_call_history = self._prune_sliding_window(self._global_tool_call_history, now)
            self._global_token_history = self._prune_sliding_window(self._global_token_history, now)

            return {
                "active_goals_count": len(self._active_goals),
                "max_concurrent_goals": self.max_concurrent_goals,
                "tool_calls_last_minute": sum(c for _, c in self._global_tool_call_history),
                "max_tool_calls_per_minute": self.global_max_tool_calls_per_minute,
                "tokens_last_minute": sum(c for _, c in self._global_token_history),
                "max_tokens_per_minute": self.global_max_tokens_per_minute,
            }

    def get_goal_utilization(self, goal_id: str) -> dict[str, Any]:
        """Query accumulated resource usage metrics for a specific goal."""
        clean_goal_id = str(goal_id).strip()
        with self._lock:
            usage = self._goal_usage.get(clean_goal_id, {"tokens": 0, "tool_calls": 0, "started_at": None})
            return dict(usage)

    def reset(self) -> None:
        """Reset all accounting state (for testing)."""
        with self._lock:
            self._active_goals.clear()
            self._global_tool_call_history.clear()
            self._global_token_history.clear()
            self._goal_usage.clear()
