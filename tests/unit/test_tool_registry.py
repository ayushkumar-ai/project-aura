import pytest

from tools.echo import EchoTool
from core.tool_registry import ToolRegistry


def test_tool_registry_registers_and_retrieves_tool():
    registry = ToolRegistry()

    tool = EchoTool()

    registry.register("echo", tool)

    assert registry.get("echo") is tool


def test_tool_registry_raises_for_unknown_tool():
    registry = ToolRegistry()

    with pytest.raises(KeyError):
        registry.get("unknown")


def test_tool_registry_rejects_duplicate_tool_name():
    registry = ToolRegistry()

    registry.register("echo", EchoTool())

    with pytest.raises(ValueError):
        registry.register("echo", EchoTool())


def test_tool_registry_lists_registered_tools():
    from tools.echo import EchoTool

    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    assert registry.list_tools() == ["echo"]


def test_tool_registry_lists_multiple_registered_tools():
    from tools.echo import EchoTool

    registry = ToolRegistry()

    registry.register("echo", EchoTool())
    registry.register("echo2", EchoTool())

    assert registry.list_tools() == ["echo", "echo2"]
