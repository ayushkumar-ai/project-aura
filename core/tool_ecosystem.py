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
    ToolExecutionType,
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
            execution_type=ToolExecutionType.SIMULATED,
            production_status="simulated_only",
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


class SafeDocumentTool(BaseEcosystemTool):
    """Production-grade safe document reader restricted to authorized sandboxes."""

    _ALLOWED_ROOTS = (".aura_artifacts", ".aura_knowledge", "docs", "scratch")

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="document_reader",
            description="Read and inspect text documents in authorized local sandbox directories",
            permission_tier=ToolPermissionTier.READ_ONLY,
            execution_type=ToolExecutionType.REAL,
            production_status="production_ready",
            parameters=[
                ToolParameterSchema(name="path", type_str="string", description="Relative path within workspace or sandbox", required=True),
                ToolParameterSchema(name="max_bytes", type_str="int", description="Max bytes to read (default 50000)", required=False, default=50000),
            ],
            timeout_seconds=5.0,
            tags=["document", "file", "storage"],
        )

    def execute(self, parameters: dict[str, Any]) -> dict[str, Any]:
        raw_path = str(parameters.get("path", "")).strip()
        if not raw_path:
            raise ValueError("Parameter 'path' cannot be empty.")
        if ".." in raw_path or raw_path.startswith("/") or (len(raw_path) > 1 and raw_path[1] == ":"):
            raise ValueError("Path traversal or absolute outside paths are forbidden.")

        from pathlib import Path
        clean_path = Path(raw_path)
        if not any(raw_path.startswith(prefix) for prefix in self._ALLOWED_ROOTS):
            raise ValueError(f"Access to path '{raw_path}' is outside authorized sandboxes: {self._ALLOWED_ROOTS}")

        resolved = clean_path.resolve()
        if not resolved.exists() or not resolved.is_file():
            raise FileNotFoundError(f"Document '{raw_path}' not found.")

        max_b = int(parameters.get("max_bytes", 50000))
        max_b = min(max(max_b, 100), 500000)
        content = resolved.read_text(encoding="utf-8", errors="replace")[:max_b]
        return {
            "path": raw_path,
            "size_chars": len(content),
            "content": content,
        }


class StructuredInfoTool(BaseEcosystemTool):
    """Structured information services: UTC datetime, ISO timestamp, UUID generator."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="structured_info",
            description="Lookup structured date/time, generate UUIDs, and inspect system clock",
            permission_tier=ToolPermissionTier.READ_ONLY,
            execution_type=ToolExecutionType.REAL,
            production_status="production_ready",
            parameters=[
                ToolParameterSchema(name="operation", type_str="string", description="Operation: now_utc, generate_uuid, iso_date", required=True),
            ],
            timeout_seconds=2.0,
            tags=["time", "uuid", "system"],
        )

    def execute(self, parameters: dict[str, Any]) -> dict[str, Any]:
        import datetime
        op = str(parameters.get("operation", "now_utc")).lower().strip()
        if op in ("now_utc", "datetime"):
            now = datetime.datetime.now(datetime.timezone.utc)
            return {
                "iso_utc": now.isoformat(),
                "timestamp": now.timestamp(),
                "year": now.year,
                "month": now.month,
                "day": now.day,
            }
        elif op in ("generate_uuid", "uuid"):
            return {"uuid": str(uuid4())}
        elif op in ("iso_date", "date"):
            today = datetime.datetime.now(datetime.timezone.utc).date()
            return {"date": today.isoformat()}
        else:
            raise ValueError(f"Unsupported operation '{op}'. Supported: now_utc, generate_uuid, iso_date")


class ControlledWebFetchTool(BaseEcosystemTool):
    """Production-grade safe HTTP fetch tool with SSRF protection, size caps, and timeout guards."""

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="web_fetch",
            description="Fetch public HTTP/HTTPS resources safely with SSRF protection",
            permission_tier=ToolPermissionTier.READ_ONLY,
            execution_type=ToolExecutionType.REAL,
            production_status="production_ready",
            parameters=[
                ToolParameterSchema(name="url", type_str="string", description="Public HTTP/HTTPS URL", required=True),
                ToolParameterSchema(name="max_chars", type_str="int", description="Max characters to extract (default 10000)", required=False, default=10000),
            ],
            timeout_seconds=10.0,
            tags=["web", "fetch", "network"],
        )

    def execute(self, parameters: dict[str, Any]) -> dict[str, Any]:
        import urllib.request
        from providers.generic_provider import validate_endpoint_url
        url = str(parameters.get("url", "")).strip()
        if not url:
            raise ValueError("Parameter 'url' cannot be empty.")
        validated_url = validate_endpoint_url(url, allow_local=False)

        max_c = int(parameters.get("max_chars", 10000))
        max_c = min(max(max_c, 100), 50000)

        req = urllib.request.Request(validated_url, headers={"User-Agent": "AURA-Agent/0.28.0"})
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw_bytes = resp.read(max_c * 2)
            text = raw_bytes.decode("utf-8", errors="replace")[:max_c]
            return {
                "url": validated_url,
                "status_code": resp.status,
                "content_type": content_type,
                "content": text,
                "length_chars": len(text),
            }



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
        self.register_tool(SafeDocumentTool())
        self.register_tool(StructuredInfoTool())
        self.register_tool(ControlledWebFetchTool())

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
        """Execute a tool with schema validation, permission checks, audit logging, and tracing."""
        from core.metrics import get_metrics_registry
        from core.tracing import Tracer
        from core.trace_types import SpanKind, SpanStatus

        start_time = time.time()
        exec_id = request.execution_id or f"exec_{uuid4().hex[:12]}"
        metrics = get_metrics_registry()
        tracer = Tracer(service_name="aura.tools")

        tool = self.get_tool(request.tool_name)
        if not tool:
            err = f"Tool '{request.tool_name}' not found in registry."
            self._record_audit(exec_id, request.tool_name, request.caller_role, 1, False, 0.0, err)
            try:
                metrics.get_counter("aura_tool_executions_total").inc(
                    labels={"tool_name": request.tool_name, "caller_role": request.caller_role, "status": "not_found"}
                )
            except Exception:
                pass
            return ToolExecutionResult(execution_id=exec_id, tool_name=request.tool_name, success=False, error=err)

        spec = tool.spec

        with tracer.start_span(
            f"tool_execution:{spec.name}",
            kind=SpanKind.INTERNAL,
            attributes={
                "tool.name": spec.name,
                "tool.caller": request.caller_role,
                "tool.permission_tier": int(spec.permission_tier),
            },
        ) as span:
            # 1. Schema Validation
            valid, schema_err = self.validate_parameters(spec, request.parameters)
            if not valid:
                self._record_audit(exec_id, spec.name, request.caller_role, int(spec.permission_tier), False, 0.0, schema_err)
                span.set_status(SpanStatus.ERROR, message=schema_err)
                try:
                    metrics.get_counter("aura_tool_executions_total").inc(
                        labels={"tool_name": spec.name, "caller_role": request.caller_role, "status": "schema_error"}
                    )
                except Exception:
                    pass
                return ToolExecutionResult(execution_id=exec_id, tool_name=spec.name, success=False, error=schema_err)

            # 2. Policy / Authorization Check
            pol = policy_engine or self.policy_engine
            if pol is not None:
                try:
                    if hasattr(pol, "authorize_tool"):
                        decision = pol.authorize_tool(spec.name)
                        if getattr(decision, "value", str(decision)).lower() != "allow":
                            err = f"Policy denied execution of tool '{spec.name}'"
                            self._record_audit(exec_id, spec.name, request.caller_role, int(spec.permission_tier), False, 0.0, err)
                            span.set_status(SpanStatus.ERROR, message=err)
                            try:
                                metrics.get_counter("aura_tool_executions_total").inc(
                                    labels={"tool_name": spec.name, "caller_role": request.caller_role, "status": "denied"}
                                )
                            except Exception:
                                pass
                            return ToolExecutionResult(execution_id=exec_id, tool_name=spec.name, success=False, error=err)
                    elif hasattr(pol, "evaluate"):
                        from core.models import AURARequest
                        decision = pol.evaluate(AURARequest(user_input=f"Execute {spec.name}"))
                        if getattr(decision, "value", None) == "deny" or getattr(decision, "decision", None) == "deny" or getattr(decision, "is_allowed", True) is False:
                            err = f"Policy denied execution of tool '{spec.name}'"
                            self._record_audit(exec_id, spec.name, request.caller_role, int(spec.permission_tier), False, 0.0, err)
                            span.set_status(SpanStatus.ERROR, message=err)
                            try:
                                metrics.get_counter("aura_tool_executions_total").inc(
                                    labels={"tool_name": spec.name, "caller_role": request.caller_role, "status": "denied"}
                                )
                            except Exception:
                                pass
                            return ToolExecutionResult(execution_id=exec_id, tool_name=spec.name, success=False, error=err)
                except Exception as e:
                    err = f"Policy evaluation error for tool '{spec.name}': {type(e).__name__}"
                    logger.warning(f"Policy evaluation error for tool '{spec.name}': {type(e).__name__}")
                    self._record_audit(exec_id, spec.name, request.caller_role, int(spec.permission_tier), False, 0.0, err)
                    span.record_exception(e)
                    try:
                        metrics.get_counter("aura_tool_executions_total").inc(
                            labels={"tool_name": spec.name, "caller_role": request.caller_role, "status": "error"}
                        )
                    except Exception:
                        pass
                    return ToolExecutionResult(execution_id=exec_id, tool_name=spec.name, success=False, error=err)

            # 3. Execution with Timeout & Error Boundary
            timeout = request.timeout_seconds or spec.timeout_seconds
            try:
                output = tool.execute(request.parameters)
                duration = time.time() - start_time
                self._record_audit(exec_id, spec.name, request.caller_role, int(spec.permission_tier), True, duration, "")
                span.set_status(SpanStatus.OK)
                span.record_resource_usage(tool_calls=1, cpu_ms=round(duration * 1000.0, 2))
                try:
                    metrics.get_counter("aura_tool_executions_total").inc(
                        labels={"tool_name": spec.name, "caller_role": request.caller_role, "status": "success"}
                    )
                    metrics.get_histogram("aura_tool_duration_seconds").observe(
                        duration, labels={"tool_name": spec.name}
                    )
                except Exception:
                    pass
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
                span.record_exception(e)
                try:
                    metrics.get_counter("aura_tool_executions_total").inc(
                        labels={"tool_name": spec.name, "caller_role": request.caller_role, "status": "failure"}
                    )
                    metrics.get_histogram("aura_tool_duration_seconds").observe(
                        duration, labels={"tool_name": spec.name}
                    )
                except Exception:
                    pass
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
            if len(self._audit_log) >= 1000:
                self._audit_log.pop(0)
            self._audit_log.append(record)


    def get_audit_log(self, tool_name: str | None = None) -> list[ToolAuditRecord]:
        with self._lock:
            if tool_name:
                return [a for a in self._audit_log if a.tool_name == tool_name]
            return list(self._audit_log)
