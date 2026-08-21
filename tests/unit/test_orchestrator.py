from core.models import AURARequest, AURAResponse
from core.orchestrator import Orchestrator
from core.policy import Policy
from providers.fake_model import FakeModelProvider
from memory.in_memory import InMemoryStore
from core.history import ConversationHistory


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