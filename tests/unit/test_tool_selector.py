import pytest

from core.tool_registry import ToolRegistry
from interfaces.tool_selector import ToolSelector
from tools.echo import EchoTool


def test_tool_selector_selects_registered_tool():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    selector = ToolSelector(registry)

    assert selector.select("echo") == "echo"


def test_tool_selector_rejects_unknown_tool():
    registry = ToolRegistry()

    selector = ToolSelector(registry)

    with pytest.raises(KeyError):
        selector.select("unknown")


def test_tool_selector_rejects_empty_request():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    selector = ToolSelector(registry)

    with pytest.raises(ValueError, match="Request cannot be empty"):
        selector.select("")


def test_tool_selector_rejects_whitespace_request():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    selector = ToolSelector(registry)

    with pytest.raises(ValueError, match="Request cannot be empty"):
        selector.select("   ")


def test_tool_selector_selects_matching_registered_tool():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())
    registry.register("echo2", EchoTool())

    selector = ToolSelector(registry)

    assert selector.select("echo") == "echo"


def test_tool_selector_lists_available_tools():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())
    registry.register("echo2", EchoTool())

    selector = ToolSelector(registry)

    assert selector.list_tools() == ["echo", "echo2"]


def test_tool_selector_rejects_request_for_unavailable_tool():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    selector = ToolSelector(registry)

    with pytest.raises(KeyError):
        selector.select("calculator")


def test_tool_selector_can_be_constructed_with_registry():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    selector = ToolSelector(registry)

    assert selector.registry is registry


def test_tool_selector_returns_registered_tool_name_without_executing_it():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    selector = ToolSelector(registry)

    result = selector.select("echo")

    assert result == "echo"
