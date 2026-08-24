from uuid import UUID, uuid4

from core.models import AURAResponse
from providers.fake_model import FakeModelProvider


def test_fake_model_provider():
    provider = FakeModelProvider()
    request_id = uuid4()

    response = provider.generate("Hello AURA", request_id)

    assert isinstance(response, AURAResponse)
    assert isinstance(response.request_id, UUID)
    assert response.request_id == request_id
    assert response.content == "Fake response to: Hello AURA"
    assert response.metadata == {"provider": "fake"}