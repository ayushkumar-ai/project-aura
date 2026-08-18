from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration for Project AURA."""

    aura_env: str = "development"
    aura_app_name: str = "AURA"
    aura_log_level: str = "INFO"

    aura_model_provider: str = ""
    aura_model_name: str = ""

    aura_api_key: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


settings = Settings()