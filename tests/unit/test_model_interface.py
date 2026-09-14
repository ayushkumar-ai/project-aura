from uuid import UUID, uuid4

import pytest

from core.models import AURAResponse
from interfaces.model import ModelInterface


def test_model_interface_requires_generate():
    with pytest.raises(TypeError):
        ModelInterface()


class FakeModel(ModelInterface):
    """Minimal model implementation used only for testing."""

    def generate(self, prompt: str, request_id: UUID) -> AURAResponse:
        return AURAResponse(
            request_id=request_id,
            content=f"Fake response to: {prompt}",
        )


def test_valid_model_implementation():
    model = FakeModel()
    request_id = uuid4()

    response = model.generate("Hello AURA", request_id)

    assert isinstance(response, AURAResponse)
    assert isinstance(response.request_id, UUID)
    assert response.request_id == request_id
    assert response.content == "Fake response to: Hello AURA"


def test_model_interface_import_isolation():
    """Verify that importing ModelInterface in a clean python process succeeds without circular import."""
    import subprocess
    import sys

    code = "from interfaces.model import ModelInterface; assert ModelInterface is not None"
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, f"Import failed with stderr: {res.stderr}"


def test_generic_provider_factory_construction():
    """Verify that generic model provider can be constructed via providers.factory without circular import."""
    from providers.factory import create_model_provider
    from providers.generic_provider import GenericOpenAICompatibleProvider

    provider = create_model_provider(
        provider="generic",
        model_name="llama-3.3-70b-versatile",
        base_url="https://api.groq.com/openai/v1",
        api_key="test-key",
    )
    assert isinstance(provider, ModelInterface)
    assert isinstance(provider, GenericOpenAICompatibleProvider)
    assert provider.model_name == "llama-3.3-70b-versatile"
    assert provider.base_url == "https://api.groq.com/openai/v1"