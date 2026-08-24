from app.main import create_orchestrator, run_aura
from core.orchestrator import Orchestrator
from core.history import ConversationHistory
from core.tool_registry import ToolRegistry
from interfaces.tool_executor import ToolExecutor
from interfaces.tool_selector import ToolSelector
from memory.in_memory import InMemoryStore


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