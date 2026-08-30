import pytest

from app.main import create_aura, create_orchestrator, run_aura
from core.orchestrator import Orchestrator
from core.history import ConversationHistory
from core.tool_registry import ToolRegistry
from interfaces.tool_executor import ToolExecutor
from interfaces.tool_selector import ToolSelector
from memory.in_memory import InMemoryStore
from app.aura import AURA
from core.models import AURARequest


def test_create_orchestrator_composes_full_runtime():
    orchestrator = create_orchestrator()

    assert isinstance(orchestrator.memory, InMemoryStore)
    assert isinstance(orchestrator.history, ConversationHistory)
    assert isinstance(orchestrator.tool_registry, ToolRegistry)
    assert isinstance(orchestrator.tool_selector, ToolSelector)
    assert isinstance(orchestrator.tool_executor, ToolExecutor)


def test_create_orchestrator_registers_echo_tool():
    orchestrator = create_orchestrator()

    assert orchestrator.tool_registry is not None
    assert orchestrator.tool_registry.get("echo") is not None
    assert orchestrator.tool_registry.get("echo").description == "Echo tool"


def test_default_composition_rejects_unauthorized_tool_before_execution():
    executed = False

    class UnauthorizedTool:
        def execute(self, tool_input):
            nonlocal executed
            executed = True
            return "should not execute"

    orchestrator = create_orchestrator()
    orchestrator.tool_registry.register("unauthorized", UnauthorizedTool())

    response = orchestrator.run(
        AURARequest(
            user_input="Run unauthorized tool",
            metadata={
                "tool": "unauthorized",
                "tool_input": "test input",
            },
        )
    )

    assert executed is False
    assert response.content == "Tool 'unauthorized' is not authorized."
    assert response.metadata == {
        "tool": "unauthorized",
        "policy": "deny",
        "error": "tool_unauthorized",
    }


def test_create_orchestrator():
    orchestrator = create_orchestrator()

    assert isinstance(orchestrator, Orchestrator)


def test_run_aura():
    response = run_aura("Hello AURA")

    assert response.content == "Fake response to: Hello AURA"
    assert response.metadata == {
        "provider": "fake",
        "policy": "allow",
    }


def test_run_aura_denied_request():
    response = run_aura("   ")

    assert response.content == "Request denied by policy."
    assert response.metadata == {
        "policy": "deny",
    }


def test_create_aura():
    aura = create_aura()

    assert isinstance(aura, AURA)
    assert isinstance(aura.orchestrator, Orchestrator)


def test_aura_preserves_history_between_requests():
    aura = create_aura()

    first_response = aura.run("Hello AURA")
    second_response = aura.run("What did I say?")

    history = aura.orchestrator.history

    assert history is not None
    assert len(history.turns) == 2

    assert history.turns[0].user_input == "Hello AURA"
    assert history.turns[0].assistant_output == first_response.content

    assert history.turns[1].user_input == "What did I say?"
    assert history.turns[1].assistant_output == second_response.content


def test_aura_preserves_memory_between_requests():
    aura = create_aura()

    assert aura.orchestrator.memory is not None

    aura.orchestrator.memory.store("test_key", "test_value")

    response = aura.orchestrator.run(
        AURARequest(
            user_input="What is stored?",
            metadata={"memory_key": "test_key"},
        )
    )

    assert response.content == (
        "Fake response to: Memory: test_value\n"
        "User: What is stored?"
    )


def test_aura_run_request_accepts_complete_request():
    aura = create_aura()

    request = AURARequest(
        user_input="echo",
        metadata={
            "tool_input": "Hello from public AURA API",
        },
    )

    response = aura.run_request(request)

    assert response.content == "Hello from public AURA API"
    assert response.metadata == {
        "tool": "echo",
        "policy": "allow",
    }


def test_aura_run_delegates_through_request_api():
    aura = create_aura()

    response = aura.run("Hello AURA")

    assert response.content == "Fake response to: Hello AURA"
    assert response.metadata == {
        "provider": "fake",
        "policy": "allow",
    }


def test_create_orchestrator_rejects_unsupported_model_provider(
    monkeypatch,
):
    monkeypatch.setenv("AURA_MODEL_PROVIDER", "unknown")

    with pytest.raises(
        ValueError,
        match="Unsupported model provider",
    ):
        create_orchestrator()
