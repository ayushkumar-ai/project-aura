import os
import pytest
from app.config import Settings
import app.config


@pytest.fixture(autouse=True)
def isolate_test_environment(monkeypatch):
    """Ensure tests run in a clean, hermetic environment isolated from local .env files."""
    monkeypatch.setenv("AURA_ENV", "development")
    monkeypatch.setenv("AURA_APP_NAME", "AURA")
    monkeypatch.setenv("AURA_LOG_LEVEL", "INFO")
    monkeypatch.setenv("AURA_MODEL_PROVIDER", "")
    monkeypatch.setenv("AURA_MODEL_NAME", "")
    monkeypatch.setenv("AURA_API_KEY", "")
    monkeypatch.setenv("AURA_GENERIC_MODEL_ENDPOINT_URL", "")
    monkeypatch.setenv("AURA_GENERIC_MODEL_NAME", "")
    monkeypatch.setenv("AURA_GENERIC_MODEL_API_KEY", "")

    app.config.settings = Settings(
        aura_env="development",
        aura_app_name="AURA",
        aura_log_level="INFO",
        aura_model_provider="",
        aura_model_name="",
        aura_api_key="",
        _env_file=None,
    )
    yield
