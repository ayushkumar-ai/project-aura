from core.models import AURARequest, AURAResponse
from core.orchestrator import Orchestrator
from core.policy import Policy
from providers.fake_model import FakeModelProvider
from memory.in_memory import InMemoryStore
from core.history import ConversationHistory
import pytest
from tools.echo import EchoTool
from core.tool_registry import ToolRegistry
from interfaces.tool_selector import ToolSelector
from tools.echo import EchoTool
from knowledge.in_memory import InMemoryKnowledgeStore, KnowledgeRecord


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
    registry.register("echo", FailingTool())

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_registry=registry,
    )

    request = AURARequest(
        user_input="Run failing tool",
        metadata={
            "tool": "echo",
            "tool_input": "Hello",
        },
    )

    response = orchestrator.run(request)

    assert response.content == "Tool 'echo' failed during execution."
    assert response.metadata == {
        "tool": "echo",
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
    registry.register("echo", FailingTool())

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
            "tool": "echo",
            "tool_input": "Hello",
        },
    )

    response = orchestrator.run(request)

    assert response.metadata["error"] == "tool_execution_failed"
    assert history.turns == []


def test_orchestrator_accepts_tool_executor():
    from interfaces.tool_executor import ToolExecutor
    from core.tool_registry import ToolRegistry
    from tools.echo import EchoTool

    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    executor = ToolExecutor(registry)

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_executor=executor,
    )

    request = AURARequest(
        user_input="Use the echo tool",
        metadata={
            "tool": "echo",
            "tool_input": "Hello from executor",
        },
    )

    response = orchestrator.run(request)

    assert response.content == "Hello from executor"
    assert response.metadata == {
        "tool": "echo",
        "policy": "allow",
    }


def test_orchestrator_prefers_explicit_tool_executor():
    from core.tool_registry import ToolRegistry
    from interfaces.tool_executor import ToolExecutor
    from tools.echo import EchoTool

    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    executor = ToolExecutor(registry)

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_registry=registry,
        tool_executor=executor,
    )

    assert orchestrator.tool_executor is executor


class FakeToolExecutor:
    """Test executor used to verify dependency injection."""

    def __init__(self):
        self.calls = []

    def execute(self, tool_name: str, tool_input: str) -> str:
        self.calls.append((tool_name, tool_input))
        return f"Executed by injected executor: {tool_input}"

    def list_tools(self) -> list[str]:
        return ["fake"]


def test_orchestrator_uses_injected_tool_executor():
    executor = FakeToolExecutor()

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_executor=executor,
    )

    request = AURARequest(
        user_input="Use a tool",
        metadata={
            "tool": "fake",
            "tool_input": "Hello AURA",
        },
    )

    response = orchestrator.run(request)

    assert response.content == "Executed by injected executor: Hello AURA"
    assert executor.calls == [
        ("fake", "Hello AURA"),
    ]


def test_orchestrator_does_not_require_tool_registry_when_executor_is_injected():
    executor = FakeToolExecutor()

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_executor=executor,
    )

    assert orchestrator.tool_registry is None
    assert orchestrator.tool_executor is executor


def test_orchestrator_uses_injected_executor_for_tool_failure():
    class FailingExecutor:
        def execute(self, tool_name: str, tool_input: str) -> str:
            raise RuntimeError("Injected executor failed")

        def list_tools(self) -> list[str]:
            return []

    executor = FailingExecutor()

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_executor=executor,
    )

    request = AURARequest(
        user_input="Use a tool",
        metadata={
            "tool": "anything",
            "tool_input": "Hello AURA",
        },
    )

    response = orchestrator.run(request)

    assert response.content == "Tool 'anything' failed during execution."
    assert response.metadata == {
        "tool": "anything",
        "policy": "allow",
        "error": "tool_execution_failed",
    }


def test_orchestrator_accepts_tool_selector():
    from core.tool_registry import ToolRegistry
    from interfaces.tool_selector import ToolSelector
    from tools.echo import EchoTool

    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    selector = ToolSelector(registry)

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_selector=selector,
    )

    assert orchestrator.tool_selector is selector


def test_orchestrator_uses_tool_selector_to_choose_tool():
    from core.tool_registry import ToolRegistry
    from interfaces.tool_selector import ToolSelector
    from tools.echo import EchoTool

    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    selector = ToolSelector(registry)

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_selector=selector,
    )

    request = AURARequest(
        user_input="echo",
        metadata={
            "tool_input": "Hello from selector",
        },
    )

    response = orchestrator.run(request)

    assert response.content == "Hello from selector"
    assert response.metadata["tool"] == "echo"


def test_orchestrator_handles_unknown_tool_selected_by_selector():
    from core.tool_registry import ToolRegistry
    from interfaces.tool_selector import ToolSelector

    registry = ToolRegistry()
    selector = ToolSelector(registry)

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_selector=selector,
    )

    request = AURARequest(
        user_input="calculator",
        metadata={
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


def test_orchestrator_handles_selected_tool_execution_failure():
    from core.tool_registry import ToolRegistry
    from interfaces.tool_selector import ToolSelector

    class FailingEchoTool(EchoTool):
        def execute(self, input_data: str) -> str:
            raise RuntimeError("Tool failed")

    registry = ToolRegistry()
    registry.register("echo", FailingEchoTool())

    selector = ToolSelector(registry)

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_selector=selector,
    )

    request = AURARequest(
        user_input="echo",
        metadata={
            "tool_input": "Hello",
        },
    )

    response = orchestrator.run(request)

    assert response.content == "Tool 'echo' failed during execution."
    assert response.metadata == {
        "tool": "echo",
        "policy": "allow",
        "error": "tool_execution_failed",
    }


def test_orchestrator_resolves_explicit_tool_and_input():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_registry=registry,
    )

    request = AURARequest(
        user_input="anything",
        metadata={
            "tool": "echo",
            "tool_input": "Hello",
        },
    )

    assert orchestrator._resolve_tool(request) == ("echo", "Hello", True)


def test_orchestrator_resolves_selected_tool_with_supplied_input():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    selector = ToolSelector(registry)

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_selector=selector,
    )

    request = AURARequest(
        user_input="echo",
        metadata={
            "tool_input": "Hello",
        },
    )

    assert orchestrator._resolve_tool(request) == ("echo", "Hello", False)


def test_orchestrator_resolves_no_tool_when_no_selector_matches():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    selector = ToolSelector(registry)

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_selector=selector,
    )

    request = AURARequest(
        user_input="hello there",
        metadata={},
    )

    assert orchestrator._resolve_tool(request) == (None, None, False)


def test_orchestrator_resolves_explicit_tool_without_input():
    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_registry=registry,
    )

    request = AURARequest(
        user_input="anything",
        metadata={
            "tool": "echo",
        },
    )

    assert orchestrator._resolve_tool(request) == ("echo", None, True)


def test_orchestrator_resolves_no_tool_without_selector():
    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
    )

    request = AURARequest(
        user_input="hello there",
        metadata={},
    )

    assert orchestrator._resolve_tool(request) == (None, None, False)


def test_orchestrator_propagates_unknown_tool_from_selector():
    registry = ToolRegistry()
    selector = ToolSelector(registry)

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_selector=selector,
    )

    request = AURARequest(
        user_input="calculator",
        metadata={},
    )

    with pytest.raises(KeyError):
        orchestrator._resolve_tool(request)


def test_orchestrator_prepares_tool_input_when_not_supplied():
    from core.tool_registry import ToolRegistry
    from interfaces.tool_executor import ToolExecutor
    from tools.echo import EchoTool

    registry = ToolRegistry()
    registry.register("echo", EchoTool())

    executor = ToolExecutor(registry)

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_executor=executor,
    )

    request = AURARequest(
        user_input="Hello from AURA",
        metadata={
            "tool": "echo",
        },
    )

    result = orchestrator._execute_tool(
        tool_name="echo",
        tool_input=None,
        request=request,
    )

    assert result == "Hello from AURA"


def test_orchestrator_uses_supplied_tool_input_directly():
    class RecordingExecutor:
        def __init__(self):
            self.prepare_calls = []
            self.execute_calls = []

        def prepare_input(self, tool_name: str, request: str) -> str:
            self.prepare_calls.append((tool_name, request))
            return "prepared input"

        def execute(self, tool_name: str, tool_input: str) -> str:
            self.execute_calls.append((tool_name, tool_input))
            return "executed result"

    executor = RecordingExecutor()

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_executor=executor,
    )

    request = AURARequest(
        user_input="Original request",
        metadata={},
    )

    result = orchestrator._execute_tool(
        tool_name="echo",
        tool_input="Supplied input",
        request=request,
    )

    assert result == "executed result"
    assert executor.prepare_calls == []
    assert executor.execute_calls == [
        ("echo", "Supplied input"),
    ]


def test_orchestrator_run_executes_resolved_tool_and_records_history():
    class RecordingExecutor:
        def __init__(self):
            self.execute_calls = []

        def prepare_input(self, tool_name: str, request: str) -> str:
            return request

        def execute(self, tool_name: str, tool_input: str) -> str:
            self.execute_calls.append((tool_name, tool_input))
            return "tool result"

    executor = RecordingExecutor()
    history = ConversationHistory()

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_executor=executor,
        history=history,
    )

    request = AURARequest(
        user_input="Original request",
        metadata={
            "tool": "echo",
            "tool_input": "Supplied input",
        },
    )

    result = orchestrator.run(request)

    assert result.content == "tool result"
    assert result.metadata["tool"] == "echo"
    assert result.metadata["policy"] == "allow"

    assert executor.execute_calls == [
        ("echo", "Supplied input"),
    ]

    assert len(history.turns) == 1
    assert history.turns[0].user_input == "Original request"
    assert history.turns[0].assistant_output == "tool result"


def test_history_and_memory_are_combined_in_model_prompt():
    history = ConversationHistory()
    history.add_turn(
        user_input="Previous user message",
        assistant_output="Previous assistant response",
    )

    memory = InMemoryStore()
    memory.store("user_name", "AURA")

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        history=history,
        memory=memory,
    )

    request = AURARequest(
        user_input="Current user request",
        metadata={"memory_key": "user_name"},
    )

    response = orchestrator.run(request)

    prompt = response.content.removeprefix("Fake response to: ")

    assert "Previous user message" in prompt
    assert "Previous assistant response" in prompt
    assert "AURA" in prompt
    assert "Current user request" in prompt


def test_retrieved_knowledge_reaches_model_prompt():
    knowledge = InMemoryKnowledgeStore(
        records=[
            KnowledgeRecord(
                content="AURA is built with Python.",
                source="architecture.md",
            )
        ]
    )

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        knowledge=knowledge,
    )

    request = AURARequest(
        user_input="Tell me what language AURA uses",
    )

    response = orchestrator.run(request)

    prompt = response.content.removeprefix("Fake response to: ")

    assert "AURA is built with Python." in prompt
    assert "Source: architecture.md" in prompt
    assert "Tell me what language AURA uses" in prompt


def test_orchestrator_handles_unauthorized_tool_execution():
    executed = False

    class UnauthorizedTool:
        def execute(self, input_data: str) -> str:
            nonlocal executed
            executed = True
            return "executed"

    registry = ToolRegistry()
    registry.register("unauthorized", UnauthorizedTool())

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        tool_registry=registry,
    )

    request = AURARequest(
        user_input="Run unauthorized tool",
        metadata={
            "tool": "unauthorized",
            "tool_input": "Hello",
        },
    )

    response = orchestrator.run(request)

    assert executed is False
    assert response.content == "Tool 'unauthorized' is not authorized."
    assert response.metadata == {
        "tool": "unauthorized",
        "policy": "deny",
        "error": "tool_unauthorized",
    }


def test_orchestrator_handles_model_generation_failure():
    from interfaces.model import ModelInterface

    class FailingModelProvider(ModelInterface):
        def generate(self, prompt: str, request_id):
            raise RuntimeError("Underlying LLM API failed")

    history = ConversationHistory()
    orchestrator = Orchestrator(
        model=FailingModelProvider(),
        policy=Policy(),
        history=history,
    )

    request = AURARequest(user_input="Hello AURA")
    response = orchestrator.run(request)

    assert isinstance(response, AURAResponse)
    assert response.request_id == request.request_id
    assert response.content == "Model generation failed."
    assert response.metadata == {
        "policy": "allow",
        "error": "model_generation_failed",
    }
    assert history.turns == []


def test_orchestrator_does_not_record_denied_request_in_memory():
    memory = InMemoryStore()
    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        memory=memory,
    )

    request = AURARequest(user_input="   ")
    response = orchestrator.run(request)

    assert response.metadata["policy"] == "deny"
    assert memory.retrieve(str(request.request_id)) is None
