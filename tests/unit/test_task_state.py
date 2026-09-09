import pytest
import time

from core.agent_runtime import AgentResult
from core.task_planner import ExecutionPlan, PlanStep
from core.task_state import StepState, StepStatus, TaskState, TaskStatus
from core.task_state_store import InMemoryTaskStateStore, TaskStateStore


def test_step_state_initialization_and_validation():
    st = StepState(step_id="step_1")
    assert st.step_id == "step_1"
    assert st.status == StepStatus.NOT_STARTED
    assert st.agent_result is None
    assert st.output is None

    # Custom status string normalization
    st2 = StepState(step_id="step_2", status="completed", output="done")
    assert st2.status == StepStatus.COMPLETED
    assert st2.output == "done"

    with pytest.raises(ValueError):
        StepState(step_id="")

    with pytest.raises(TypeError):
        StepState(step_id="step_1", status=123)

    with pytest.raises(TypeError):
        StepState(step_id="step_1", agent_result="not_a_result")

    with pytest.raises(TypeError):
        StepState(step_id="step_1", metadata="not_a_dict")


def test_task_state_initialization_and_validation():
    s1 = PlanStep(step_id="s1", skill_name="skill_a")
    plan = ExecutionPlan(steps=(s1,), plan_id="plan_1")

    state = TaskState(task_id="task_1", plan_id="plan_1", plan=plan)
    assert state.task_id == "task_1"
    assert state.plan_id == "plan_1"
    assert state.status == TaskStatus.PENDING
    assert state.is_completed() is False
    assert state.is_failed() is False

    with pytest.raises(ValueError):
        TaskState(task_id="", plan_id="plan_1")

    with pytest.raises(ValueError):
        TaskState(task_id="task_1", plan_id="")

    with pytest.raises(TypeError):
        TaskState(task_id="t1", plan_id="p1", status=123)

    with pytest.raises(TypeError):
        TaskState(task_id="t1", plan_id="p1", plan="not_a_plan")


def test_task_state_step_lookup_and_status_checks():
    state = TaskState(
        task_id="t1",
        plan_id="p1",
        step_states={"s1": StepState(step_id="s1", status=StepStatus.COMPLETED)},
    )
    assert state.get_step_state("s1").status == StepStatus.COMPLETED

    with pytest.raises(KeyError):
        state.get_step_state("unknown")

    with pytest.raises(KeyError):
        state.get_step_state("")


def test_in_memory_task_state_store_crud():
    store = InMemoryTaskStateStore()
    assert isinstance(store, TaskStateStore)

    s1 = PlanStep(step_id="s1", skill_name="skill_a")
    plan = ExecutionPlan(steps=(s1,), plan_id="p1")

    # 1. Create
    state = store.create(task_id="t1", plan_id="p1", plan=plan)
    assert state.task_id == "t1"
    assert state.plan_id == "p1"
    assert "s1" in state.step_states
    assert store.exists("t1") is True
    assert store.exists("unknown") is False
    assert store.list_tasks() == ["t1"]

    # 2. Duplicate create rejection
    with pytest.raises(ValueError, match="Task already exists"):
        store.create(task_id="t1", plan_id="p1")

    # 3. Get
    loaded = store.get("t1")
    assert loaded.task_id == "t1"
    assert loaded.status == TaskStatus.PENDING

    # 4. Save and mutate
    loaded.status = TaskStatus.RUNNING
    loaded.step_states["s1"].status = StepStatus.RUNNING
    store.save(loaded)

    reloaded = store.get("t1")
    assert reloaded.status == TaskStatus.RUNNING
    assert reloaded.step_states["s1"].status == StepStatus.RUNNING

    # 5. Unknown get
    with pytest.raises(KeyError):
        store.get("nonexistent")

    with pytest.raises(KeyError):
        store.get("")

    # 6. Invalid save
    with pytest.raises(TypeError):
        store.save("not_a_state")
