import concurrent.futures
import json
import pytest
from pathlib import Path

from core.file_task_state_store import FileTaskStateStore
from core.task_state import StepState, StepStatus, TaskState, TaskStatus
from core.agent_plan import AgentPlan, AgentPlanStep
from core.task_planner import ExecutionPlan, PlanStep
from core.agent_runtime import AgentResult
from core.provenance import wrap_tainted, is_tainted, unwrap_tainted


def test_create_get_exists_save_delete(tmp_path):
    store = FileTaskStateStore(tmp_path / "tasks")

    plan = ExecutionPlan(
        plan_id="p1",
        steps=(PlanStep(step_id="s1", skill_name="echo", input_data={"msg": "hello"}),),
    )

    state = store.create(
        task_id="t1",
        plan_id="p1",
        plan=plan,
        goal_id="g100",
        parent_goal_id="g99",
        metadata={"custom": "val"},
    )
    assert state.task_id == "t1"
    assert store.exists("t1") is True
    assert store.exists("nonexistent") is False

    retrieved = store.get("t1")
    assert retrieved.task_id == "t1"
    assert retrieved.plan_id == "p1"
    assert retrieved.goal_id == "g100"
    assert retrieved.parent_goal_id == "g99"
    assert retrieved.metadata["custom"] == "val"

    # Update state
    retrieved.status = TaskStatus.COMPLETED
    retrieved.final_output = "success_output"
    store.save(retrieved)

    re_retrieved = store.get("t1")
    assert re_retrieved.status == TaskStatus.COMPLETED
    assert re_retrieved.final_output == "success_output"

    # Delete
    assert store.delete("t1") is True
    assert store.exists("t1") is False
    assert store.delete("t1") is False


def test_persistence_across_store_recreation(tmp_path):
    storage_dir = tmp_path / "tasks_persist"
    store1 = FileTaskStateStore(storage_dir)

    agent_plan = AgentPlan(
        plan_id="ap1",
        task_goal="test goal",
        steps=(AgentPlanStep(step_id="step1", skill_name="calculator", objective="add numbers"),),
    )
    store1.create("t_persist", "ap1", plan=agent_plan)

    # Recreate store instance on same directory
    store2 = FileTaskStateStore(storage_dir)
    assert store2.exists("t_persist") is True
    loaded = store2.get("t_persist")
    assert loaded.task_id == "t_persist"
    assert loaded.plan is not None
    assert isinstance(loaded.plan, AgentPlan)
    assert loaded.plan.task_goal == "test goal"
    assert len(loaded.plan.steps) == 1
    assert loaded.plan.steps[0].skill_name == "calculator"


def test_goal_lineage_and_taint_provenance_preservation(tmp_path):
    store = FileTaskStateStore(tmp_path / "taint_store")

    tainted_out = wrap_tainted("untrusted_result", source_type="web_search", source_urls=["http://example.com"])
    tainted_meta = {"tainted_k": wrap_tainted("danger", source_type="user")}

    state = store.create(
        task_id="t_taint",
        plan_id="p_taint",
        goal_id="root_goal_42",
        parent_goal_id="parent_goal_10",
        metadata=tainted_meta,
    )
    state.step_states["step_x"] = StepState(
        step_id="step_x",
        status=StepStatus.COMPLETED,
        output=tainted_out,
        agent_result=AgentResult(success=True, skill_name="web", output=tainted_out),
    )
    store.save(state)

    reloaded = store.get("t_taint")
    assert reloaded.goal_id == "root_goal_42"
    assert reloaded.parent_goal_id == "parent_goal_10"
    assert "tainted_k" in reloaded.metadata
    assert is_tainted(reloaded.metadata["tainted_k"])

    step_st = reloaded.get_step_state("step_x")
    assert is_tainted(step_st.output)
    assert unwrap_tainted(step_st.output) == "untrusted_result"
    assert step_st.agent_result is not None
    assert is_tainted(step_st.agent_result.output)


def test_corrupt_file_handling(tmp_path):
    store_dir = tmp_path / "corrupt_tasks"
    store_dir.mkdir(parents=True)
    store = FileTaskStateStore(store_dir)

    # Write a valid task
    store.create("valid_task", "p1")

    # Write a corrupt json file
    bad_file = store_dir / "bad_task.json"
    with open(bad_file, "w", encoding="utf-8") as f:
        f.write("{corrupt_json: invalid")

    # list_tasks should not crash and should return valid tasks
    tasks = store.list_tasks()
    assert "valid_task" in tasks

    # get() on corrupt task should raise KeyError
    with pytest.raises(KeyError):
        store.get("bad_task")


def test_concurrent_access_safety(tmp_path):
    store = FileTaskStateStore(tmp_path / "concurrent_tasks")

    def worker(i: int):
        task_id = f"task_{i}"
        store.create(task_id, f"plan_{i}")
        st = store.get(task_id)
        st.status = TaskStatus.RUNNING
        store.save(st)
        return store.exists(task_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, i) for i in range(20)]
        results = [f.result() for f in futures]

    assert all(results)
    assert len(store.list_tasks()) == 20
