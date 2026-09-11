"""Unit tests for Distributed Saga Coordinator & Compensating Engine (M25)."""

from unittest.mock import MagicMock
import pytest
from core.artifact_manager import ArtifactManager
from core.artifact_store import InMemoryArtifactStore
from core.artifact_types import ArtifactType
from core.campaign_types import (
    CompensatingAction,
    CompensatingActionType,
    SagaStep,
    SagaStepStatus,
)
from core.saga_coordinator import (
    CompensatingActionEngine,
    SagaCoordinator,
    SagaRollbackLog,
)


def test_saga_forward_step_recording_and_reverse_compensation():
    execution_order = []

    def make_handler(name):
        def handler(params):
            execution_order.append(name)
            return {"status": "ok"}
        return handler

    engine = CompensatingActionEngine(
        custom_handlers={
            "handler_1": make_handler("action_1"),
            "handler_2": make_handler("action_2"),
            "handler_3": make_handler("action_3"),
        }
    )
    coord = SagaCoordinator(engine=engine)

    # Record 3 forward steps
    act1 = CompensatingAction(action_id="a1", action_type=CompensatingActionType.CUSTOM_CALLBACK, target_id="handler_1")
    act2 = CompensatingAction(action_id="a2", action_type=CompensatingActionType.CUSTOM_CALLBACK, target_id="handler_2")
    act3 = CompensatingAction(action_id="a3", action_type=CompensatingActionType.CUSTOM_CALLBACK, target_id="handler_3")

    coord.record_forward_step("g1", "p1", {"out": 1}, [act1])
    coord.record_forward_step("g2", "p2", {"out": 2}, [act2])
    coord.record_forward_step("g3", "p3", {"out": 3}, [act3])

    assert len(coord.log.get_steps()) == 3

    # Trigger compensation: should run in reverse order (action_3, action_2, action_1)
    results = coord.compensate_all()
    assert len(results) == 3
    assert all(r["success"] for r in results)
    assert execution_order == ["action_3", "action_2", "action_1"]

    # Re-running compensation should be idempotent and skip already compensated steps
    execution_order.clear()
    results2 = coord.compensate_all()
    assert len(results2) == 0
    assert execution_order == []


def test_saga_tombstone_artifact_compensation():
    store = InMemoryArtifactStore()
    mgr = ArtifactManager(store=store)
    art = mgr.store_artifact(
        name="deliverable.py",
        content="def run(): pass",
        artifact_type=ArtifactType.CODE,
    )
    assert not art.taint_status

    engine = CompensatingActionEngine(artifact_manager=mgr)
    action = CompensatingAction(
        action_id="comp_tombstone",
        action_type=CompensatingActionType.TOMBSTONE_ARTIFACT,
        target_id=art.artifact_id,
        parameters={"reason": "Test rollback"},
    )
    res = engine.execute_action(action)
    assert res["success"] is True


def test_saga_release_locks_compensation():
    lock_mgr = MagicMock()
    lock_mgr.release_all_locks_for_goal.return_value = 2

    engine = CompensatingActionEngine(lock_manager=lock_mgr)
    action = CompensatingAction(
        action_id="comp_locks",
        action_type=CompensatingActionType.RELEASE_LOCKS,
        target_id="goal_test",
    )
    res = engine.execute_action(action)
    assert res["success"] is True
    assert res["released_locks_count"] == 2
    lock_mgr.release_all_locks_for_goal.assert_called_once_with("goal_test")


def test_saga_compensating_action_approval_security_gate():
    approval_gateway = MagicMock()
    approval_gateway.is_action_approved.return_value = False  # Not approved

    engine = CompensatingActionEngine(approval_gateway=approval_gateway)
    action = CompensatingAction(
        action_id="high_risk_act",
        action_type=CompensatingActionType.CUSTOM_CALLBACK,
        target_id="delete_db",
        requires_approval=True,
    )
    res = engine.execute_action(action)
    assert res["success"] is False
    assert "Operator approval required" in res["error"]


def test_saga_phase_specific_compensation():
    engine = CompensatingActionEngine(
        custom_handlers={"h_p1": lambda p: {"p": 1}, "h_p2": lambda p: {"p": 2}}
    )
    coord = SagaCoordinator(engine=engine)

    act1 = CompensatingAction(action_id="a1", action_type=CompensatingActionType.CUSTOM_CALLBACK, target_id="h_p1")
    act2 = CompensatingAction(action_id="a2", action_type=CompensatingActionType.CUSTOM_CALLBACK, target_id="h_p2")

    coord.record_forward_step("g1", "phase_1", {}, [act1])
    coord.record_forward_step("g2", "phase_2", {}, [act2])

    # Compensate only phase_1
    res = coord.compensate_phase("phase_1")
    assert len(res) == 1
    assert res[0]["phase_id"] == "phase_1"

    # Step for phase_2 remains forward_executed
    steps = coord.log.get_steps()
    assert steps[0].status == SagaStepStatus.COMPENSATED
    assert steps[1].status == SagaStepStatus.FORWARD_EXECUTED


def test_saga_serialization_and_deserialization():
    act = CompensatingAction(
        action_id="act_1",
        action_type=CompensatingActionType.RELEASE_LOCKS,
        target_id="goal_1",
    )
    coord = SagaCoordinator()
    coord.record_forward_step("g1", "p1", {"ok": True}, [act])

    d = coord.to_dict()
    restored = SagaCoordinator.from_dict(d)
    assert len(restored.log.get_steps()) == 1
    assert restored.log.get_steps()[0].goal_id == "g1"
