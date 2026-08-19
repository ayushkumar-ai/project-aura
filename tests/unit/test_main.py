from app.main import create_orchestrator, run_aura
from core.orchestrator import Orchestrator


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