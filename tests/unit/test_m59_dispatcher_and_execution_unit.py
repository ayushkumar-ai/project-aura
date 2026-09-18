"""M59 — Action Dispatcher & Execution Unit Tests."""

from unittest.mock import MagicMock
import pytest
from core.agent_mesh.dispatcher import ActionDispatcher
from core.agent_mesh.types import ActionType, PlanStep, AgentRole, AgentRun, AgentRunStatus
from core.platform.types import DeviceExecutionRecord, ExecutionStatus
from core.repositories.in_memory_cognitive_memory import InMemoryCognitiveMemoryRepository


class TestDispatcherAndExecutionUnit:
    def test_dispatch_tool_calculate_and_echo(self):
        # Invariant M59-F21
        dispatcher = ActionDispatcher()

        calc_step = PlanStep(
            step_number=1,
            action_type=ActionType.TOOL,
            action_name="calculate",
            parameters={"expression": "10 + 5 * 2"},
        )
        calc_res = dispatcher.dispatch("tenant_1", "run_101", calc_step)
        assert calc_res["action_name"] == "calculate"
        assert calc_res["result"]["result"] == 20
        assert calc_res["duration_ms"] >= 0.0

        echo_step = PlanStep(
            step_number=2,
            action_type=ActionType.TOOL,
            action_name="echo",
            parameters={"text": "Hello world with Bearer sk-1234567890abcdef1234567890"},
        )
        echo_res = dispatcher.dispatch("tenant_1", "run_101", echo_step)
        assert "sk-1234567890" not in echo_res["result"]["echo"]
        assert "[REDACTED_SECRET]" in echo_res["result"]["echo"]

    def test_dispatch_memory_record(self):
        # Invariant M59-F25
        mem_repo = InMemoryCognitiveMemoryRepository()
        dispatcher = ActionDispatcher(memory_repo=mem_repo)

        mem_step = PlanStep(
            step_number=1,
            action_type=ActionType.MEMORY,
            action_name="save_note",
            parameters={"content": "Important user constraint noted during run", "category": "run_notes"},
        )
        res = dispatcher.dispatch("tenant_1", "run_102", mem_step)
        assert res["result"]["status"] == "recorded"
        assert "memory_id" in res["result"]

        # Verify saved in repo
        mems = mem_repo.query_memories(tenant_id="tenant_1")
        assert len(mems) == 1
        assert mems[0].content == "Important user constraint noted during run"

    def test_dispatch_device_execution(self):
        # Invariant M59-F23
        mock_gateway = MagicMock()
        mock_record = DeviceExecutionRecord(
            device_id="dev_1",
            tenant_id="tenant_1",
            capability_name="get_system_metrics",
            status=ExecutionStatus.SUCCEEDED,
            result={"cpu": 12.5, "memory_free_mb": 4096},
        )
        mock_gateway.execute_action.return_value = mock_record

        dispatcher = ActionDispatcher(platform_gateway=mock_gateway)
        dev_step = PlanStep(
            step_number=1,
            action_type=ActionType.DEVICE,
            action_name="get_system_metrics",
            parameters={"device_id": "dev_1"},
        )
        res = dispatcher.dispatch("tenant_1", "run_103", dev_step)
        assert res["action_type"] == "device"
        assert res["result"]["status"] == "succeeded"
        mock_gateway.execute_action.assert_called_once()

    def test_dispatch_delegation(self):
        # Invariant M59-F35
        mock_coordinator = MagicMock()
        child_run = AgentRun(
            run_id="run_child_200",
            tenant_id="tenant_1",
            status=AgentRunStatus.RUNNING,
            intent="Subtask search",
        )
        mock_coordinator.delegate_task.return_value = child_run

        dispatcher = ActionDispatcher(mesh_coordinator=mock_coordinator)
        del_step = PlanStep(
            step_number=1,
            action_type=ActionType.DELEGATION,
            action_name="subtask_search",
            parameters={"role": "research", "intent": "Subtask search"},
        )
        res = dispatcher.dispatch("tenant_1", "run_parent_100", del_step)
        assert res["result"]["child_run_id"] == "run_child_200"
        assert res["result"]["status"] == "running"
        mock_coordinator.delegate_task.assert_called_once()
