"""M45 — Production Configuration Validator for Project AURA.

Enforces strict validation rules on configuration settings when running in production mode,
guaranteeing fail-closed security and operational reliability.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger("aura.config.validator")

INSECURE_KEY_DEFAULTS = {
    "",
    "default",
    "change_me",
    "changeme",
    "secret",
    "password",
    "123456",
    "admin",
    "test",
    "dev",
    "aura",
}


def validate_production_config(config: Settings, raise_on_error: bool = False) -> tuple[bool, list[str]]:
    """Validate runtime configuration settings for production safety.
    
    Returns (is_valid, list_of_error_messages).
    If raise_on_error is True and validation fails, raises ValueError.
    """
    errors: list[str] = []
    env = getattr(config, "aura_env", "development").lower()

    if env == "production":
        # 1. Validate Master API Key / Auth configuration
        auth_enabled = getattr(config, "aura_api_key_auth_enabled", False)
        api_key = getattr(config, "aura_server_api_key", "").strip()

        if auth_enabled:
            if not api_key:
                errors.append("AURA_API_KEY_AUTH_ENABLED is true, but AURA_SERVER_API_KEY is not set.")
            elif len(api_key) < 16:
                errors.append(f"AURA_SERVER_API_KEY is too short ({len(api_key)} chars). Must be >= 16 characters in production.")
            elif api_key.lower() in INSECURE_KEY_DEFAULTS:
                errors.append("AURA_SERVER_API_KEY is set to a well-known insecure default value.")

        # 2. Validate Database Configuration
        backend = getattr(config, "aura_persistence_backend", "auto").lower()
        db_url = getattr(config, "aura_database_url", "").strip()

        if backend == "postgres" or (backend == "auto" and db_url):
            if not db_url:
                errors.append("aura_persistence_backend='postgres' requires non-empty AURA_DATABASE_URL.")
            elif not db_url.startswith(("postgresql://", "postgres://", "postgresql+psycopg2://")):
                errors.append("AURA_DATABASE_URL must specify a postgresql:// scheme in production.")

        # 3. Validate Request Limits
        max_body = getattr(config, "aura_max_request_body_bytes", 1048576)
        if max_body < 1024 or max_body > 52428800:
            errors.append(f"AURA_MAX_REQUEST_BODY_BYTES ({max_body}) is outside production safe bounds (1KB - 50MB).")

        # 4. Validate Graceful Shutdown Timeout
        shutdown_timeout = getattr(config, "aura_shutdown_grace_period_seconds", 10.0)
        if shutdown_timeout < 0.5 or shutdown_timeout > 300.0:
            errors.append(f"AURA_SHUTDOWN_GRACE_PERIOD_SECONDS ({shutdown_timeout}) is outside reasonable bounds (0.5s - 300s).")

        # 5. Validate CORS allowed origins in production
        cors_origin = getattr(config, "aura_cors_allowed_origins", "*")
        if cors_origin == "*":
            logger.warning("Production warning: AURA_CORS_ALLOWED_ORIGINS is set to wildcard '*' in production.")

    if errors:
        for err in errors:
            logger.error(f"Configuration validation error: {err}")
        if raise_on_error:
            raise ValueError("Production configuration validation failed:\n" + "\n".join(f"- {e}" for e in errors))
        return False, errors

    return True, []
