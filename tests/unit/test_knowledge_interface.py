import pytest

from core.models import AURARequest
from core.orchestrator import Orchestrator
from core.policy import Policy
from interfaces.knowledge import KnowledgeInterface, KnowledgeRecord
from knowledge.in_memory import InMemoryKnowledgeStore
from providers.fake_model import FakeModelProvider


def test_knowledge_interface_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        KnowledgeInterface()


def test_in_memory_knowledge_store_implements_interface():
    store = InMemoryKnowledgeStore()
    assert isinstance(store, KnowledgeInterface)


def test_orchestrator_accepts_custom_knowledge_interface():
    class CustomKnowledgeStore(KnowledgeInterface):
        def retrieve(
            self,
            query: str,
            top_k: int | None = None,
        ) -> list[KnowledgeRecord]:
            return [
                KnowledgeRecord(
                    content="Custom knowledge content",
                    source="custom_source",
                )
            ]

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        knowledge=CustomKnowledgeStore(),
    )

    response = orchestrator.run(AURARequest(user_input="test query"))

    assert "Custom knowledge content" in response.content
    assert "custom_source" in response.content


def test_orchestrator_sanitizes_malformed_knowledge_records_from_custom_store():
    class CustomKnowledgeStore(KnowledgeInterface):
        def retrieve(
            self,
            query: str,
            top_k: int | None = None,
        ) -> list[KnowledgeRecord]:
            return [
                None,
                "not a record",
                123,
                KnowledgeRecord(content="Valid custom info", source="valid_src"),
            ]

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        knowledge=CustomKnowledgeStore(),
    )

    response = orchestrator.run(AURARequest(user_input="custom info"))

    assert "Valid custom info" in response.content
    assert response.metadata == {"provider": "fake", "policy": "allow"}


def test_orchestrator_handles_non_list_knowledge_retrieval_return():
    class MalformedKnowledgeStore(KnowledgeInterface):
        def retrieve(
            self,
            query: str,
            top_k: int | None = None,
        ):
            return "not a list"

    orchestrator = Orchestrator(
        model=FakeModelProvider(),
        policy=Policy(),
        knowledge=MalformedKnowledgeStore(),
    )

    response = orchestrator.run(AURARequest(user_input="test input"))

    assert response.content == "Fake response to: test input"
    assert response.metadata == {"provider": "fake", "policy": "allow"}
