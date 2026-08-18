from uuid import UUID

from core.models import AURAResponse
from providers.fake_model import FakeModelProvider


def test_fake_model_provider():
    provider = FakeModelProvider()

    response = provider.generate("Hello AURA")

    assert isinstance(response, AURAResponse)
    assert isinstance(response.request_id, UUID)
    assert response.content == "Fake response to: Hello AURA"
    assert response.metadata == {"provider": "fake"}