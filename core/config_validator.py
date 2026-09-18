"""M45 — Production Configuration Validator for Project AURA.

Enforces strict validation rules on configuration settings when running in production mode,
guaranteeing fail-closed security and operational reliability.
"""

from __future__ import annotations

import ipaddress
import logging
import urllib.parse
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
    "aura-default-master-key-32bytes!!",
}


def validate_production_config(
    config: Settings,
    raise_on_error: bool = False,
    authenticator_provided: bool = False,
) -> tuple[bool, list[str]]:
    """Validate runtime configuration settings for production safety.
    
    Returns (is_valid, list_of_error_messages).
    If raise_on_error is True and validation fails, raises ValueError.
    """
    errors: list[str] = []
    env = getattr(config, "aura_env", "development").lower()

    # 1. Validate Trusted Proxy CIDRs (applicable in all modes if configured)
    trusted_cidrs_raw = getattr(config, "aura_trusted_proxy_cidrs", "").strip()
    if trusted_cidrs_raw:
        for cidr_str in trusted_cidrs_raw.split(","):
            c = cidr_str.strip()
            if not c:
                continue
            try:
                ipaddress.ip_network(c, strict=False)
            except ValueError:
                errors.append(f"Invalid trusted proxy CIDR format: '{c}'. Must be a valid IPv4/IPv6 CIDR or IP address.")

    # 2. Validate Rate Limiting Configuration
    rate_limit_enabled = getattr(config, "aura_rate_limit_enabled", True)
    if rate_limit_enabled:
        rpm = getattr(config, "aura_rate_limit_requests_per_minute", 60)
        burst = getattr(config, "aura_rate_limit_burst_size", 10)
        max_buckets = getattr(config, "aura_rate_limit_max_buckets", 50000)
        cleanup_interval = getattr(config, "aura_rate_limit_cleanup_interval_seconds", 3600.0)

        if rpm <= 0:
            errors.append(f"AURA_RATE_LIMIT_REQUESTS_PER_MINUTE ({rpm}) must be greater than 0.")
        if burst <= 0:
            errors.append(f"AURA_RATE_LIMIT_BURST_SIZE ({burst}) must be greater than 0.")
        if max_buckets < 100:
            errors.append(f"AURA_RATE_LIMIT_MAX_BUCKETS ({max_buckets}) must be >= 100.")
        if cleanup_interval < 1.0:
            errors.append(f"AURA_RATE_LIMIT_CLEANUP_INTERVAL_SECONDS ({cleanup_interval}) must be >= 1.0.")

    if env == "production":
        # 3. Validate Master API Key / Auth configuration
        auth_enabled = getattr(config, "aura_api_key_auth_enabled", False)
        api_key = getattr(config, "aura_server_api_key", "").strip()

        if auth_enabled and not authenticator_provided:
            if not api_key:
                errors.append("AURA_API_KEY_AUTH_ENABLED is true, but AURA_SERVER_API_KEY is not set.")
            elif api_key.lower() in INSECURE_KEY_DEFAULTS:
                errors.append("AURA_SERVER_API_KEY is set to a well-known insecure default value.")
            elif len(api_key) < 16:
                errors.append(f"AURA_SERVER_API_KEY is too short ({len(api_key)} chars). Must be >= 16 characters in production.")

        # 4. Validate Database Configuration
        backend = getattr(config, "aura_persistence_backend", "auto").lower()
        db_url = getattr(config, "aura_database_url", "").strip()

        if backend == "postgres" or (backend == "auto" and db_url):
            if not db_url:
                errors.append("aura_persistence_backend='postgres' requires non-empty AURA_DATABASE_URL.")
            elif not db_url.startswith(("postgresql://", "postgres://", "postgresql+psycopg2://")):
                errors.append("AURA_DATABASE_URL must specify a postgresql:// scheme in production.")

        # 5. Validate Request Limits
        max_body = getattr(config, "aura_max_request_body_bytes", 1048576)
        if max_body < 1024 or max_body > 52428800:
            errors.append(f"AURA_MAX_REQUEST_BODY_BYTES ({max_body}) is outside production safe bounds (1KB - 50MB).")

        # 6. Validate Graceful Shutdown Timeout
        shutdown_timeout = getattr(config, "aura_shutdown_grace_period_seconds", 10.0)
        if shutdown_timeout < 0.5 or shutdown_timeout > 300.0:
            errors.append(f"AURA_SHUTDOWN_GRACE_PERIOD_SECONDS ({shutdown_timeout}) is outside reasonable bounds (0.5s - 300s).")

        # 7. Validate CORS allowed origins in production (Fail-closed)
        cors_origin = getattr(config, "aura_cors_allowed_origins", "").strip()
        allow_credentials = getattr(config, "aura_cors_allow_credentials", False)

        if not cors_origin or cors_origin == "*":
            errors.append(
                "AURA_CORS_ALLOWED_ORIGINS cannot be empty or wildcard '*' in production mode. Explicit allowed origins required."
            )
        else:
            origins = [o.strip() for o in cors_origin.split(",") if o.strip()]
            if "*" in origins:
                errors.append("AURA_CORS_ALLOWED_ORIGINS cannot contain wildcard '*' in production mode.")
            if allow_credentials and "*" in origins:
                errors.append("AURA_CORS_ALLOW_CREDENTIALS cannot be true with wildcard origin.")
            for o in origins:
                parsed = urllib.parse.urlparse(o)
                if not (parsed.scheme in ("http", "https") and parsed.netloc):
                    errors.append(f"Invalid CORS origin format in production: '{o}'. Must be valid absolute URL (e.g. https://app.example.com).")

        # 8. Validate Persistent Storage Paths
        for path_attr in ("aura_artifact_storage_dir", "aura_knowledge_storage_dir", "aura_checkpoint_dir"):
            p = getattr(config, path_attr, None)
            if p is not None and not str(p).strip():
                errors.append(f"Storage path '{path_attr}' cannot be empty string in production.")

    if errors:
        for err in errors:
            logger.error(f"Configuration validation error: {err}")
        if raise_on_error:
            raise ValueError("Production configuration validation failed:\n" + "\n".join(f"- {e}" for e in errors))
        return False, errors

    return True, []
