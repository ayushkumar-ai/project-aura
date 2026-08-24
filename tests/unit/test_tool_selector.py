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


def test_tool_selector_can_describe_available_tools():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    selector = ToolSelector(registry)

    descriptions = selector.describe_tools()

    assert descriptions == {
        "echo": "Echo tool",
    }


def test_tool_selector_returns_empty_descriptions_for_empty_registry():
    registry = ToolRegistry()

    selector = ToolSelector(registry)

    assert selector.describe_tools() == {}


def test_tool_selector_uses_each_tool_description():
    class CalculatorTool:
        @property
        def description(self) -> str:
            return "Calculator tool"

        def execute(self, input_data: str) -> str:
            return "4"

    registry = ToolRegistry()
    registry.register("echo", EchoTool())
    registry.register("calculator", CalculatorTool())

    selector = ToolSelector(registry)

    assert selector.describe_tools() == {
        "echo": "Echo tool",
        "calculator": "Calculator tool",
    }


def test_tool_selector_selects_tool_from_natural_language():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    selector = ToolSelector(registry)

    result = selector.select("please echo this message")

    assert result == "echo"


def test_tool_selector_returns_none_when_no_tool_matches():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    selector = ToolSelector(registry)

    result = selector.select("tell me the weather")

    assert result is None