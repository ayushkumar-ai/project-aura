"""M42/M52/M53/M54/M55 — Repositories package for Project AURA."""

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
from core.repositories.base_automation import BaseAutomationRepository
from core.repositories.base_webhook import BaseWebhookRepository
from core.repositories.base_fleet import BaseFleetRepository
from core.repositories.base_cognitive_memory import BaseCognitiveMemoryRepository
from core.repositories.base_multimodal import BaseMultimodalRepository
from core.repositories.in_memory_automation import InMemoryAutomationRepository
from core.repositories.in_memory_webhook import InMemoryWebhookRepository
from core.repositories.in_memory_fleet import InMemoryFleetRepository
from core.repositories.in_memory_cognitive_memory import InMemoryCognitiveMemoryRepository
from core.repositories.in_memory_multimodal import InMemoryMultimodalRepository
from core.repositories.postgres_automation import PostgresAutomationRepository
from core.repositories.postgres_webhook import PostgresWebhookRepository
from core.repositories.postgres_fleet import PostgresFleetRepository
from core.repositories.postgres_cognitive_memory import PostgresCognitiveMemoryRepository
from core.repositories.postgres_multimodal import PostgresMultimodalRepository
from core.repositories.postgres_task import PostgresTaskRepository
from core.repositories.postgres_approval import PostgresApprovalRepository
from core.repositories.factory import (
    RepositoryContainer,
    create_in_memory_repositories,
    create_postgres_repositories,
    create_repository_container,
)
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

__all__ = [
    "BaseUserRepository",
    "BaseUserPreferencesRepository",
    "BaseApiTokenRepository",
    "BaseConversationRepository",
    "BaseMemoryRepository",
    "BaseExperienceRepository",
    "BaseCheckpointRepository",
    "BaseKnowledgeRepository",
    "BaseVectorSearchRepository",
    "BaseTaskRepository",
    "BaseApprovalRepository",
    "BaseAutomationRepository",
    "BaseWebhookRepository",
    "BaseFleetRepository",
    "BaseCognitiveMemoryRepository",
    "BaseMultimodalRepository",
    "InMemoryUserRepository",
    "InMemoryUserPreferencesRepository",
    "InMemoryApiTokenRepository",
    "InMemoryConversationRepository",
    "InMemoryMemoryRepository",
    "InMemoryExperienceRepository",
    "InMemoryCheckpointRepository",
    "InMemoryKnowledgeRepository",
    "InMemoryVectorSearchRepository",
    "InMemoryTaskRepository",
    "InMemoryApprovalRepository",
    "InMemoryAutomationRepository",
    "InMemoryWebhookRepository",
    "InMemoryFleetRepository",
    "InMemoryCognitiveMemoryRepository",
    "InMemoryMultimodalRepository",
    "PostgresUserRepository",
    "PostgresUserPreferencesRepository",
    "PostgresApiTokenRepository",
    "PostgresConversationRepository",
    "PostgresMemoryRepository",
    "PostgresExperienceRepository",
    "PostgresCheckpointRepository",
    "PostgresKnowledgeRepository",
    "PostgresVectorSearchRepository",
    "PostgresTaskRepository",
    "PostgresApprovalRepository",
    "PostgresAutomationRepository",
    "PostgresWebhookRepository",
    "PostgresFleetRepository",
    "PostgresCognitiveMemoryRepository",
    "PostgresMultimodalRepository",
    "RepositoryContainer",
    "create_in_memory_repositories",
    "create_postgres_repositories",
    "create_repository_container",
]
