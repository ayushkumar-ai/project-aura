"""Deterministic M6 capability evaluation for the existing AURA runtime."""

import time

import pytest

from core.history import ConversationHistory
from core.models import AURARequest
from core.orchestrator import Orchestrator
from core.policy import Policy
from core.tool_registry import ToolRegistry
from interfaces.tool_executor import ToolExecutor
from interfaces.tool_selector import ToolSelector
from knowledge.in_memory import InMemoryKnowledgeStore, KnowledgeRecord
from memory.in_memory import InMemoryStore
from providers.fake_model import FakeModelProvider
from tools.calculator import CalculatorTool
from tools.echo import EchoTool


def build_runtime(*, memory=None, history=None, knowledge=None, registry=None):
    registry = registry or ToolRegistry()
    selector = ToolSelector(registry)
    executor = ToolExecutor(registry, policy=Policy())

    return Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        memory=memory,
        history=history,
        knowledge=knowledge,
        tool_registry=registry,
        tool_selector=selector,
        tool_executor=executor,
    )


@pytest.mark.parametrize(
    ("case", "history_turns", "memory_value", "aura_request", "expected_content"),
    [
        (
            "baseline",
            [],
            None,
            AURARequest(user_input="Hello AURA"),
            "Fake response to: Hello AURA",
        ),
        (
            "history",
            [("Previous request", "Previous response")],
            None,
            AURARequest(user_input="Current request"),
            "Fake response to: History:\n"
            "User: Previous request\n"
            "Assistant: Previous response\n"
            "User: Current request",
        ),
        (
            "persistent_memory",
            [],
            "AURA",
            AURARequest(
                user_input="What is my name?",
                metadata={"memory_key": "user_name"},
            ),
            "Fake response to: Memory: AURA\nUser: What is my name?",
        ),
    ],
    ids=["baseline", "history", "persistent-memory"],
)
def test_m6_model_context_cases(
    case,
    history_turns,
    memory_value,
    aura_request,
    expected_content,
):
    """Evaluate deterministic baseline, history, and memory behavior."""
    history = ConversationHistory()
    for user_input, assistant_output in history_turns:
        history.add_turn(user_input=user_input, assistant_output=assistant_output)

    memory = InMemoryStore()
    if memory_value is not None:
        memory.store("user_name", memory_value)

    response = build_runtime(memory=memory, history=history).run(aura_request)

    assert response.content == expected_content, case
    assert response.metadata == {"provider": "fake", "policy": "allow"}


def test_m6_retrieval_paired_comparison():
    """Evaluate the same request with and without controlled knowledge."""
    question = "Explain the provider architecture"
    record = KnowledgeRecord(
        content="AURA's model provider architecture is provider-agnostic.",
        source="architecture.md",
    )

    baseline = build_runtime().run(AURARequest(user_input=question))
    retrieved = build_runtime(
        knowledge=InMemoryKnowledgeStore(records=[record])
    ).run(AURARequest(user_input=question))

    assert baseline.content == f"Fake response to: {question}"
    assert record.content not in baseline.content
    assert f"Source: {record.source}" not in baseline.content
    assert record.content in retrieved.content
    assert f"Source: {record.source}" in retrieved.content


def test_m6_tool_selection_and_prepared_argument():
    """Evaluate deterministic calculator selection and request preparation."""
    registry = ToolRegistry()
    registry.register("calculator", CalculatorTool())
    selector = ToolSelector(registry)
    executor = ToolExecutor(registry, policy=Policy())

    tool_name = selector.select("calculate 25 * 4")

    assert tool_name == "calculator"
    assert executor.prepare_input(tool_name, "calculate 25 * 4") == "25 * 4"


def test_m6_approved_tool_execution():
    """Evaluate an approved tool result returned through AURAResponse."""
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    response = build_runtime(registry=registry).run(
        AURARequest(
            user_input="echo",
            metadata={"tool_input": "Hello from AURA"},
        )
    )

    assert response.content == "Hello from AURA"
    assert response.metadata == {"tool": "echo", "policy": "allow"}


def test_m6_invalid_tool_input_is_rejected():
    """Evaluate local validation of invalid tool input."""
    registry = ToolRegistry()
    registry.register("echo", EchoTool())
    executor = ToolExecutor(registry, policy=Policy())

    with pytest.raises(ValueError, match="Tool input cannot be empty"):
        executor.execute("echo", "   ")


def test_m6_unauthorized_tool_is_not_executed():
    """Evaluate authorization before a registered tool can execute."""
    executed = False

    class UnauthorizedTool:
        def execute(self, tool_input):
            nonlocal executed
            executed = True
            return "should not execute"

    registry = ToolRegistry()
    registry.register("unauthorized", UnauthorizedTool())
    executor = ToolExecutor(registry, policy=Policy())

    with pytest.raises(PermissionError, match="not authorized"):
        executor.execute("unauthorized", "test input")

    assert executed is False


def test_m6_unknown_tool_is_handled_safely():
    """Evaluate the runtime's deterministic unknown-tool response."""
    response = build_runtime().run(
        AURARequest(
            user_input="Use unknown tool",
            metadata={"tool": "unknown", "tool_input": "test input"},
        )
    )

    assert response.content == "Tool 'unknown' is not available."
    assert response.metadata == {
        "tool": "unknown",
        "policy": "allow",
        "error": "tool_not_found",
    }


def test_m6_tool_execution_failure_is_handled_safely():
    """Evaluate the runtime's deterministic tool failure response."""
    class FailingTool:
        def execute(self, tool_input):
            raise RuntimeError("deterministic failure")

    registry = ToolRegistry()
    registry.register("echo", FailingTool())

    response = build_runtime(registry=registry).run(
        AURARequest(
            user_input="Run failing tool",
            metadata={"tool": "echo", "tool_input": "test input"},
        )
    )

    assert response.content == "Tool 'echo' failed during execution."
    assert response.metadata == {
        "tool": "echo",
        "policy": "allow",
        "error": "tool_execution_failed",
    }


def test_m6_tool_execution_timeout_raises_timeout_error():
    """Evaluate the existing executor timeout behavior without runtime changes."""
    class SlowTool:
        def execute(self, tool_input):
            time.sleep(0.1)
            return "finished"

    registry = ToolRegistry()
    registry.register("calculator", SlowTool())
    executor = ToolExecutor(registry, policy=Policy())

    with pytest.raises(TimeoutError):
        executor.execute("calculator", "test input", timeout=0.01)
