import time
import pytest
from core.goal import (
    Goal,
    GoalObservation,
    GoalPriority,
    GoalProgress,
    GoalStatus,
    GoalTrigger,
    TriggerType,
    deserialize_goal,
    detect_dependency_cycles,
    get_topological_evaluation_order,
    serialize_goal,
    validate_goal_hierarchy,
)
from core.provenance import wrap_tainted, is_tainted


def test_goal_hierarchy_defaults():
    goal = Goal(title="Root Goal")
    assert goal.parent_goal_id is None
    assert goal.subgoal_ids == ()
    assert goal.depends_on_goal_ids == ()
    assert goal.depth == 0
    assert goal.executed_task_ids == ()
    assert goal.status == GoalStatus.CREATED


def test_goal_hierarchy_with_subgoals_and_dependencies():
    g1 = Goal(
        goal_id="g1",
        title="Deploy System",
        subgoal_ids=("g2", "g3"),
        depends_on_goal_ids=("g0",),
        depth=1,
        executed_task_ids=("task_1",),
    )
    assert g1.parent_goal_id is None
    assert g1.subgoal_ids == ("g2", "g3")
    assert g1.depends_on_goal_ids == ("g0",)
    assert g1.depth == 1
    assert g1.executed_task_ids == ("task_1",)


def test_goal_cannot_depend_on_itself():
    with pytest.raises(ValueError, match="cannot depend on itself"):
        Goal(goal_id="g1", title="Self Dep", depends_on_goal_ids=("g1",))


def test_goal_cannot_be_its_own_parent():
    with pytest.raises(ValueError, match="cannot be its own parent"):
        Goal(goal_id="g1", title="Self Parent", parent_goal_id="g1")


def test_goal_cannot_include_itself_as_subgoal():
    with pytest.raises(ValueError, match="cannot include itself as a subgoal"):
        Goal(goal_id="g1", title="Self Subgoal", subgoal_ids=("g1",))


def test_goal_cannot_include_parent_as_subgoal():
    with pytest.raises(ValueError, match="cannot include parent"):
        Goal(goal_id="g2", title="Sub", parent_goal_id="g1", subgoal_ids=("g1",))


def test_max_depth_enforcement():
    with pytest.raises(ValueError, match="depth .* exceeds maximum"):
        Goal(title="Too Deep", depth=4)


def test_max_subgoals_per_parent_enforcement():
    with pytest.raises(ValueError, match="Sub-goal count .* exceeds maximum"):
        Goal(title="Too Many Subgoals", subgoal_ids=("s1", "s2", "s3", "s4", "s5", "s6"))


def test_max_dependencies_enforcement():
    with pytest.raises(ValueError, match="Dependency count .* exceeds maximum"):
        Goal(title="Too Many Deps", depends_on_goal_ids=tuple(f"d{i}" for i in range(11)))


def test_goal_with_methods():
    g = Goal(goal_id="root", title="Root")
    g_sub = g.with_subgoal("child1")
    assert "child1" in g_sub.subgoal_ids
    assert g_sub.subgoal_ids == ("child1",)

    # Idempotent addition
    g_sub2 = g_sub.with_subgoal("child1")
    assert g_sub2.subgoal_ids == ("child1",)

    g_dep = g_sub.with_dependency("dep1")
    assert g_dep.depends_on_goal_ids == ("dep1",)

    g_task = g_dep.with_executed_task("task_123")
    assert g_task.executed_task_ids == ("task_123",)


def test_dependency_cycle_detection():
    # A -> B -> C -> A
    ga = Goal(goal_id="ga", title="A", depends_on_goal_ids=("gb",))
    gb = Goal(goal_id="gb", title="B", depends_on_goal_ids=("gc",))
    gc = Goal(goal_id="gc", title="C", depends_on_goal_ids=("ga",))

    cycles = detect_dependency_cycles([ga, gb, gc])
    assert len(cycles) > 0
    assert any("ga" in c for c in cycles)

    with pytest.raises(ValueError, match="dependency cycles detected"):
        validate_goal_hierarchy([ga, gb, gc])


def test_valid_hierarchy_validation():
    root = Goal(goal_id="root", title="Root", subgoal_ids=("c1", "c2"), depth=0)
    c1 = Goal(goal_id="c1", title="Child 1", parent_goal_id="root", depth=1)
    c2 = Goal(goal_id="c2", title="Child 2", parent_goal_id="root", depth=1, depends_on_goal_ids=("c1",))

    validate_goal_hierarchy([root, c1, c2])


def test_invalid_depth_relationship_validation():
    root = Goal(goal_id="root", title="Root", depth=0)
    c1 = Goal(goal_id="c1", title="Child 1", parent_goal_id="root", depth=2)  # Should be depth=1

    with pytest.raises(ValueError, match="depth .* must be parent depth"):
        validate_goal_hierarchy([root, c1])


def test_topological_evaluation_order():
    # c1 has no dependencies
    # c2 depends on c1
    # root has subgoals c1, c2
    root = Goal(goal_id="root", title="Root", subgoal_ids=("c1", "c2"), depth=0)
    c1 = Goal(goal_id="c1", title="Child 1", parent_goal_id="root", depth=1)
    c2 = Goal(goal_id="c2", title="Child 2", parent_goal_id="root", depth=1, depends_on_goal_ids=("c1",))

    ordered = get_topological_evaluation_order([root, c1, c2])
    ordered_ids = [g.goal_id for g in ordered]

    # c1 must come before c2 (dependency)
    # both c1 and c2 must come before root (subgoals)
    assert ordered_ids.index("c1") < ordered_ids.index("c2")
    assert ordered_ids.index("c1") < ordered_ids.index("root")
    assert ordered_ids.index("c2") < ordered_ids.index("root")


def test_serialization_roundtrip_with_lineage_and_taint():
    tainted_meta = {"source": wrap_tainted("https://example.com/untrusted", is_untrusted=True)}
    g = Goal(
        goal_id="g_test",
        title="Test Goal",
        description="Detailed test description",
        success_criteria=("c1", "c2"),
        constraints=("safe",),
        priority=GoalPriority.HIGH,
        status=GoalStatus.ACTIVE,
        parent_goal_id="g_parent",
        subgoal_ids=("sub_1", "sub_2"),
        depends_on_goal_ids=("dep_1",),
        depth=1,
        executed_task_ids=("task_abc", "task_def"),
        metadata=tainted_meta,
    )

    serialized = serialize_goal(g)
    deserialized = deserialize_goal(serialized)

    assert deserialized.goal_id == g.goal_id
    assert deserialized.title == g.title
    assert deserialized.parent_goal_id == "g_parent"
    assert deserialized.subgoal_ids == ("sub_1", "sub_2")
    assert deserialized.depends_on_goal_ids == ("dep_1",)
    assert deserialized.depth == 1
    assert deserialized.executed_task_ids == ("task_abc", "task_def")
    assert is_tainted(deserialized.metadata["source"])
