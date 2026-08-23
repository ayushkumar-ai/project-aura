from core.models import AURARequest, AURAResponse
from core.orchestrator import Orchestrator
from core.policy import Policy
from providers.fake_model import FakeModelProvider
from memory.in_memory import InMemoryStore
from core.history import ConversationHistory
import pytest
from tools.echo import EchoTool


def test_orchestrator_allows_request():
    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
    )

    request = AURARequest(user_input="Hello AURA")
    response = orchestrator.run(request)

    assert isinstance(response, AURAResponse)
    assert response.request_id == request.request_id
    assert response.content == "Fake response to: Hello AURA"
    assert response.metadata == {
        "provider": "fake",
        "policy": "allow",
    }


def test_orchestrator_denies_request():
    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
    )

    request = AURARequest(user_input="   ")
    response = orchestrator.run(request)

    assert isinstance(response, AURAResponse)
    assert response.request_id == request.request_id
    assert response.content == "Request denied by policy."
    assert response.metadata == {
        "policy": "deny",
    }

def test_orchestrator_evaluates_response():
    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
    )

    request = AURARequest(user_input="Hello AURA")
    result = orchestrator.evaluate(request)

    assert result.passed is True
    assert result.score == 1.0
    assert result.details == {
        "reason": "response is valid",
    }


def test_orchestrator_evaluates_denied_response():
    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
    )

    request = AURARequest(user_input="   ")
    result = orchestrator.evaluate(request)

    assert result.passed is True
    assert result.score == 1.0


def test_orchestrator_stores_request_in_memory():
    memory = InMemoryStore()

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        memory=memory,
    )

    request = AURARequest(user_input="Remember this")

    orchestrator.run(request)

    assert memory.retrieve(str(request.request_id)) == "Remember this"


def test_orchestrator_builds_context_with_memory():
    memory = InMemoryStore()

    memory.store("user_name", "AURA")

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        memory=memory,
    )

    request = AURARequest(
        user_input="What is my name?",
        metadata={"memory_key": "user_name"},
    )

    context = orchestrator._build_context(request)

    assert context.state == {
        "memory": "AURA",
    }



def test_orchestrator_uses_memory_in_model_prompt():
    memory = InMemoryStore()
    memory.store("user_name", "AURA")

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        memory=memory,
    )

    request = AURARequest(
        user_input="What is my name?",
        metadata={"memory_key": "user_name"},
    )

    response = orchestrator.run(request)

    assert response.content == (
        "Fake response to: Memory: AURA\n"
        "User: What is my name?"
    )


def test_orchestrator_handles_missing_memory():
    memory = InMemoryStore()

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        memory=memory,
    )

    request = AURARequest(
        user_input="What is my name?",
        metadata={"memory_key": "unknown"},
    )

    context = orchestrator._build_context(request)

    assert context.state == {}

def test_orchestrator_uses_user_prompt_when_memory_is_missing():
    memory = InMemoryStore()

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        memory=memory,
    )

    request = AURARequest(
        user_input="What is my name?",
        metadata={"memory_key": "unknown"},
    )

    response = orchestrator.run(request)

    assert response.content == (
        "Fake response to: What is my name?"
    )


def test_orchestrator_records_conversation_history():
    history = ConversationHistory()

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        history=history,
    )

    request = AURARequest(user_input="Hello AURA")

    response = orchestrator.run(request)

    assert len(history.turns) == 1
    assert history.turns[0].user_input == "Hello AURA"
    assert history.turns[0].assistant_output == response.content


def test_orchestrator_does_not_record_denied_request_in_history():
    history = ConversationHistory()

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        history=history,
    )

    request = AURARequest(user_input="   ")

    orchestrator.run(request)

    assert history.turns == []


def test_orchestrator_builds_context_with_history():
    history = ConversationHistory()

    history.add_turn(
        user_input="Hello AURA",
        assistant_output="Hello! How can I help?",
    )

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        history=history,
    )

    request = AURARequest(user_input="What can you do?")

    context = orchestrator._build_context(request)

    assert context.history is history
    assert len(context.history.turns) == 1
    assert context.history.turns[0].user_input == "Hello AURA"


def test_orchestrator_uses_conversation_history_in_model_prompt():
    history = ConversationHistory()

    history.add_turn(
        user_input="Hello AURA",
        assistant_output="Hello! How can I help?",
    )

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        history=history,
    )

    request = AURARequest(user_input="What can you do?")

    response = orchestrator.run(request)

    assert response.content == (
        "Fake response to: "
        "History:\n"
        "User: Hello AURA\n"
        "Assistant: Hello! How can I help?\n"
        "User: What can you do?"
    )


def test_orchestrator_maintains_history_across_multiple_requests():
    history = ConversationHistory()

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
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


def test_orchestrator_does_not_add_denied_request_between_turns():
    history = ConversationHistory()

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        history=history,
    )

    first_request = AURARequest(user_input="Hello AURA")
    first_response = orchestrator.run(first_request)

    denied_request = AURARequest(user_input="   ")
    orchestrator.run(denied_request)

    second_request = AURARequest(user_input="Continue")
    second_response = orchestrator.run(second_request)

    assert len(history.turns) == 2

    assert history.turns[0].user_input == "Hello AURA"
    assert history.turns[0].assistant_output == first_response.content

    assert history.turns[1].user_input == "Continue"
    assert history.turns[1].assistant_output == second_response.content


def test_orchestrator_accepts_tool_registry():
    from core.tool_registry import ToolRegistry

    registry = ToolRegistry()

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_registry=registry,
    )

    assert orchestrator.tool_registry is registry


def test_orchestrator_executes_requested_tool():
    from core.tool_registry import ToolRegistry
    from tools.echo import EchoTool

    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_registry=registry,
    )

    request = AURARequest(
        user_input="Use the echo tool",
        metadata={
            "tool": "echo",
            "tool_input": "Hello from tool",
        },
    )

    response = orchestrator.run(request)

    assert response.content == "Hello from tool"

def test_orchestrator_returns_error_for_unknown_tool():
    from core.tool_registry import ToolRegistry

    registry = ToolRegistry()

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_registry=registry,
    )

    request = AURARequest(
        user_input="Use unknown tool",
        metadata={
            "tool": "unknown",
            "tool_input": "Hello",
        },
    )

    response = orchestrator.run(request)

    assert response.content == "Tool 'unknown' is not available."
    assert response.metadata == {
        "tool": "unknown",
        "policy": "allow",
        "error": "tool_not_found",
    }


def test_orchestrator_uses_model_when_no_tool_requested():
    from core.tool_registry import ToolRegistry

    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_registry=registry,
    )

    request = AURARequest(user_input="Hello AURA")

    response = orchestrator.run(request)

    assert response.content == "Fake response to: Hello AURA"


def test_orchestrator_uses_model_when_tool_input_is_missing():
    from core.tool_registry import ToolRegistry

    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_registry=registry,
    )

    request = AURARequest(
        user_input="Hello AURA",
        metadata={
            "tool": "echo",
        },
    )

    response = orchestrator.run(request)

    assert response.content == "Fake response to: Hello AURA"


def test_orchestrator_includes_tool_metadata_in_response():
    from core.tool_registry import ToolRegistry
    from tools.echo import EchoTool

    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_registry=registry,
    )

    request = AURARequest(
        user_input="Use the echo tool",
        metadata={
            "tool": "echo",
            "tool_input": "Hello from tool",
        },
    )

    response = orchestrator.run(request)

    assert response.metadata == {
        "tool": "echo",
        "policy": "allow",
    }


class FailingTool:
    """Tool used to test tool execution failures."""

    def execute(self, input_data: str) -> str:
        raise RuntimeError("Tool execution failed")


def test_orchestrator_handles_tool_execution_failure():
    from core.tool_registry import ToolRegistry

    registry = ToolRegistry()
    registry.register("failing", FailingTool())

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_registry=registry,
    )

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


def test_orchestrator_does_not_execute_tool_without_tool_input():
    from core.tool_registry import ToolRegistry
    from tools.echo import EchoTool

    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_registry=registry,
    )

    request = AURARequest(
        user_input="Use the echo tool",
        metadata={
            "tool": "echo",
        },
    )

    response = orchestrator.run(request)

    assert response.content == "Fake response to: Use the echo tool"


def test_orchestrator_does_not_execute_tool_when_request_is_denied():
    from core.tool_registry import ToolRegistry
    from tools.echo import EchoTool

    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_registry=registry,
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


def test_orchestrator_records_successful_tool_execution_in_history():
    from core.tool_registry import ToolRegistry
    from tools.echo import EchoTool

    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    history = ConversationHistory()

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_registry=registry,
        history=history,
    )

    request = AURARequest(
        user_input="Use the echo tool",
        metadata={
            "tool": "echo",
            "tool_input": "Hello from tool",
        },
    )

    response = orchestrator.run(request)

    assert len(history.turns) == 1
    assert history.turns[0].user_input == "Use the echo tool"
    assert history.turns[0].assistant_output == response.content


def test_orchestrator_does_not_record_failed_tool_execution_in_history():
    from core.tool_registry import ToolRegistry

    registry = ToolRegistry()
    registry.register("failing", FailingTool())

    history = ConversationHistory()

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_registry=registry,
        history=history,
    )

    request = AURARequest(
        user_input="Run failing tool",
        metadata={
            "tool": "failing",
            "tool_input": "Hello",
        },
    )

    response = orchestrator.run(request)

    assert response.metadata["error"] == "tool_execution_failed"
    assert history.turns == []
