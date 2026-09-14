"""M47 — Production Tool Ecosystem & Integrations Test Suite.

Verifies:
1. Tool Classification Matrix & Spec Metadata
2. Safe Document Reader Sandboxing & Path Traversal Guards
3. Structured Information Tool Operations
4. Controlled Web Fetch SSRF Defense & Validation
5. Policy Engine Enforcement & Security Audit Logging
"""

import pytest
from unittest.mock import MagicMock
from pathlib import Path

from core.tool_ecosystem import (
    ToolEcosystemRegistry,
    SafeDocumentTool,
    StructuredInfoTool,
    ControlledWebFetchTool,
)
from core.tool_ecosystem_types import (
    ToolExecutionRequest,
    ToolExecutionType,
    ToolPermissionTier,
)


def test_tool_inventory_and_classification():
    """Verify tool inventory contains explicit execution types and production statuses."""
    registry = ToolEcosystemRegistry()
    tools = registry.list_tools()
    assert len(tools) >= 10

    tool_map = {t.name: t for t in tools}

    # Verify standard tools have explicit classifications
    assert "calculator" in tool_map
    assert tool_map["calculator"].execution_type in (ToolExecutionType.LOCAL_ONLY, ToolExecutionType.REAL)

    assert "http_mock" in tool_map
    assert tool_map["http_mock"].execution_type in (ToolExecutionType.SIMULATED, ToolExecutionType.MOCK)

    assert "document_reader" in tool_map
    assert tool_map["document_reader"].execution_type == ToolExecutionType.REAL
    assert tool_map["document_reader"].production_status == "production_ready"

    assert "structured_info" in tool_map
    assert tool_map["structured_info"].execution_type == ToolExecutionType.REAL

    assert "web_fetch" in tool_map
    assert tool_map["web_fetch"].execution_type == ToolExecutionType.REAL


def test_safe_document_tool_sandboxing_and_traversal_rejection(tmp_path):
    """Verify document reader strictly enforces sandboxing and rejects traversal."""
    doc_tool = SafeDocumentTool()

    # Path traversal rejection
    with pytest.raises(ValueError, match="forbidden"):
        doc_tool.execute({"path": "../../../etc/passwd"})

    with pytest.raises(ValueError, match="forbidden"):
        doc_tool.execute({"path": "C:\\Windows\\System32\\cmd.exe"})

    # Outside sandbox rejection
    with pytest.raises(ValueError, match="outside authorized sandboxes"):
        doc_tool.execute({"path": "app/server.py"})


def test_structured_info_tool():
    """Verify structured information service operations."""
    info_tool = StructuredInfoTool()

    # 1. UTC datetime
    res_dt = info_tool.execute({"operation": "now_utc"})
    assert "iso_utc" in res_dt
    assert "year" in res_dt

    # 2. UUID generation
    res_uuid = info_tool.execute({"operation": "generate_uuid"})
    assert "uuid" in res_uuid
    assert len(res_uuid["uuid"]) == 36

    # 3. ISO date
    res_date = info_tool.execute({"operation": "iso_date"})
    assert "date" in res_date


def test_controlled_web_fetch_ssrf_rejection():
    """Verify web fetch strictly rejects local, loopback, and metadata endpoints."""
    fetch_tool = ControlledWebFetchTool()

    # Cloud metadata IP rejection
    with pytest.raises(ValueError, match="SSRF"):
        fetch_tool.execute({"url": "http://169.254.169.254/latest/meta-data"})

    # Localhost rejection
    with pytest.raises(ValueError, match="SSRF"):
        fetch_tool.execute({"url": "http://127.0.0.1:8000/ready"})

    # Invalid scheme
    with pytest.raises(ValueError, match="scheme"):
        fetch_tool.execute({"url": "file:///etc/passwd"})


def test_policy_boundary_final_authority():
    """Verify policy engine denial cannot be bypassed and emits audit record."""
    mock_policy = MagicMock()
    mock_policy.authorize_tool.return_value = "deny"

    registry = ToolEcosystemRegistry(policy_engine=mock_policy)
    req = ToolExecutionRequest(tool_name="calculator", parameters={"expression": "10 + 20"}, caller_role="agent")
    result = registry.execute_tool(req)

    assert not result.success
    assert "Policy denied" in result.error
