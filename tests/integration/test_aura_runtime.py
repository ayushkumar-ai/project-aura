import pytest

from core.history import ConversationHistory
from core.models import AURARequest
from core.orchestrator import Orchestrator
from core.policy import Policy
from core.tool_registry import ToolRegistry
from interfaces.tool_executor import ToolExecutor
from interfaces.tool_selector import ToolSelector
from memory.in_memory import InMemoryStore
from providers.fake_model import FakeModelProvider
from tools.echo import EchoTool


class FailingTool:
    """Tool used to verify runtime failure handling."""

    @property
    def description(self) -> str:
        return "Failing tool"

    def execute(self, input_data: str) -> str:
        raise RuntimeError("Tool execution failed")


def build_runtime(
    *,
    memory=None,
    history=None,
    registry=None,
):
    registry = registry or ToolRegistry()
    selector = ToolSelector(registry)
    executor = ToolExecutor(registry)

    return Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        memory=memory,
        history=history,
        tool_registry=registry,
        tool_selector=selector,
        tool_executor=executor,
    )


def test_aura_tool_runtime_end_to_end():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    orchestrator = build_runtime(registry=registry)

    request = AURARequest(
        user_input="echo",
        metadata={
            "tool_input": "Hello from AURA",
        },
    )

    response = orchestrator.run(request)

    assert response.content == "Hello from AURA"
    assert response.metadata == {
        "tool": "echo",
        "policy": "allow",
    }


def test_aura_runtime_falls_back_to_model_when_no_tool_matches():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    orchestrator = build_runtime(registry=registry)

    request = AURARequest(user_input="Hello AURA")

    response = orchestrator.run(request)

    assert response.content == "Fake response to: Hello AURA"
    assert response.metadata == {
        "provider": "fake",
        "policy": "allow",
    }


def test_aura_runtime_handles_unknown_explicit_tool():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    orchestrator = build_runtime(registry=registry)

    request = AURARequest(
        user_input="Use unknown tool",
        metadata={
            "tool": "calculator",
            "tool_input": "2 + 2",
        },
    )

    response = orchestrator.run(request)

    assert response.content == "Tool 'calculator' is not available."
    assert response.metadata == {
        "tool": "calculator",
        "policy": "allow",
        "error": "tool_not_found",
    }


def test_aura_runtime_handles_tool_execution_failure():
    registry = ToolRegistry()
    registry.register("failing", FailingTool())

    orchestrator = build_runtime(registry=registry)

    request = AURARequest(
        user_input="Run failing tool",
        metadata={
            "tool": "failing",
            "tool_input": "Hello",
        },
    )

    response = orchestrator.run(request)

    assert response.content == "Tool 'failing' failed during execution."
    assert response.metadata == {
        "tool": "failing",
        "policy": "allow",
        "error": "tool_execution_failed",
    }


def test_aura_runtime_denies_request_before_tool_execution():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    history = ConversationHistory()
    orchestrator = build_runtime(
        registry=registry,
        history=history,
    )

    request = AURARequest(
        user_input="   ",
        metadata={
            "tool": "echo",
            "tool_input": "Should not execute",
        },
    )

    response = orchestrator.run(request)

    assert response.content == "Request denied by policy."
    assert response.metadata == {
        "policy": "deny",
    }
    assert history.turns == []


def test_aura_runtime_preserves_conversation_history():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    history = ConversationHistory()
    orchestrator = build_runtime(
        registry=registry,
        history=history,
    )

    first_request = AURARequest(user_input="Hello AURA")
    first_response = orchestrator.run(first_request)

    second_request = AURARequest(user_input="What can you do?")
    second_response = orchestrator.run(second_request)

    assert len(history.turns) == 2

    assert history.turns[0].user_input == "Hello AURA"
    assert history.turns[0].assistant_output == first_response.content

    assert history.turns[1].user_input == "What can you do?"
    assert history.turns[1].assistant_output == second_response.content


def test_aura_runtime_uses_memory_in_model_context():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    memory = InMemoryStore()
    memory.store("user_name", "AURA")

    orchestrator = build_runtime(
        registry=registry,
        memory=memory,
    )

    request = AURARequest(
        user_input="What is my name?",
        metadata={
            "memory_key": "user_name",
        },
    )

    response = orchestrator.run(request)

    assert response.content == (
        "Fake response to: Memory: AURA\n"
        "User: What is my name?"
    )