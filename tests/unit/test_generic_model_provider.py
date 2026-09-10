import pytest
from unittest.mock import MagicMock
from uuid import uuid4

from providers.generic_provider import (
    GenericOpenAICompatibleProvider,
    validate_endpoint_url,
)


def test_url_validation_and_ssrf():
    assert validate_endpoint_url("https://api.openai.com/v1") == "https://api.openai.com/v1"
    assert validate_endpoint_url("http://localhost:11434/v1", allow_local=True) == "http://localhost:11434/v1"
    assert validate_endpoint_url("http://127.0.0.1:8000/v1", allow_local=True) == "http://127.0.0.1:8000/v1"

    # Reject unsupported scheme
    with pytest.raises(ValueError, match="Invalid URL scheme"):
        validate_endpoint_url("ftp://example.com/v1")

    # Reject cloud metadata IP
    with pytest.raises(ValueError, match="SSRF violation"):
        validate_endpoint_url("http://169.254.169.254/latest/meta-data")

    # Reject localhost when local endpoints disallowed
    with pytest.raises(ValueError, match="SSRF violation"):
        validate_endpoint_url("http://127.0.0.1:11434/v1", allow_local=False)


def test_generic_provider_generation():
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "Response from local Ollama"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_client.chat.completions.create.return_value = mock_response

    provider = GenericOpenAICompatibleProvider(
        base_url="http://localhost:11434/v1",
        model_name="llama3:latest",
        api_key="test-key",
        client=mock_client,
    )

    req_id = uuid4()
    resp = provider.generate("Explain quantum computing", request_id=req_id)

    assert resp.request_id == req_id
    assert resp.content == "Response from local Ollama"
    assert resp.metadata["provider"] == "generic"
    assert resp.metadata["model"] == "llama3:latest"
    assert resp.metadata["base_url"] == "http://localhost:11434/v1"


def test_generic_provider_redacts_api_key_on_error():
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = Exception("401 Unauthorized with secret-token-xyz123")
    if hasattr(mock_client, "responses"):
        mock_client.responses.create.side_effect = Exception("401 Unauthorized with secret-token-xyz123")

    provider = GenericOpenAICompatibleProvider(
        base_url="https://remote-llm.example.com/v1",
        model_name="gpt-4o",
        api_key="secret-token-xyz123",
        client=mock_client,
    )

    with pytest.raises(RuntimeError) as exc_info:
        provider.generate("Hello", request_id=uuid4())

    err_str = str(exc_info.value)
    assert "secret-token-xyz123" not in err_str
    assert "[REDACTED_API_KEY]" in err_str
