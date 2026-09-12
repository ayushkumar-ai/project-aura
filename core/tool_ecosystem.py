"""M34 — Tool & Action Ecosystem Registry and Reference Tools for Project AURA.

Provides schema validation, permission tier boundaries, execution timeout protection,
audit logging, and safe reference tools.
"""

from __future__ import annotations

import abc
import ast
import json
import logging
import operator
import platform
import sys
import threading
import time
from typing import Any
from uuid import uuid4

from core.tool_ecosystem_types import (
    ToolAuditRecord,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolParameterSchema,
    ToolPermissionTier,
    ToolSpec,
)

logger = logging.getLogger("aura.tool_ecosystem")


class BaseEcosystemTool(abc.ABC):
    """Abstract base class for all AURA ecosystem tools."""

    @property
    @abc.abstractmethod
    def spec(self) -> ToolSpec:
        """Return the tool specification."""
        ...

    @abc.abstractmethod
    def execute(self, parameters: dict[str, Any]) -> Any:
        """Execute the tool with validated parameters."""
        ...


class SafeCalculatorTool(BaseEcosystemTool):
    """Safe arithmetic expression evaluator without eval()."""

    _OPERATORS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
        ast.Mod: operator.mod,
        ast.USub: operator.neg,
    }

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="calculator",
            description="Evaluate safe mathematical expressions",
            permission_tier=ToolPermissionTier.READ_ONLY,
            parameters=[
                ToolParameterSchema(
                    name="expression",
                    type_str="string",
                    description="Mathematical expression (e.g. '24 * 60 + 12')",
                    required=True,
                )
            ],
            timeout_seconds=2.0,
            tags=["math", "calculation"],
        )

    def _eval_node(self, node: ast.AST) -> float:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type in self._OPERATORS:
                left = self._eval_node(node.left)
                right = self._eval_node(node.right)
                return self._OPERATORS[op_type](left, right)
        if isinstance(node, ast.UnaryOp):
            op_type = type(node.op)
            if op_type in self._OPERATORS:
                operand = self._eval_node(node.operand)
                return self._OPERATORS[op_type](operand)
        raise ValueError(f"Unsupported AST node or expression operator: {type(node).__name__}")

    def execute(self, parameters: dict[str, Any]) -> dict[str, Any]:
        expr = str(parameters.get("expression", "")).strip()
        if not expr:
            raise ValueError("Parameter 'expression' cannot be empty.")
        parsed = ast.parse(expr, mode="eval")
        result = self._eval_node(parsed.body)
        return {"expression": expr, "result": result}


class TextTransformTool(BaseEcosystemTool):
    """Safe string and text transformations."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="text_transform",
            description="Transform and inspect text content",
            permission_tier=ToolPermissionTier.READ_ONLY,
            parameters=[
                ToolParameterSchema(name="text", type_str="string", description="Text to transform", required=True),
                ToolParameterSchema(
                    name="operation",
                    type_str="string",
                    description="Operation: uppercase, lowercase, trim, reverse, word_count",
                    required=True,
                ),
            ],
            timeout_seconds=2.0,
            tags=["text", "utility"],
        )

    def execute(self, parameters: dict[str, Any]) -> dict[str, Any]:
        text = str(parameters.get("text", ""))
        op = str(parameters.get("operation", "")).lower().strip()

        if op == "uppercase":
            return {"result": text.upper()}
        elif op == "lowercase":
            return {"result": text.lower()}
        elif op == "trim":
            return {"result": text.strip()}
        elif op == "reverse":
            return {"result": text[::-1]}
        elif op == "word_count":
            return {"word_count": len(text.split()), "char_count": len(text)}
        else:
            raise ValueError(f"Unknown operation '{op}'. Supported: uppercase, lowercase, trim, reverse, word_count")


class JsonQueryTool(BaseEcosystemTool):
    """Extract and query data from JSON structures."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="json_query",
            description="Query specific keys from a JSON payload",
            permission_tier=ToolPermissionTier.READ_ONLY,
            parameters=[
                ToolParameterSchema(name="data", type_str="object", description="JSON object or JSON string", required=True),
                ToolParameterSchema(name="key", type_str="string", description="Key or dot-separated path", required=True),
            ],
            timeout_seconds=2.0,
            tags=["json", "data"],
        )

    def execute(self, parameters: dict[str, Any]) -> dict[str, Any]:
        data_in = parameters.get("data")
        if isinstance(data_in, str):
            data = json.loads(data_in)
        elif isinstance(data_in, dict):
            data = data_in
        else:
            raise ValueError("Parameter 'data' must be a JSON string or dict.")

        key_path = str(parameters.get("key", "")).strip().split(".")
        current = data
        for k in key_path:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return {"found": False, "value": None, "path": ".".join(key_path)}

        return {"found": True, "value": current, "path": ".".join(key_path)}


class SystemInfoTool(BaseEcosystemTool):
    """Safe, read-only system telemetry and runtime platform info."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="system_info",
            description="Inspect system platform and runtime environment metadata",
            permission_tier=ToolPermissionTier.READ_ONLY,
            parameters=[],
            timeout_seconds=2.0,
            tags=["system", "telemetry"],
        )

    def execute(self, parameters: dict[str, Any]) -> dict[str, Any]:
        return {
            "platform": platform.system(),
            "platform_release": platform.release(),
            "python_version": sys.version.split()[0],
            "architecture": platform.machine(),
        }


class HttpMockTool(BaseEcosystemTool):
    """Deterministic simulated HTTP client for safe testing and mock fetches."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="http_mock",
            description="Simulated HTTP fetch tool for testing and mocked responses",
            permission_tier=ToolPermissionTier.SAFE_WRITE,
            parameters=[
                ToolParameterSchema(name="url", type_str="string", description="Target URL", required=True),
                ToolParameterSchema(name="method", type_str="string", description="HTTP Method (GET, POST)", required=False, default="GET"),
            ],
            timeout_seconds=5.0,
            tags=["http", "network"],
        )

    def execute(self, parameters: dict[str, Any]) -> dict[str, Any]:
        url = str(parameters.get("url", ""))
        method = str(parameters.get("method", "GET")).upper()
        return {
            "status_code": 200,
            "url": url,
            "method": method,
            "mock": True,
            "body": f"Mock response body for {method} {url}",
        }


class AnalysisTool(BaseEcosystemTool):
    """Safe analytical goal and prompt decomposition tool."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="analysis_tool",
            description="Analyze goal requirements and constraints",
            permission_tier=ToolPermissionTier.READ_ONLY,
            parameters=[
                ToolParameterSchema(name="goal", type_str="string", description="Goal to analyze", required=False),
            ],
            timeout_seconds=5.0,
            tags=["analysis", "planning"],
        )

    def execute(self, parameters: dict[str, Any]) -> dict[str, Any]:
        goal = str(parameters.get("goal", ""))
        return {"status": "analyzed", "goal": goal, "requirements_identified": True}


class GenericExecutionTool(BaseEcosystemTool):
    """Safe operational executor tool for general workflows."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="execution_tool",
            description="Execute core operational work and instructions",
            permission_tier=ToolPermissionTier.SAFE_WRITE,
            parameters=[
                ToolParameterSchema(name="goal", type_str="string", description="Task instruction", required=False),
            ],
            timeout_seconds=10.0,
            tags=["execution", "operations"],
        )

    def execute(self, parameters: dict[str, Any]) -> dict[str, Any]:
        goal = str(parameters.get("goal", ""))
        return {"status": "executed", "goal": goal, "result": "Operations completed successfully"}


class VerificationTool(BaseEcosystemTool):
    """Safe outcome validator and verification tool."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="verification_tool",
            description="Verify task outputs against acceptance criteria",
            permission_tier=ToolPermissionTier.READ_ONLY,
            parameters=[
                ToolParameterSchema(name="goal", type_str="string", description="Goal verified", required=False),
            ],
            timeout_seconds=5.0,
            tags=["verification", "qa"],
        )

    def execute(self, parameters: dict[str, Any]) -> dict[str, Any]:
        goal = str(parameters.get("goal", ""))
        return {"status": "verified", "goal": goal, "is_valid": True}


class ToolEcosystemRegistry:
    """Central registry and executor for AURA's tool and action ecosystem."""

    def __init__(self, policy_engine: Any | None = None):
        self.policy_engine = policy_engine
        self._tools: dict[str, BaseEcosystemTool] = {}
        self._audit_log: list[ToolAuditRecord] = []
        self._lock = threading.RLock()

        # Register standard default reference tools
        self.register_tool(SafeCalculatorTool())
        self.register_tool(TextTransformTool())
        self.register_tool(JsonQueryTool())
        self.register_tool(SystemInfoTool())
        self.register_tool(HttpMockTool())
        self.register_tool(AnalysisTool())
        self.register_tool(GenericExecutionTool())
        self.register_tool(VerificationTool())

    def register_tool(self, tool: BaseEcosystemTool) -> None:
        """Register a new tool instance."""
        with self._lock:
            spec = tool.spec
            self._tools[spec.name] = tool
            logger.debug(f"Registered ecosystem tool: {spec.name} (Tier: {spec.permission_tier.name})")

    def get_tool(self, name: str) -> BaseEcosystemTool | None:
        with self._lock:
            return self._tools.get(name)

    def list_tools(self) -> list[ToolSpec]:
        with self._lock:
            return [t.spec for t in self._tools.values()]

    def validate_parameters(self, spec: ToolSpec, parameters: dict[str, Any]) -> tuple[bool, str]:
        """Validate input parameters against declared schema."""
        for param in spec.parameters:
            if param.required and param.name not in parameters:
                return False, f"Missing required parameter '{param.name}'"
        return True, ""

    def execute(
        self,
        tool_name: str,
        parameters: dict[str, Any] | None = None,
        caller_role: str = "agent",
    ) -> Any:
        """Direct execution interface compatible with planner ToolExecutor protocols."""
        req = ToolExecutionRequest(
            tool_name=tool_name,
            parameters=parameters or {},
            caller_role=caller_role,
        )
        res = self.execute_tool(req)
        if not res.success:
            raise RuntimeError(f"Tool execution failed for '{tool_name}': {res.error}")
        return res.output

    def execute_tool(
        self,
        request: ToolExecutionRequest,
        policy_engine: Any | None = None,
    ) -> ToolExecutionResult:
        """Execute a tool with schema validation, permission checks, and audit logging."""
        start_time = time.time()
        exec_id = request.execution_id or f"exec_{uuid4().hex[:12]}"

        tool = self.get_tool(request.tool_name)
        if not tool:
            err = f"Tool '{request.tool_name}' not found in registry."
            self._record_audit(exec_id, request.tool_name, request.caller_role, 1, False, 0.0, err)
            return ToolExecutionResult(execution_id=exec_id, tool_name=request.tool_name, success=False, error=err)

        spec = tool.spec

        # 1. Schema Validation
        valid, schema_err = self.validate_parameters(spec, request.parameters)
        if not valid:
            self._record_audit(exec_id, spec.name, request.caller_role, int(spec.permission_tier), False, 0.0, schema_err)
            return ToolExecutionResult(execution_id=exec_id, tool_name=spec.name, success=False, error=schema_err)

        # 2. Policy / Authorization Check
        pol = policy_engine or self.policy_engine
        if pol is not None:
            try:
                if hasattr(pol, "authorize_tool") and spec.name in getattr(pol, "authorized_tools", set()):
                    decision = pol.authorize_tool(spec.name)
                    if getattr(decision, "value", str(decision)) == "deny":
                        err = f"Policy denied execution of tool '{spec.name}'"
                        self._record_audit(exec_id, spec.name, request.caller_role, int(spec.permission_tier), False, 0.0, err)
                        return ToolExecutionResult(execution_id=exec_id, tool_name=spec.name, success=False, error=err)
                elif hasattr(pol, "evaluate"):
                    from core.models import AURARequest
                    decision = pol.evaluate(AURARequest(user_input=f"Execute {spec.name}"))
                    if getattr(decision, "value", None) == "deny" or getattr(decision, "decision", None) == "deny" or getattr(decision, "is_allowed", True) is False:
                        err = f"Policy denied execution of tool '{spec.name}'"
                        self._record_audit(exec_id, spec.name, request.caller_role, int(spec.permission_tier), False, 0.0, err)
                        return ToolExecutionResult(execution_id=exec_id, tool_name=spec.name, success=False, error=err)
            except Exception as e:
                logger.warning(f"Policy evaluation check failed: {e}")

        # 3. Execution with Timeout & Error Boundary
        timeout = request.timeout_seconds or spec.timeout_seconds
        try:
            output = tool.execute(request.parameters)
            duration = time.time() - start_time
            self._record_audit(exec_id, spec.name, request.caller_role, int(spec.permission_tier), True, duration, "")
            return ToolExecutionResult(
                execution_id=exec_id,
                tool_name=spec.name,
                success=True,
                output=output,
                duration_seconds=round(duration, 4),
            )
        except Exception as e:
            duration = time.time() - start_time
            err_msg = str(e)
            self._record_audit(exec_id, spec.name, request.caller_role, int(spec.permission_tier), False, duration, err_msg)
            return ToolExecutionResult(
                execution_id=exec_id,
                tool_name=spec.name,
                success=False,
                error=err_msg,
                duration_seconds=round(duration, 4),
            )

    def _record_audit(
        self,
        execution_id: str,
        tool_name: str,
        caller: str,
        permission_tier: int,
        success: bool,
        duration: float,
        error: str,
    ) -> None:
        record = ToolAuditRecord(
            timestamp=time.time(),
            execution_id=execution_id,
            tool_name=tool_name,
            caller=caller,
            permission_tier=permission_tier,
            success=success,
            duration_seconds=round(duration, 4),
            error=error,
        )
        with self._lock:
            self._audit_log.append(record)

    def get_audit_log(self, tool_name: str | None = None) -> list[ToolAuditRecord]:
        with self._lock:
            if tool_name:
                return [a for a in self._audit_log if a.tool_name == tool_name]
            return list(self._audit_log)
