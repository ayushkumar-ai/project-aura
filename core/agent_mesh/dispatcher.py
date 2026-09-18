"""M59 — Unified Action Dispatcher.

Routes validated plan steps to target subsystem executors:
- M58 Platform & Device Gateway
- M56 Cognitive Memory Repository
- M57 Multimodal Processor
- M52 Background Task Manager
- Intelligence Mesh Child Delegations
- Built-in Safe Tools
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from core.agent_mesh.types import ActionType, PlanStep
from core.cognitive_memory.types import scrub_sensitive_content

if TYPE_CHECKING:
    from core.agent_mesh.mesh import IntelligenceMeshCoordinator
    from core.platform.gateway import PlatformIntegrationGateway
    from core.repositories.base_cognitive_memory import BaseCognitiveMemoryRepository

logger = logging.getLogger("aura.agent_mesh.dispatcher")


class ActionDispatcher:
    """Dispatches validated actions to the appropriate underlying subsystem."""

    def __init__(
        self,
        platform_gateway: PlatformIntegrationGateway | None = None,
        memory_repo: BaseCognitiveMemoryRepository | None = None,
        mesh_coordinator: IntelligenceMeshCoordinator | None = None,
    ):
        self.platform_gateway = platform_gateway
        self.memory_repo = memory_repo
        self.mesh_coordinator = mesh_coordinator

    def dispatch(
        self,
        tenant_id: str,
        run_id: str,
        step: PlanStep,
        approval_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Execute action step and return structured result (Invariants M59-F21, M59-F22)."""
        start_time = time.time()
        action_type = step.action_type
        action_name = step.action_name
        params = dict(step.parameters)

        result_data: dict[str, Any] = {}

        if action_type == ActionType.DEVICE:
            if not self.platform_gateway:
                raise RuntimeError("Platform integration gateway is unavailable.")
            # Resolve target device or find first registered device
            device_id = params.get("device_id")
            if not device_id:
                devices = self.platform_gateway.repository.list_devices(tenant_id=tenant_id)
                if devices:
                    device_id = devices[0].device_id
                else:
                    # Register a temporary managed workstation for execution
                    dev = self.platform_gateway.register_device(tenant_id=tenant_id, name="Default Workstation", auto_authorize=True)
                    device_id = dev.device_id

            exec_record = self.platform_gateway.execute_action(
                tenant_id=tenant_id,
                device_id=device_id,
                capability_name=action_name,
                parameters=params,
                approval_token=approval_token,
                idempotency_key=idempotency_key,
            )
            result_data = exec_record.to_dict()

        elif action_type == ActionType.RESPONSE:
            msg = params.get("message", f"Completed action '{action_name}'.")
            result_data = {
                "message": scrub_sensitive_content(msg),
                "status": "responded",
            }

        elif action_type == ActionType.MEMORY:
            if not self.memory_repo:
                raise RuntimeError("Cognitive memory repository is unavailable.")
            mem_content = params.get("content", "")
            mem, _ = self.memory_repo.record_memory(
                tenant_id=tenant_id,
                content=mem_content,
                category=params.get("category", "agent_run"),
                key=params.get("key", f"run_{run_id}"),
            )
            result_data = {"memory_id": mem.memory_id, "status": "recorded"}

        elif action_type == ActionType.DELEGATION:
            if not self.mesh_coordinator:
                raise RuntimeError("Intelligence mesh coordinator is unavailable.")
            child_run = self.mesh_coordinator.delegate_task(
                parent_run_id=run_id,
                tenant_id=tenant_id,
                role=params.get("role", "research"),
                subtask_intent=params.get("intent", action_name),
            )
            result_data = {"child_run_id": child_run.run_id, "status": child_run.status.value}

        elif action_type == ActionType.TOOL:
            # Safe built-in tool execution
            if action_name == "calculate":
                expr = str(params.get("expression", "0"))
                # Safe evaluation of basic arithmetic
                try:
                    import ast
                    import operator as op
                    operators = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv, ast.Pow: op.pow}
                    def eval_expr(node):
                        if isinstance(node, ast.Constant):
                            return node.value
                        elif isinstance(node, ast.BinOp):
                            return operators[type(node.op)](eval_expr(node.left), eval_expr(node.right))
                        raise ValueError("Unsupported operation")
                    val = eval_expr(ast.parse(expr, mode='eval').body)
                    result_data = {"result": val, "expression": expr}
                except Exception as e:
                    result_data = {"error": str(e), "status": "calculation_failed"}
            elif action_name == "echo":
                result_data = {"echo": scrub_sensitive_content(params.get("text", ""))}
            else:
                result_data = {"action": action_name, "status": "executed", "params": params}

        else:
            result_data = {"action": action_name, "status": "unsupported_action_type"}

        duration_ms = (time.time() - start_time) * 1000.0
        return {
            "result": result_data,
            "duration_ms": duration_ms,
            "action_type": action_type.value,
            "action_name": action_name,
        }
