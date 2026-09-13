from app.config import Settings, settings


def test_settings_defaults():
    test_settings = Settings(_env_file=None)
    assert test_settings.aura_env == "development"
    assert test_settings.aura_app_name == "AURA"
    assert test_settings.aura_log_level == "INFO"
    assert test_settings.aura_model_provider == ""
    assert test_settings.aura_model_name == ""
    assert test_settings.aura_api_key == ""


def test_settings_environment_override(monkeypatch):
    monkeypatch.setenv("AURA_ENV", "testing")
    monkeypatch.setenv("AURA_APP_NAME", "AURA-Test")
    monkeypatch.setenv("AURA_LOG_LEVEL", "DEBUG")

    test_settings = Settings()

    assert test_settings.aura_env == "testing"
    assert test_settings.aura_app_name == "AURA-Test"
    assert test_settings.aura_log_level == "DEBUG"