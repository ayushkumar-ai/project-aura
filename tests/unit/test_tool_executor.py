from core.tool_registry import ToolRegistry
from interfaces.tool_executor import ToolExecutor
from tools.echo import EchoTool


def test_tool_executor_executes_registered_tool():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    executor = ToolExecutor(registry)

    result = executor.execute(
        tool_name="echo",
        tool_input="Hello AURA",
    )

    assert result == "Hello AURA"


import pytest

from core.tool_registry import ToolRegistry
from interfaces.tool_executor import ToolExecutor
from tools.echo import EchoTool


def test_tool_executor_executes_registered_tool():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    executor = ToolExecutor(registry)

    result = executor.execute(
        tool_name="echo",
        tool_input="Hello AURA",
    )

    assert result == "Hello AURA"


def test_tool_executor_raises_for_unknown_tool():
    registry = ToolRegistry()

    executor = ToolExecutor(registry)

    with pytest.raises(KeyError):
        executor.execute(
            tool_name="unknown",
            tool_input="Hello AURA",
        )


def test_tool_executor_rejects_empty_tool_input():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    executor = ToolExecutor(registry)

    with pytest.raises(ValueError):
        executor.execute(
            tool_name="echo",
            tool_input="",
        )


class FailingTool:
    def execute(self, input_data: str) -> str:
        raise RuntimeError("Tool failed")


def test_tool_executor_propagates_tool_execution_failure():
    registry = ToolRegistry()
    registry.register("failing", FailingTool())

    executor = ToolExecutor(registry)

    with pytest.raises(RuntimeError, match="Tool failed"):
        executor.execute(
            tool_name="failing",
            tool_input="Hello",
        )


def test_tool_executor_lists_available_tools():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    executor = ToolExecutor(registry)

    assert executor.list_tools() == ["echo"]


def test_tool_executor_rejects_whitespace_only_tool_input():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    executor = ToolExecutor(registry)

    with pytest.raises(ValueError):
        executor.execute(
            tool_name="echo",
            tool_input="   ",
        )


def test_tool_executor_rejects_empty_tool_name():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    executor = ToolExecutor(registry)

    with pytest.raises(ValueError):
        executor.execute(
            tool_name="",
            tool_input="Hello AURA",
        )
