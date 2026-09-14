"""M42 — Database Connection Pooling, Transaction Management & Schema Migrations for Project AURA.

Provides authoritative PostgreSQL connectivity, connection pooling via psycopg_pool,
transaction management context managers, fail-closed health verification, and
idempotent schema migration execution.
"""

from __future__ import annotations

import hashlib
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Iterator

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
    PSYCOPG_AVAILABLE = True
except ImportError:
    psycopg = None  # type: ignore
    dict_row = None  # type: ignore
    ConnectionPool = None  # type: ignore
    PSYCOPG_AVAILABLE = False

logger = logging.getLogger("aura.database")


class DatabaseConnectionPool:
    """Manages PostgreSQL connection pooling, lifecycle, transactions, and health checks."""

    def __init__(
        self,
        connection_url: str,
        min_size: int = 1,
        max_size: int = 10,
        timeout: float = 30.0,
        is_production: bool = False,
    ) -> None:
        self.connection_url = connection_url
        self.min_size = max(1, min_size)
        self.max_size = max(self.min_size, max_size)
        self.timeout = timeout
        self.is_production = is_production
        self._pool: ConnectionPool | None = None
        self._is_closed = False

        if not PSYCOPG_AVAILABLE:
            if self.is_production:
                raise RuntimeError(
                    "psycopg / psycopg_pool driver is not installed. Production database connectivity is mandatory."
                )
            logger.warning("psycopg is not installed; PostgreSQL persistence will be unavailable.")
            return

        if not connection_url or not connection_url.strip():
            if self.is_production:
                raise RuntimeError(
                    "AURA_DATABASE_URL is missing or empty in production mode. Fail-closed: Cannot start without database."
                )
            logger.info("No database URL configured; running in volatile/in-memory mode.")
            return

        self._initialize_pool()

    def _initialize_pool(self) -> None:
        """Initialize the psycopg ConnectionPool."""
        try:
            self._pool = ConnectionPool(
                conninfo=self.connection_url,
                min_size=self.min_size,
                max_size=self.max_size,
                timeout=self.timeout,
                kwargs={"row_factory": dict_row},
                open=True,
            )
            # In production or when testing, wait for initial connection to verify reachability
            if self.is_production:
                self._pool.wait(timeout=min(self.timeout, 5.0))
            logger.info(
                f"PostgreSQL connection pool initialized (min={self.min_size}, max={self.max_size})"
            )
        except Exception as e:
            logger.error(f"Failed to initialize PostgreSQL connection pool: {e}")
            if self._pool is not None:
                try:
                    self._pool.close()
                except Exception:
                    pass
                self._pool = None
            if self.is_production:
                raise RuntimeError(f"Production database connection pool failed to initialize: {e}") from e
            self._pool = None

    @property
    def is_active(self) -> bool:
        """Check if pool is instantiated and not closed."""
        return self._pool is not None and not self._is_closed

    @contextmanager
    def connection(self) -> Generator[Any, None, None]:
        """Obtain a connection from the pool with automatic release."""
        if not self.is_active or self._pool is None:
            raise RuntimeError("Database connection pool is not available or closed.")
        with self._pool.connection() as conn:
            yield conn

    @contextmanager
    def transaction(self) -> Generator[Any, None, None]:
        """Obtain a connection and wrap operations in an atomic transaction."""
        if not self.is_active or self._pool is None:
            raise RuntimeError("Database connection pool is not available or closed.")
        with self._pool.connection() as conn:
            with conn.transaction():
                yield conn

    def check_health(self) -> dict[str, Any]:
        """Verify database responsiveness (SELECT 1) and return diagnostic status."""
        if not self.is_active:
            return {
                "status": "unavailable" if self.is_production else "disabled",
                "connected": False,
                "error": "Connection pool inactive" if not self.is_production else "Database connection pool inactive",
            }

        try:
            with self.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 AS alive;")
                    row = cur.fetchone()
                    if row and row.get("alive") == 1:
                        return {
                            "status": "healthy",
                            "connected": True,
                            "min_pool_size": self.min_size,
                            "max_pool_size": self.max_size,
                        }
                    return {
                        "status": "degraded",
                        "connected": True,
                        "error": f"Unexpected health check response: {row}",
                    }
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return {
                "status": "unhealthy",
                "connected": False,
                "error": str(e),
            }

    def close(self) -> None:
        """Gracefully close all pooled connections."""
        self._is_closed = True
        if self._pool is not None:
            try:
                self._pool.close()
                logger.info("PostgreSQL connection pool closed.")
            except Exception as e:
                logger.warning(f"Error while closing database connection pool: {e}")
            finally:
                self._pool = None


class MigrationRunner:
    """Executes schema migrations idempotently and verifies migration checksums."""

    def __init__(self, pool: DatabaseConnectionPool, migrations_dir: str | Path | None = None) -> None:
        self.pool = pool
        self.migrations_dir = (
            Path(migrations_dir)
            if migrations_dir
            else Path(__file__).resolve().parent.parent / "migrations"
        )

    def run_migrations(self) -> list[str]:
        """Discover, validate, and execute all pending migrations in order."""
        if not self.pool.is_active:
            logger.info("Database pool not active; skipping schema migrations.")
            return []

        if not self.migrations_dir.exists():
            logger.warning(f"Migrations directory '{self.migrations_dir}' not found.")
            return []

        applied_migrations: list[str] = []
        sql_files = sorted(self.migrations_dir.glob("*.sql"))

        with self.pool.connection() as conn:
            # Ensure schema_migrations table exists
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version VARCHAR(64) PRIMARY KEY,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        checksum VARCHAR(64) NOT NULL
                    );
                    """
                )
                conn.commit()

            # Retrieve previously applied migrations
            with conn.cursor() as cur:
                cur.execute("SELECT version, checksum FROM schema_migrations ORDER BY version ASC;")
                existing = {row["version"]: row["checksum"] for row in cur.fetchall()}

            for sql_file in sql_files:
                version = sql_file.name
                content = sql_file.read_text(encoding="utf-8")
                file_checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()

                if version in existing:
                    recorded_checksum = existing[version]
                    if recorded_checksum != file_checksum:
                        err_msg = (
                            f"Migration '{version}' checksum mismatch! "
                            f"Recorded: {recorded_checksum}, File: {file_checksum}"
                        )
                        logger.critical(err_msg)
                        raise ValueError(err_msg)
                    logger.debug(f"Migration '{version}' already applied.")
                    continue

                logger.info(f"Applying schema migration: {version}")
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(content)
                        cur.execute(
                            "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s);",
                            (version, file_checksum),
                        )
                applied_migrations.append(version)
                logger.info(f"Successfully applied migration: {version}")

        return applied_migrations
