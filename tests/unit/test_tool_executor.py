import time

import pytest

from core.policy import Policy
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


def test_tool_executor_requires_tool_name():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    executor = ToolExecutor(registry)

    with pytest.raises(ValueError, match="Tool name cannot be empty"):
        executor.execute(
            tool_name="   ",
            tool_input="Hello AURA",
        )


def test_tool_executor_requires_tool_input():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    executor = ToolExecutor(registry)

    with pytest.raises(ValueError, match="Tool input cannot be empty"):
        executor.execute(
            tool_name="echo",
            tool_input="   ",
        )


def test_tool_executor_prepares_tool_input():
    class PreparingTool:
        def prepare_input(self, request: str) -> str:
            return request.replace("calculate ", "")

        def execute(self, input_data: str) -> str:
            return input_data

    registry = ToolRegistry()
    registry.register("calculator", PreparingTool())

    executor = ToolExecutor(registry)

    result = executor.prepare_input(
        tool_name="calculator",
        request="calculate 25 * 4",
    )

    assert result == "25 * 4"


def test_unauthorized_tool_is_rejected_before_execution():
    registry = ToolRegistry()
    executed = False

    class UnauthorizedTool:
        def execute(self, tool_input):
            nonlocal executed
            executed = True
            return "should not execute"

    registry.register("unauthorized", UnauthorizedTool())

    executor = ToolExecutor(
        registry=registry,
        policy=Policy(),
    )

    with pytest.raises(PermissionError):
        executor.execute("unauthorized", "test input")

    assert executed is False

def test_tool_execution_timeout_is_handled_safely():
    registry = ToolRegistry()

    class SlowTool:
        def execute(self, tool_input):
            time.sleep(0.2)
            return "finished"

    registry.register("calculator", SlowTool())

    executor = ToolExecutor(
        registry=registry,
        policy=Policy(),
    )

    with pytest.raises(TimeoutError):
        executor.execute("calculator", "test input", timeout=0.05)


def test_tool_execution_timeout_does_not_block_caller():
    registry = ToolRegistry()

    class VerySlowTool:
        def execute(self, tool_input):
            time.sleep(0.6)
            return "finished"

    registry.register("calculator", VerySlowTool())

    executor = ToolExecutor(
        registry=registry,
        policy=Policy(),
    )

    start_time = time.perf_counter()
    with pytest.raises(TimeoutError):
        executor.execute("calculator", "test input", timeout=0.05)
    elapsed = time.perf_counter() - start_time

    assert elapsed < 0.3, (
        f"Caller was blocked for {elapsed:.3f}s, expected < 0.3s"
    )
