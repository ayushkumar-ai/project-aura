import logging
import threading
import time
from collections.abc import Sequence
from typing import Any

from app.config import settings
from core.goal import Goal, GoalPriority, GoalStatus
from core.goal_reasoner import GoalEvaluationResult
from core.resource_budget import ResourceBudgetManager
from core.resource_locks import SharedResourceLockManager
from core.scheduling_types import (
    GoalScheduleStatus,
    LockType,
    ResourceQuota,
    ScheduledGoalTask,
)

logger = logging.getLogger("aura.goal_scheduler")


PRIORITY_BASE_WEIGHTS: dict[GoalPriority, float] = {
    GoalPriority.CRITICAL: 4.0,
    GoalPriority.HIGH: 3.0,
    GoalPriority.MEDIUM: 2.0,
    GoalPriority.LOW: 1.0,
}


class MultiGoalScheduler:
    """Priority-driven multi-goal scheduler with starvation aging and resource arbitration."""

    def __init__(
        self,
        budget_manager: ResourceBudgetManager | None = None,
        lock_manager: SharedResourceLockManager | None = None,
        max_concurrent_goals: int | None = None,
        aging_factor: float = 0.5,
    ):
        self.budget_manager = budget_manager if budget_manager is not None else ResourceBudgetManager()
        self.lock_manager = lock_manager if lock_manager is not None else SharedResourceLockManager()
        self.max_concurrent_goals = (
            max_concurrent_goals
            if max_concurrent_goals is not None
            else getattr(settings, "aura_max_concurrent_active_goals", 4)
        )
        self.aging_factor = max(0.0, float(aging_factor))

        self._lock = threading.RLock()
        # tasks: goal_id -> ScheduledGoalTask
        self._tasks: dict[str, ScheduledGoalTask] = {}

    def compute_effective_priority(
        self,
        task: ScheduledGoalTask,
        goal: Goal | None = None,
        current_time: float | None = None,
    ) -> float:
        """Compute dynamic scheduling priority with starvation aging and deadline urgency."""
        now = current_time if current_time is not None else time.time()
        base = task.base_weight

        # Aging: + aging_factor per 60 seconds waiting in queue
        wait_seconds = max(0.0, now - task.enqueued_at)
        aging_bonus = (wait_seconds / 60.0) * self.aging_factor

        # Deadline urgency
        urgency_bonus = 0.0
        if goal and goal.expires_at is not None:
            time_to_exp = goal.expires_at - now
            if time_to_exp <= 300.0:  # within 5 minutes of expiring
                urgency_bonus = max(0.0, (300.0 - time_to_exp) / 60.0)

        return base + aging_bonus + urgency_bonus

    def schedule_goal(
        self,
        goal_id: str,
        priority: GoalPriority | None = None,
        required_resources: Sequence[str] = (),
        metadata: dict[str, Any] | None = None,
        current_time: float | None = None,
    ) -> ScheduledGoalTask:
        """Enqueue or update a goal in the scheduler."""
        clean_gid = str(goal_id).strip()
        if not clean_gid:
            raise ValueError("goal_id must be a non-empty string.")

        eff_pri = priority if priority is not None else GoalPriority.MEDIUM
        base_w = PRIORITY_BASE_WEIGHTS.get(eff_pri, 2.0)
        now = current_time if current_time is not None else time.time()

        with self._lock:
            existing = self._tasks.get(clean_gid)
            if existing and existing.status == GoalScheduleStatus.RUNNING:
                return existing

            task = ScheduledGoalTask(
                goal_id=clean_gid,
                priority=eff_pri,
                base_weight=base_w,
                effective_priority=base_w,
                status=GoalScheduleStatus.QUEUED,
                enqueued_at=now,
                required_resources=tuple(required_resources),
                metadata=dict(metadata or {}),
            )
            self._tasks[clean_gid] = task
            return task

    def get_queued_tasks(self, current_time: float | None = None, goal_engine: Any | None = None) -> list[ScheduledGoalTask]:
        """Return all queued tasks sorted descending by dynamic effective priority."""
        now = current_time if current_time is not None else time.time()
        with self._lock:
            queued = [t for t in self._tasks.values() if t.status == GoalScheduleStatus.QUEUED]
            updated_tasks: list[ScheduledGoalTask] = []
            for t in queued:
                goal_obj = None
                if goal_engine and hasattr(goal_engine, "get_goal"):
                    try:
                        goal_obj = goal_engine.get_goal(t.goal_id)
                    except Exception:
                        goal_obj = None
                eff_p = self.compute_effective_priority(t, goal=goal_obj, current_time=now)
                upd = t.with_status(GoalScheduleStatus.QUEUED, effective_priority=eff_p)
                self._tasks[t.goal_id] = upd
                updated_tasks.append(upd)

            # Sort descending by effective_priority, then ascending by enqueued_at
            updated_tasks.sort(key=lambda x: (-x.effective_priority, x.enqueued_at))
            return updated_tasks

    def step_next_batch(
        self,
        goal_engine: Any,
        max_batch_size: int | None = None,
        current_time: float | None = None,
    ) -> list[GoalEvaluationResult]:
        """Dispatch the highest-priority runnable queued goals up to concurrency bounds."""
        if goal_engine is None:
            raise ValueError("goal_engine must not be None.")

        now = current_time if current_time is not None else time.time()
        batch_limit = max_batch_size if max_batch_size is not None else self.max_concurrent_goals
        results: list[GoalEvaluationResult] = []

        with self._lock:
            running_count = sum(1 for t in self._tasks.values() if t.status == GoalScheduleStatus.RUNNING)
            available_slots = max(0, self.max_concurrent_goals - running_count)
            slots_to_fill = min(available_slots, batch_limit)

            if slots_to_fill <= 0:
                return []

            candidate_tasks = self.get_queued_tasks(current_time=now, goal_engine=goal_engine)

            for task in candidate_tasks:
                if len(results) >= slots_to_fill:
                    break

                gid = task.goal_id

                # Fetch Goal from store
                if not goal_engine.goal_store.exists(gid):
                    self._tasks[gid] = task.with_status(GoalScheduleStatus.FAILED)
                    continue

                goal = goal_engine.get_goal(gid)
                if goal.status.is_terminal():
                    self._tasks[gid] = task.with_status(GoalScheduleStatus.COMPLETED)
                    continue

                # Check Goal Dependencies
                if goal.depends_on_goal_ids:
                    deps_satisfied = True
                    for dep_id in goal.depends_on_goal_ids:
                        if not goal_engine.goal_store.exists(dep_id):
                            deps_satisfied = False
                            break
                        dep_g = goal_engine.get_goal(dep_id)
                        if dep_g.status != GoalStatus.COMPLETED:
                            deps_satisfied = False
                            break
                    if not deps_satisfied:
                        # Prerequisite pending; skip for now
                        continue

                # Check Resource Budget
                alloc_res = self.budget_manager.acquire_quota(goal_id=gid, current_time=now)
                if not alloc_res.is_granted:
                    logger.debug("Goal '%s' skipped by scheduler due to budget constraint: %s", gid, alloc_res.reason)
                    continue

                # Check & Acquire Resource Locks
                if task.required_resources:
                    lock_res = self.lock_manager.acquire_locks_batch(
                        resource_uris=task.required_resources,
                        goal_id=gid,
                        lock_type=LockType.EXCLUSIVE_WRITE,
                        current_time=now,
                    )
                    if not lock_res.success:
                        logger.debug("Goal '%s' skipped by scheduler due to lock conflict: %s", gid, lock_res.reason)
                        self.budget_manager.release_quota(gid, current_time=now)
                        continue

                # Transition to RUNNING
                running_task = task.with_status(GoalScheduleStatus.RUNNING, started_at=now)
                self._tasks[gid] = running_task

                # Evaluate Goal via GoalEngine
                try:
                    eval_res = goal_engine.evaluate_goal(gid)
                    results.append(eval_res)

                    # Update task status based on evaluation outcome
                    updated_goal = goal_engine.get_goal(gid)
                    if updated_goal.status == GoalStatus.COMPLETED:
                        self._tasks[gid] = running_task.with_status(GoalScheduleStatus.COMPLETED, completed_at=time.time())
                    elif updated_goal.status == GoalStatus.PAUSED:
                        self._tasks[gid] = running_task.with_status(GoalScheduleStatus.PAUSED)
                    elif updated_goal.status == GoalStatus.FAILED:
                        self._tasks[gid] = running_task.with_status(GoalScheduleStatus.FAILED, completed_at=time.time())
                    else:
                        # Re-enqueue for further evaluations
                        self._tasks[gid] = running_task.with_status(GoalScheduleStatus.QUEUED)

                except Exception as ex:
                    logger.error("Exception during scheduled evaluation of goal '%s': %s", gid, ex)
                    self._tasks[gid] = running_task.with_status(GoalScheduleStatus.FAILED, completed_at=time.time())
                finally:
                    # Clean up locks and active budget count
                    self.lock_manager.release_all_locks_for_goal(gid)
                    self.budget_manager.register_goal_finish(gid)

            return results

    def cancel_scheduled_goal(self, goal_id: str) -> bool:
        """Cancel and remove a goal from the scheduler."""
        clean_gid = str(goal_id).strip()
        with self._lock:
            task = self._tasks.pop(clean_gid, None)
            if task:
                self.lock_manager.release_all_locks_for_goal(clean_gid)
                self.budget_manager.register_goal_finish(clean_gid)
                return True
            return False

    def get_queue_status(self) -> dict[str, Any]:
        """Return counts of tasks in each lifecycle status."""
        with self._lock:
            counts: dict[str, int] = {}
            for st in GoalScheduleStatus:
                counts[st.value] = sum(1 for t in self._tasks.values() if t.status == st)
            return {
                "total_scheduled": len(self._tasks),
                "counts_by_status": counts,
                "max_concurrent_goals": self.max_concurrent_goals,
            }
