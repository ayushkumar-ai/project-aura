"""M42/M52/M53/M54/M55 — Repository Factory & Dependency Resolution for Project AURA.

Constructs and wires appropriate repository implementations (PostgreSQL vs In-Memory)
based on runtime configuration and environment settings.
Enforces fail-closed semantics in production mode.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import Settings, settings
from core.database import DatabaseConnectionPool, MigrationRunner
from core.repositories.base import (
    BaseApiTokenRepository,
    BaseApprovalRepository,
    BaseAutomationRepository,
    BaseCheckpointRepository,
    BaseConversationRepository,
    BaseExperienceRepository,
    BaseKnowledgeRepository,
    BaseMemoryRepository,
    BaseTaskRepository,
    BaseUserPreferencesRepository,
    BaseUserRepository,
    BaseVectorSearchRepository,
)
from core.repositories.base_cognitive_memory import BaseCognitiveMemoryRepository
from core.repositories.base_webhook import BaseWebhookRepository
from core.repositories.base_fleet import BaseFleetRepository
from core.repositories.base_multimodal import BaseMultimodalRepository
from core.repositories.base_platform import BasePlatformRepository
from core.repositories.base_agent_mesh import BaseAgentMeshRepository
from core.repositories.in_memory import (
    InMemoryApiTokenRepository,
    InMemoryApprovalRepository,
    InMemoryCheckpointRepository,
    InMemoryConversationRepository,
    InMemoryExperienceRepository,
    InMemoryKnowledgeRepository,
    InMemoryMemoryRepository,
    InMemoryTaskRepository,
    InMemoryUserPreferencesRepository,
    InMemoryUserRepository,
    InMemoryVectorSearchRepository,
)
from core.repositories.in_memory_automation import InMemoryAutomationRepository
from core.repositories.in_memory_webhook import InMemoryWebhookRepository
from core.repositories.in_memory_fleet import InMemoryFleetRepository
from core.repositories.in_memory_cognitive_memory import InMemoryCognitiveMemoryRepository
from core.repositories.in_memory_multimodal import InMemoryMultimodalRepository
from core.repositories.in_memory_platform import InMemoryPlatformRepository
from core.repositories.in_memory_agent_mesh import InMemoryAgentMeshRepository
from core.repositories.postgres import (
    PostgresApiTokenRepository,
    PostgresCheckpointRepository,
    PostgresConversationRepository,
    PostgresExperienceRepository,
    PostgresKnowledgeRepository,
    PostgresMemoryRepository,
    PostgresUserPreferencesRepository,
    PostgresUserRepository,
    PostgresVectorSearchRepository,
)
from core.repositories.postgres_task import PostgresTaskRepository
from core.repositories.postgres_approval import PostgresApprovalRepository
from core.repositories.postgres_automation import PostgresAutomationRepository
from core.repositories.postgres_webhook import PostgresWebhookRepository
from core.repositories.postgres_fleet import PostgresFleetRepository
from core.repositories.postgres_cognitive_memory import PostgresCognitiveMemoryRepository
from core.repositories.postgres_multimodal import PostgresMultimodalRepository
from core.repositories.postgres_platform import PostgresPlatformRepository
from core.repositories.postgres_agent_mesh import PostgresAgentMeshRepository


logger = logging.getLogger("aura.repositories.factory")


@dataclass
class RepositoryContainer:
    """Holds active repository instances for the AURA runtime."""

    users: BaseUserRepository
    preferences: BaseUserPreferencesRepository
    tokens: BaseApiTokenRepository
    conversations: BaseConversationRepository
    memories: BaseMemoryRepository
    experiences: BaseExperienceRepository
    checkpoints: BaseCheckpointRepository
    knowledge: BaseKnowledgeRepository
    vectors: BaseVectorSearchRepository
    tasks: BaseTaskRepository | None = None
    approvals: BaseApprovalRepository | None = None
    automations: BaseAutomationRepository | None = None
    webhooks: BaseWebhookRepository | None = None
    fleet: BaseFleetRepository | None = None
    cognitive_memories: BaseCognitiveMemoryRepository | None = None
    multimodal: BaseMultimodalRepository | None = None
    platform: BasePlatformRepository | None = None
    agent_mesh: BaseAgentMeshRepository | None = None
    db_pool: DatabaseConnectionPool | None = None
    is_postgres: bool = False


def create_in_memory_repositories() -> RepositoryContainer:
    """Construct standalone in-memory repository container for testing and development."""
    user_repo = InMemoryUserRepository()
    prefs_repo = InMemoryUserPreferencesRepository()
    token_repo = InMemoryApiTokenRepository(user_repo=user_repo)
    conv_repo = InMemoryConversationRepository()
    mem_repo = InMemoryMemoryRepository()
    exp_repo = InMemoryExperienceRepository()
    chk_repo = InMemoryCheckpointRepository()
    know_repo = InMemoryKnowledgeRepository()
    vec_repo = InMemoryVectorSearchRepository(
        knowledge_repo=know_repo,
        memory_repo=mem_repo,
        experience_repo=exp_repo,
    )
    task_repo = InMemoryTaskRepository()
    approval_repo = InMemoryApprovalRepository(task_repo=task_repo)
    automation_repo = InMemoryAutomationRepository(task_repo=task_repo)
    webhook_repo = InMemoryWebhookRepository()
    fleet_repo = InMemoryFleetRepository(task_repo=task_repo)
    cog_repo = InMemoryCognitiveMemoryRepository()
    mm_repo = InMemoryMultimodalRepository()
    platform_repo = InMemoryPlatformRepository()
    agent_mesh_repo = InMemoryAgentMeshRepository()

    return RepositoryContainer(
        users=user_repo,
        preferences=prefs_repo,
        tokens=token_repo,
        conversations=conv_repo,
        memories=mem_repo,
        experiences=exp_repo,
        checkpoints=chk_repo,
        knowledge=know_repo,
        vectors=vec_repo,
        tasks=task_repo,
        approvals=approval_repo,
        automations=automation_repo,
        webhooks=webhook_repo,
        fleet=fleet_repo,
        cognitive_memories=cog_repo,
        multimodal=mm_repo,
        platform=platform_repo,
        agent_mesh=agent_mesh_repo,
        db_pool=None,
        is_postgres=False,
    )


def create_postgres_repositories(db_pool: DatabaseConnectionPool) -> RepositoryContainer:
    """Construct PostgreSQL repository container bound to a live connection pool."""
    if not db_pool.is_active:
        raise RuntimeError("Cannot construct PostgreSQL repositories: Database connection pool is inactive.")

    user_repo = PostgresUserRepository(db_pool)
    prefs_repo = PostgresUserPreferencesRepository(db_pool)
    token_repo = PostgresApiTokenRepository(db_pool, user_repo=user_repo)
    conv_repo = PostgresConversationRepository(db_pool)
    mem_repo = PostgresMemoryRepository(db_pool)
    exp_repo = PostgresExperienceRepository(db_pool)
    chk_repo = PostgresCheckpointRepository(db_pool)
    know_repo = PostgresKnowledgeRepository(db_pool)
    vec_repo = PostgresVectorSearchRepository(db_pool)
    task_repo = PostgresTaskRepository(db_pool)
    approval_repo = PostgresApprovalRepository(db_pool)
    automation_repo = PostgresAutomationRepository(db_pool)
    webhook_repo = PostgresWebhookRepository(db_pool)
    fleet_repo = PostgresFleetRepository(db_pool)
    cog_repo = PostgresCognitiveMemoryRepository(db_pool)
    mm_repo = PostgresMultimodalRepository(db_pool)
    platform_repo = PostgresPlatformRepository(db_pool)
    agent_mesh_repo = PostgresAgentMeshRepository(db_pool)

    return RepositoryContainer(
        users=user_repo,
        preferences=prefs_repo,
        tokens=token_repo,
        conversations=conv_repo,
        memories=mem_repo,
        experiences=exp_repo,
        checkpoints=chk_repo,
        knowledge=know_repo,
        vectors=vec_repo,
        tasks=task_repo,
        approvals=approval_repo,
        automations=automation_repo,
        webhooks=webhook_repo,
        fleet=fleet_repo,
        cognitive_memories=cog_repo,
        multimodal=mm_repo,
        platform=platform_repo,
        agent_mesh=agent_mesh_repo,
        db_pool=db_pool,
        is_postgres=True,
    )



def create_repository_container(
    config: Settings | None = None,
    db_pool: DatabaseConnectionPool | None = None,
    auto_migrate: bool = True,
) -> RepositoryContainer:
    """Resolve and construct the authoritative repository container.
    
    Persistence Authority Rules:
    - If persistence_backend == 'postgres', PostgreSQL connection is mandatory (fail-closed).
    - If database_url is provided, PostgreSQL is authoritative; failures fail-closed (no fallback).
    - If no database_url is provided and backend is 'auto' or 'memory', uses isolated in-memory repositories.
    """
    cfg = config or settings
    is_production = cfg.aura_env.lower() == "production"
    backend = getattr(cfg, "aura_persistence_backend", "auto").lower()

    if backend == "memory":
        logger.info("Explicitly configured 'memory' persistence backend.")
        return create_in_memory_repositories()

    # If pool is explicitly provided
    if db_pool is not None:
        if db_pool.is_active:
            if auto_migrate:
                runner = MigrationRunner(db_pool)
                runner.run_migrations()
            return create_postgres_repositories(db_pool)
        elif is_production or backend == "postgres":
            raise RuntimeError("Database connection pool is not active. Fail-closed.")

    db_url = getattr(cfg, "aura_database_url", "")

    if backend == "postgres":
        if not db_url or not db_url.strip():
            raise RuntimeError("AURA_DATABASE_URL is required when aura_persistence_backend='postgres'. Fail-closed.")
        pool = DatabaseConnectionPool(
            connection_url=db_url,
            min_size=getattr(cfg, "aura_database_pool_min_connections", 1),
            max_size=getattr(cfg, "aura_database_pool_max_connections", 10),
            timeout=getattr(cfg, "aura_database_pool_timeout_seconds", 30.0),
            is_production=True,
        )
        if not pool.is_active:
            raise RuntimeError("Failed to connect to PostgreSQL database. Fail-closed.")
        if auto_migrate and getattr(cfg, "aura_database_auto_migrate", True):
            runner = MigrationRunner(pool)
            runner.run_migrations()
        return create_postgres_repositories(pool)

    if db_url and db_url.strip():
        pool = DatabaseConnectionPool(
            connection_url=db_url,
            min_size=getattr(cfg, "aura_database_pool_min_connections", 1),
            max_size=getattr(cfg, "aura_database_pool_max_connections", 10),
            timeout=getattr(cfg, "aura_database_pool_timeout_seconds", 30.0),
            is_production=is_production,
        )
        if pool.is_active:
            if auto_migrate and getattr(cfg, "aura_database_auto_migrate", True):
                runner = MigrationRunner(pool)
                runner.run_migrations()
            return create_postgres_repositories(pool)
        elif is_production:
            raise RuntimeError("Failed to connect to PostgreSQL database in production. Fail-closed: No fallback allowed.")
        else:
            logger.warning("PostgreSQL connection inactive; falling back to in-memory repository for dev.")
            return create_in_memory_repositories()

    logger.info("Initializing in-memory repository container for development/testing.")
    return create_in_memory_repositories()
