"""Unit tests for M34 Tool & Action Ecosystem Subsystem."""

import pytest
from core.tool_ecosystem import (
    HttpMockTool,
    JsonQueryTool,
    SafeCalculatorTool,
    SystemInfoTool,
    TextTransformTool,
    ToolEcosystemRegistry,
)
from core.tool_ecosystem_types import (
    ToolExecutionRequest,
    ToolPermissionTier,
)


def test_calculator_tool_evaluation():
    calc = SafeCalculatorTool()
    res1 = calc.execute({"expression": "10 * 5 + 2"})
    assert res1["result"] == 52.0

    res2 = calc.execute({"expression": "(100 - 20) / 4"})
    assert res2["result"] == 20.0

    with pytest.raises(Exception):
        calc.execute({"expression": "import os; os.system('ls')"})


def test_text_transform_tool():
    tt = TextTransformTool()
    assert tt.execute({"text": "hello aura", "operation": "uppercase"})["result"] == "HELLO AURA"
    assert tt.execute({"text": "HELLO", "operation": "lowercase"})["result"] == "hello"
    assert tt.execute({"text": "  trim me  ", "operation": "trim"})["result"] == "trim me"
    assert tt.execute({"text": "abc", "operation": "reverse"})["result"] == "cba"
    assert tt.execute({"text": "one two three", "operation": "word_count"})["word_count"] == 3


def test_json_query_tool():
    jq = JsonQueryTool()
    data = {"user": {"name": "Eve", "settings": {"theme": "dark"}}}
    res = jq.execute({"data": data, "key": "user.settings.theme"})
    assert res["found"] is True
    assert res["value"] == "dark"

    res_missing = jq.execute({"data": data, "key": "user.nonexistent"})
    assert res_missing["found"] is False


def test_tool_registry_execution_and_audit():
    registry = ToolEcosystemRegistry()
    tools = registry.list_tools()
    assert len(tools) >= 5

    req = ToolExecutionRequest(
        tool_name="calculator",
        parameters={"expression": "7 * 8"},
        caller_role="agent",
    )
    result = registry.execute_tool(req)
    assert result.success is True
    assert result.output["result"] == 56.0

    # Audit log check
    audit = registry.get_audit_log(tool_name="calculator")
    assert len(audit) >= 1
    assert audit[-1].success is True
    assert audit[-1].tool_name == "calculator"


def test_tool_schema_validation_error():
    registry = ToolEcosystemRegistry()
    # Missing required 'expression' parameter
    req = ToolExecutionRequest(tool_name="calculator", parameters={})
    result = registry.execute_tool(req)
    assert result.success is False
    assert "Missing required parameter" in result.error
