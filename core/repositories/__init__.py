"""M42 — Repositories package for Project AURA."""

from core.repositories.base import (
    BaseApiTokenRepository,
    BaseCheckpointRepository,
    BaseConversationRepository,
    BaseExperienceRepository,
    BaseMemoryRepository,
    BaseUserPreferencesRepository,
    BaseUserRepository,
)
from core.repositories.factory import (
    RepositoryContainer,
    create_in_memory_repositories,
    create_postgres_repositories,
    create_repository_container,
)
from core.repositories.in_memory import (
    InMemoryApiTokenRepository,
    InMemoryCheckpointRepository,
    InMemoryConversationRepository,
    InMemoryExperienceRepository,
    InMemoryMemoryRepository,
    InMemoryUserPreferencesRepository,
    InMemoryUserRepository,
)
from core.repositories.postgres import (
    PostgresApiTokenRepository,
    PostgresCheckpointRepository,
    PostgresConversationRepository,
    PostgresExperienceRepository,
    PostgresMemoryRepository,
    PostgresUserPreferencesRepository,
    PostgresUserRepository,
)

__all__ = [
    "BaseUserRepository",
    "BaseUserPreferencesRepository",
    "BaseApiTokenRepository",
    "BaseConversationRepository",
    "BaseMemoryRepository",
    "BaseExperienceRepository",
    "BaseCheckpointRepository",
    "InMemoryUserRepository",
    "InMemoryUserPreferencesRepository",
    "InMemoryApiTokenRepository",
    "InMemoryConversationRepository",
    "InMemoryMemoryRepository",
    "InMemoryExperienceRepository",
    "InMemoryCheckpointRepository",
    "PostgresUserRepository",
    "PostgresUserPreferencesRepository",
    "PostgresApiTokenRepository",
    "PostgresConversationRepository",
    "PostgresMemoryRepository",
    "PostgresExperienceRepository",
    "PostgresCheckpointRepository",
    "RepositoryContainer",
    "create_in_memory_repositories",
    "create_postgres_repositories",
    "create_repository_container",
]
