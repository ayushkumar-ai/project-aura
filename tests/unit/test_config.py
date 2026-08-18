from app.config import Settings, settings


def test_settings_defaults():
    assert settings.aura_env == "development"
    assert settings.aura_app_name == "AURA"
    assert settings.aura_log_level == "INFO"
    assert settings.aura_model_provider == ""
    assert settings.aura_model_name == ""
    assert settings.aura_api_key == ""


def test_settings_environment_override(monkeypatch):
    monkeypatch.setenv("AURA_ENV", "testing")
    monkeypatch.setenv("AURA_APP_NAME", "AURA-Test")
    monkeypatch.setenv("AURA_LOG_LEVEL", "DEBUG")

    test_settings = Settings()

    assert test_settings.aura_env == "testing"
    assert test_settings.aura_app_name == "AURA-Test"
    assert test_settings.aura_log_level == "DEBUG"