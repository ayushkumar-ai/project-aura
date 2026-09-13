"""PROJECT AURA — Surgical Hardening Regression Test Suite.

Verifies:
1. Fail-closed policy enforcement across all subsystems (ToolEcosystem, DeviceEngine, StructuredPlanner, ToolExecutor, Orchestrator)
2. Elimination of dangerous simulated-success execution when missing executors
3. Concurrency and deadlock safety during simultaneous bidirectional cross-device synchronization
4. Multimodal and device reference boundaries and classification
"""

import concurrent.futures
import threading
import time
from uuid import uuid4

import pytest

from core.cross_device_sync_engine import CrossDeviceSyncEngine
from core.cross_device_types import SyncOperationType
from core.device_integration_engine import DeviceIntegrationEngine
from core.device_integration_types import DeviceActionRequest, DeviceCapability
from core.history import ConversationHistory
from core.models import AURARequest, AURAResponse
from core.multimodal_engine import MockMultimodalAdapter, MultimodalProcessor, ReferenceMultimodalAdapter
from core.orchestrator import Orchestrator
from core.policy import Policy, PolicyDecision
from core.structured_plan_types import PlanStepNode, PlanStepStatus
from core.structured_planner import StructuredPlanningEngine
from core.tool_ecosystem import ToolEcosystemRegistry
from core.tool_ecosystem_types import ToolExecutionRequest
from core.tool_registry import ToolRegistry
from interfaces.model import ModelInterface
from interfaces.tool_executor import ToolExecutor
from memory.in_memory import InMemoryStore
from tools.echo import EchoTool


# =========================================================================
# 1. Fail-Closed Policy Enforcement Tests
# =========================================================================

class ExplodingPolicy:
    """Policy mock that raises exceptions during evaluation."""
    def evaluate(self, request):
        raise RuntimeError("DB Connection crashed during policy check: secret_db_key_12345")

    def authorize_tool(self, tool_name):
        raise RuntimeError("Authorization engine unavailable: secret_ldap_cred")


class DenyingPolicy:
    """Policy mock that strictly denies everything."""
    def evaluate(self, request):
        return PolicyDecision.DENY

    def authorize_tool(self, tool_name):
        return PolicyDecision.DENY


class AllowingPolicy:
    """Policy mock that allows everything."""
    def evaluate(self, request):
        return PolicyDecision.ALLOW

    def authorize_tool(self, tool_name):
        return PolicyDecision.ALLOW


def test_tool_ecosystem_fail_closed_policy_exception():
    """ToolEcosystemRegistry MUST fail closed when policy evaluator raises an exception."""
    registry = ToolEcosystemRegistry(policy_engine=ExplodingPolicy())
    req = ToolExecutionRequest(tool_name="calculator", parameters={"expression": "10 + 5"})
    res = registry.execute_tool(req)
    assert res.success is False
    assert "Policy evaluation error" in res.error
    assert "secret_ldap_cred" not in res.error  # No sensitive info leaked in audit error


def test_tool_ecosystem_policy_deny():
    """ToolEcosystemRegistry MUST reject execution when policy returns DENY."""
    registry = ToolEcosystemRegistry(policy_engine=DenyingPolicy())
    req = ToolExecutionRequest(tool_name="calculator", parameters={"expression": "10 + 5"})
    res = registry.execute_tool(req)
    assert res.success is False
    assert "Policy denied" in res.error


def test_tool_ecosystem_policy_allow():
    """ToolEcosystemRegistry executes when policy returns ALLOW."""
    registry = ToolEcosystemRegistry(policy_engine=AllowingPolicy())
    req = ToolExecutionRequest(tool_name="calculator", parameters={"expression": "10 + 5"})
    res = registry.execute_tool(req)
    assert res.success is True
    assert res.output["result"] == 15.0


def test_device_engine_fail_closed_policy_exception():
    """DeviceIntegrationEngine MUST fail closed when policy evaluator raises an exception."""
    engine = DeviceIntegrationEngine(policy_engine=ExplodingPolicy())
    req = DeviceActionRequest(
        action_id="act_fail_closed_1",
        device_id="dev_desktop_primary",
        capability=DeviceCapability.SEND_NOTIFICATION,
        parameters={"message": "System Alert"},
    )
    res = engine.execute_action(req)
    assert res.success is False
    assert "Policy evaluation error" in res.error
    assert "secret_db_key" not in res.error


def test_device_engine_policy_deny():
    """DeviceIntegrationEngine MUST reject action when policy returns DENY."""
    engine = DeviceIntegrationEngine(policy_engine=DenyingPolicy())
    req = DeviceActionRequest(
        action_id="act_deny_1",
        device_id="dev_desktop_primary",
        capability=DeviceCapability.SEND_NOTIFICATION,
        parameters={"message": "System Alert"},
    )
    res = engine.execute_action(req)
    assert res.success is False
    assert "Policy denied" in res.error


def test_structured_planner_fail_closed_policy_exception():
    """StructuredPlanningEngine MUST fail step and plan when policy check raises an exception."""
    planner = StructuredPlanningEngine(policy_engine=ExplodingPolicy())
    step = PlanStepNode(
        step_id="step_calc",
        title="Calculate",
        description="Calculate metric",
        tool_name="calculator",
        parameters={"expression": "2 * 2"},
    )
    plan = planner.create_plan(goal="Calculate total", steps=[step])
    audit = planner.execute_plan(plan, tool_executor=ToolEcosystemRegistry())
    assert audit.is_success is False
    assert audit.steps_failed == 1
    assert "Policy evaluation error" in audit.execution_trace[0]["error"]


def test_tool_executor_fail_closed_policy_exception():
    """ToolExecutor MUST raise PermissionError when policy evaluation raises an exception."""
    reg = ToolRegistry()
    reg.register("echo", EchoTool())
    executor = ToolExecutor(registry=reg, policy=ExplodingPolicy())
    with pytest.raises(PermissionError, match="Policy evaluation error"):
        executor.execute(tool_name="echo", tool_input="hello")


def test_orchestrator_fail_closed_policy_exception():
    """Orchestrator MUST return controlled denial response when policy evaluation raises an exception."""
    class DummyModel(ModelInterface):
        def generate(self, prompt, request_id):
            return AURAResponse(request_id=request_id, content="Should not execute")

    orchestrator = Orchestrator(
        model=DummyModel(),
        policy=ExplodingPolicy(),
        memory=InMemoryStore(),
        history=ConversationHistory(),
    )
    resp = orchestrator.run(AURARequest(user_input="Test input"))
    assert resp.metadata.get("policy") == "deny"
    assert "denied by policy" in resp.content.lower()


# =========================================================================
# 2. Elimination of Dangerous Simulated-Success Tests
# =========================================================================

def test_structured_planner_fails_when_no_executor_configured():
    """StructuredPlanningEngine MUST NOT synthesize fake success when no executor is provided."""
    planner = StructuredPlanningEngine(default_tool_executor=None, policy_engine=AllowingPolicy())
    step = PlanStepNode(
        step_id="step_op",
        title="Important DB Write",
        description="Write records",
        tool_name="db_writer",
        parameters={},
    )
    plan = planner.create_plan(goal="Write DB", steps=[step])
    audit = planner.execute_plan(plan, tool_executor=None)
    assert audit.is_success is False
    assert audit.steps_completed == 0
    assert audit.steps_failed == 1
    assert step.status == PlanStepStatus.FAILED
    assert "No tool executor configured" in step.error


def test_structured_planner_succeeds_with_real_executor():
    """StructuredPlanningEngine marks success only when a real executor successfully executes."""
    ecosystem = ToolEcosystemRegistry()
    planner = StructuredPlanningEngine(default_tool_executor=ecosystem, policy_engine=AllowingPolicy())
    step = PlanStepNode(
        step_id="step_calc",
        title="Compute Values",
        description="Compute sum",
        tool_name="calculator",
        parameters={"expression": "50 + 50"},
    )
    plan = planner.create_plan(goal="Compute sum", steps=[step])
    audit = planner.execute_plan(plan)
    assert audit.is_success is True
    assert audit.steps_completed == 1
    assert audit.steps_failed == 0
    assert step.result["result"] == 100.0


# =========================================================================
# 3. Cross-Device Synchronization Concurrency & Deadlock Tests
# =========================================================================

def test_cross_device_sync_bidirectional_deadlock_free():
    """Verify simultaneous bidirectional sync_with_peer calls across threads cannot deadlock."""
    node_a = CrossDeviceSyncEngine(device_id="node_alpha")
    node_b = CrossDeviceSyncEngine(device_id="node_beta")

    # Seed deltas
    for i in range(20):
        node_a.generate_delta(
            operation=SyncOperationType.SET_PREFERENCE,
            entity_id=f"pref_alpha_{i}",
            payload={"key": f"val_a_{i}"},
        )
        node_b.generate_delta(
            operation=SyncOperationType.SET_PREFERENCE,
            entity_id=f"pref_beta_{i}",
            payload={"key": f"val_b_{i}"},
        )

    errors = []

    def sync_a_to_b():
        try:
            for _ in range(50):
                node_a.sync_with_peer(node_b)
                time.sleep(0.001)
        except Exception as e:
            errors.append(e)

    def sync_b_to_a():
        try:
            for _ in range(50):
                node_b.sync_with_peer(node_a)
                time.sleep(0.001)
        except Exception as e:
            errors.append(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(sync_a_to_b)
        f2 = executor.submit(sync_b_to_a)
        # Timeout after 5 seconds to ensure no deadlock occurs
        concurrent.futures.wait([f1, f2], timeout=5.0)

    assert len(errors) == 0
    assert f1.done() and f2.done()
    # Ensure vector clocks were merged
    assert "node_alpha" in node_b.get_status().vector_clock
    assert "node_beta" in node_a.get_status().vector_clock


# =========================================================================
# 4. Multimodal Reference Foundation Boundary Tests
# =========================================================================

def test_multimodal_reference_adapter_alias():
    """Verify ReferenceMultimodalAdapter alias and reference behavior."""
    assert ReferenceMultimodalAdapter is MockMultimodalAdapter
    proc = MultimodalProcessor(adapter=ReferenceMultimodalAdapter())
    block = proc.ingest_image(raw_bytes=b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", filename="test.png")
    from core.multimodal_types import MultimodalRequest
    res = proc.process_request(MultimodalRequest(request_id="mm_req_1", prompt="Test prompt", blocks=[block]))
    assert "image" in res.detected_modalities
    assert len(res.image_metadata) == 1
    assert res.image_metadata[0]["format"] == "png"
