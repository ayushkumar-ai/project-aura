"""M42 — Database Connection Pool & Fail-Closed Unit Tests for Project AURA."""

import pytest
from core.database import DatabaseConnectionPool, MigrationRunner
from core.repositories.factory import create_repository_container
from app.config import Settings


def test_database_pool_inactive_in_dev():
    """Verify that empty database URL in development returns inactive pool without raising."""
    pool = DatabaseConnectionPool(connection_url="", is_production=False)
    assert not pool.is_active
    health = pool.check_health()
    assert health["connected"] is False
    assert health["status"] == "disabled"


def test_database_pool_fail_closed_in_production():
    """Verify that empty database URL in production raises fail-closed RuntimeError."""
    with pytest.raises(RuntimeError, match="Fail-closed: Cannot start without database"):
        DatabaseConnectionPool(connection_url="", is_production=True)


def test_database_pool_invalid_connection_in_production():
    """Verify that invalid connection string in production fails closed."""
    with pytest.raises(RuntimeError, match="Production database connection pool failed to initialize"):
        DatabaseConnectionPool(
            connection_url="postgresql://invalid_user:invalid_pass@127.0.0.1:9999/nonexistent_db",
            is_production=True,
            timeout=1.0,
        )


def test_repository_container_fail_closed_in_production():
    """Verify create_repository_container fails closed when postgres backend is requested without valid DB."""
    prod_settings = Settings(
        aura_env="production",
        aura_persistence_backend="postgres",
        aura_database_url="",
    )
    with pytest.raises(RuntimeError, match="AURA_DATABASE_URL is required when aura_persistence_backend='postgres'"):
        create_repository_container(config=prod_settings)


def test_repository_container_postgres_backend_invalid_url_fails_closed():
    """Verify that setting backend to 'postgres' with unreachable database fails closed."""
    prod_settings = Settings(
        aura_env="production",
        aura_persistence_backend="postgres",
        aura_database_url="postgresql://invalid_user:invalid_pass@127.0.0.1:9999/nonexistent_db",
    )
    with pytest.raises(RuntimeError, match="Production database connection pool failed to initialize"):
        create_repository_container(config=prod_settings)


def test_repository_container_fallback_to_memory_in_dev():
    """Verify that development environment gracefully initializes in-memory repository container."""
    dev_settings = Settings(
        aura_env="development",
        aura_database_url="",
    )
    container = create_repository_container(config=dev_settings)
    assert not container.is_postgres
    assert container.users is not None
    assert container.preferences is not None
    assert container.conversations is not None
    assert container.tokens is not None
