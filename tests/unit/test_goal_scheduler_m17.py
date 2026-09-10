import time
import pytest
from core.goal import Goal, GoalPriority, GoalStatus
from core.goal_reasoner import GoalEvaluationResult, GoalProgress
from core.goal_scheduler import MultiGoalScheduler
from core.goal_store import InMemoryGoalStore
from core.resource_locks import SharedResourceLockManager
from core.scheduling_types import LockType


class FakeReasoner:
    def evaluate(self, goal, observations=()):
        return GoalEvaluationResult(
            is_completed=True,
            action_needed=False,
            new_progress=GoalProgress(percentage=1.0, current_stage="completed"),
            rationale="Completed in test.",
        )


class FakeGoalEngine:
    def __init__(self):
        self.goal_store = InMemoryGoalStore()
        self.reasoner = FakeReasoner()
        self.evaluation_calls = []

    def get_goal(self, goal_id):
        return self.goal_store.get(goal_id)

    def evaluate_goal(self, goal_id):
        self.evaluation_calls.append(goal_id)
        g = self.goal_store.get(goal_id)
        res = self.reasoner.evaluate(g)
        comp = g.with_progress(progress=res.new_progress, status=GoalStatus.COMPLETED)
        self.goal_store.update(comp)
        return res


def test_scheduler_priority_ordering():
    sched = MultiGoalScheduler(aging_factor=0.0)
    sched.schedule_goal("goal_low", priority=GoalPriority.LOW)
    sched.schedule_goal("goal_crit", priority=GoalPriority.CRITICAL)
    sched.schedule_goal("goal_med", priority=GoalPriority.MEDIUM)

    queued = sched.get_queued_tasks()
    order = [t.goal_id for t in queued]
    assert order == ["goal_crit", "goal_med", "goal_low"]


def test_scheduler_starvation_aging():
    sched = MultiGoalScheduler(aging_factor=2.0)
    # Low priority queued at t=0
    sched.schedule_goal("goal_low", priority=GoalPriority.LOW, current_time=0.0)
    # High priority queued at t=120 (2 mins later)
    sched.schedule_goal("goal_high", priority=GoalPriority.HIGH, current_time=120.0)

    # At t=120.0:
    # goal_high base = 3.0, wait = 0 -> eff = 3.0
    # goal_low base = 1.0, wait = 120s -> bonus = (120/60) * 2.0 = 4.0 -> eff = 5.0!
    queued = sched.get_queued_tasks(current_time=120.0)
    assert queued[0].goal_id == "goal_low"
    assert queued[1].goal_id == "goal_high"


def test_scheduler_concurrency_batch_execution():
    engine = FakeGoalEngine()
    engine.goal_store.create(Goal(title="G1", goal_id="g1"))
    engine.goal_store.create(Goal(title="G2", goal_id="g2"))
    engine.goal_store.create(Goal(title="G3", goal_id="g3"))

    sched = MultiGoalScheduler(max_concurrent_goals=2)
    sched.schedule_goal("g1")
    sched.schedule_goal("g2")
    sched.schedule_goal("g3")

    # Step batch 1: max 2 goals executed
    batch1 = sched.step_next_batch(engine, max_batch_size=2)
    assert len(batch1) == 2
    assert len(engine.evaluation_calls) == 2

    # Step batch 2: g3 executed
    batch2 = sched.step_next_batch(engine, max_batch_size=2)
    assert len(batch2) == 1
    assert len(engine.evaluation_calls) == 3


def test_scheduler_respects_prerequisite_dependencies():
    engine = FakeGoalEngine()
    dep_goal = engine.goal_store.create(Goal(title="Dep Goal", goal_id="dep_1"))
    main_goal = engine.goal_store.create(Goal(title="Main Goal", goal_id="main_1", depends_on_goal_ids=["dep_1"]))

    sched = MultiGoalScheduler()
    sched.schedule_goal("main_1", priority=GoalPriority.HIGH)

    # Main goal should not run because dep_1 is not completed
    batch = sched.step_next_batch(engine)
    assert len(batch) == 0

    # Complete dep_1
    comp_dep = dep_goal.with_status(GoalStatus.COMPLETED)
    engine.goal_store.update(comp_dep)

    # Now main_1 runs
    batch2 = sched.step_next_batch(engine)
    assert len(batch2) == 1
    assert batch2[0].is_completed is True


def test_scheduler_respects_resource_locks():
    engine = FakeGoalEngine()
    engine.goal_store.create(Goal(title="Goal 1", goal_id="g1"))
    engine.goal_store.create(Goal(title="Goal 2", goal_id="g2"))

    lock_mgr = SharedResourceLockManager()
    # External lock on tool:database by g_other
    lock_mgr.acquire_lock("tool:database", "g_other", lock_type=LockType.EXCLUSIVE_WRITE)

    sched = MultiGoalScheduler(lock_manager=lock_mgr)
    sched.schedule_goal("g1", required_resources=["tool:database"])

    # g1 cannot run because tool:database is locked
    batch = sched.step_next_batch(engine)
    assert len(batch) == 0

    # Release external lock
    lock_mgr.release_all_locks_for_goal("g_other")

    # Now g1 can run
    batch2 = sched.step_next_batch(engine)
    assert len(batch2) == 1


def test_scheduler_cancellation():
    sched = MultiGoalScheduler()
    sched.schedule_goal("g_cancel", priority=GoalPriority.LOW)
    assert sched.get_queue_status()["total_scheduled"] == 1

    assert sched.cancel_scheduled_goal("g_cancel") is True
    assert sched.get_queue_status()["total_scheduled"] == 0
