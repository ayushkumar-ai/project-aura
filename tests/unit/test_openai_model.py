from uuid import uuid4

import pytest

from core.models import AURAResponse
from interfaces.model import ModelInterface
from providers.openai_model import OpenAIProvider


class FakeResponse:
    output_text = "OpenAI response"


class FakeResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse()


class FakeOpenAIClient:
    def __init__(self):
        self.responses = FakeResponses()


def test_openai_provider_implements_model_interface():
    client = FakeOpenAIClient()

    provider = OpenAIProvider(
        model_name="test-model",
        api_key="test-key",
        client=client,
    )

    assert isinstance(provider, ModelInterface)


def test_openai_provider_generates_response():
    client = FakeOpenAIClient()
    provider = OpenAIProvider(
        model_name="test-model",
        api_key="test-key",
        client=client,
    )

    request_id = uuid4()

    response = provider.generate(
        prompt="Hello AURA",
        request_id=request_id,
    )

    assert isinstance(response, AURAResponse)
    assert response.content == "OpenAI response"


def test_openai_provider_propagates_request_id():
    client = FakeOpenAIClient()
    provider = OpenAIProvider(
        model_name="test-model",
        api_key="test-key",
        client=client,
    )

    request_id = uuid4()

    response = provider.generate(
        prompt="Hello AURA",
        request_id=request_id,
    )

    assert response.request_id == request_id


def test_openai_provider_sends_prompt_and_model():
    client = FakeOpenAIClient()
    provider = OpenAIProvider(
        model_name="test-model",
        api_key="test-key",
        client=client,
    )

    request_id = uuid4()

    provider.generate(
        prompt="Hello AURA",
        request_id=request_id,
    )

    assert client.responses.calls == [
        {
            "model": "test-model",
            "input": "Hello AURA",
        }
    ]


def test_openai_provider_sets_metadata():
    client = FakeOpenAIClient()
    provider = OpenAIProvider(
        model_name="test-model",
        api_key="test-key",
        client=client,
    )

    response = provider.generate(
        prompt="Hello AURA",
        request_id=uuid4(),
    )

    assert response.metadata == {
        "provider": "openai",
        "model": "test-model",
    }


def test_openai_provider_wraps_api_failure():
    class FailingResponses:
        def create(self, **kwargs):
            raise RuntimeError("API failure")

    class FailingClient:
        responses = FailingResponses()

    provider = OpenAIProvider(
        model_name="test-model",
        api_key="test-key",
        client=FailingClient(),
    )

    with pytest.raises(
        RuntimeError,
        match="OpenAI model generation failed",
    ):
        provider.generate(
            prompt="Hello AURA",
            request_id=uuid4(),
        )


def test_openai_provider_requires_model_name():
    with pytest.raises(
        ValueError,
        match="OpenAI model name cannot be empty",
    ):
        OpenAIProvider(
            model_name="   ",
            api_key="test-key",
        )


def test_openai_provider_requires_api_key_without_client():
    with pytest.raises(
        ValueError,
        match="OpenAI API key cannot be empty",
    ):
        OpenAIProvider(
            model_name="test-model",
            api_key="",
        )
