"""M42 — Abstract Repository Interfaces for Project AURA.

Defines repository contracts for multi-user isolation, credential management,
conversations, episodic memories, experiences, and runtime checkpoints.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.identity import UserIdentity
from core.personal_state_types import (
    EpisodicExperienceRecord,
    PersonalStateSnapshot,
    UnifiedMemoryRecord,
    UserPreferences,
)


class BaseUserRepository(ABC):
    """Abstract repository for user identity lifecycle management."""

    @abstractmethod
    def get_by_id(self, user_id: str) -> UserIdentity | None:
        """Fetch user by canonical user_id."""
        pass

    @abstractmethod
    def get_by_username(self, username: str) -> UserIdentity | None:
        """Fetch user by unique username."""
        pass

    @abstractmethod
    def save(self, user: UserIdentity) -> UserIdentity:
        """Create or update user identity."""
        pass

    @abstractmethod
    def delete(self, user_id: str) -> bool:
        """Delete user identity by user_id."""
        pass

    @abstractmethod
    def list_users(self, limit: int = 100, offset: int = 0) -> list[UserIdentity]:
        """List user identities."""
        pass


class BaseUserPreferencesRepository(ABC):
    """Abstract repository for isolated user preferences."""

    @abstractmethod
    def get(self, user_id: str) -> UserPreferences:
        """Fetch preferences for a specific user_id."""
        pass

    @abstractmethod
    def save(self, user_id: str, preferences: UserPreferences | dict[str, Any]) -> UserPreferences:
        """Save or merge preferences for a specific user_id."""
        pass

    @abstractmethod
    def delete(self, user_id: str) -> bool:
        """Delete preferences for a specific user_id."""
        pass


class BaseApiTokenRepository(ABC):
    """Abstract repository for secure hashed token persistence."""

    @abstractmethod
    def register_token(
        self,
        raw_token: str,
        user_id: str,
        expires_in_seconds: float = 2592000,
        rate_limit_rpm: int = 60,
        rate_limit_tpm: int = 60000,
        token_id: str | None = None,
    ) -> str:
        """Hash and persist an API/Bearer token. Returns the token record ID."""
        pass

    @abstractmethod
    def find_by_token(self, raw_token: str) -> tuple[str, UserIdentity] | None:
        """Find active, unexpired identity by comparing token hash. Returns (token_id, identity) or None."""
        pass

    @abstractmethod
    def revoke_token(self, raw_token: str) -> bool:
        """Revoke a token by its raw secret."""
        pass

    @abstractmethod
    def revoke_token_by_id(self, token_id: str, user_id: str | None = None) -> bool:
        """Revoke a token by its record ID, optionally verifying ownership."""
        pass

    @abstractmethod
    def list_tokens_for_user(self, user_id: str) -> list[dict[str, Any]]:
        """List non-secret token metadata (prefix, created_at, expires_at, is_active) for a user."""
        pass


class BaseConversationRepository(ABC):
    """Abstract repository for multi-user conversation history and turns."""

    @abstractmethod
    def create_conversation(
        self,
        user_id: str,
        title: str = "",
        metadata: dict[str, Any] | None = None,
        conversation_id: str | None = None,
    ) -> str:
        """Create a new conversation belonging to user_id. Returns conversation_id."""
        pass

    @abstractmethod
    def get_conversation(self, conversation_id: str, user_id: str) -> dict[str, Any] | None:
        """Fetch conversation metadata if owned by user_id."""
        pass

    @abstractmethod
    def list_conversations(self, user_id: str, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """List all conversations owned by user_id."""
        pass

    @abstractmethod
    def delete_conversation(self, conversation_id: str, user_id: str) -> bool:
        """Delete conversation and all associated turns owned by user_id."""
        pass

    @abstractmethod
    def add_turn(
        self,
        conversation_id: str,
        user_id: str,
        role: str,
        content: str,
        turn_index: int | None = None,
        model: str = "",
        metadata: dict[str, Any] | None = None,
        turn_id: str | None = None,
    ) -> str:
        """Add a conversation turn, strictly validating user ownership. Returns turn_id."""
        pass

    @abstractmethod
    def get_turns(self, conversation_id: str, user_id: str) -> list[dict[str, Any]]:
        """Retrieve all turns for a conversation owned by user_id."""
        pass

    @abstractmethod
    def clear_turns(self, conversation_id: str, user_id: str) -> int:
        """Delete all turns for a conversation owned by user_id."""
        pass


class BaseMemoryRepository(ABC):
    """Abstract repository for isolated semantic and unified memories."""

    @abstractmethod
    def record_memory(
        self,
        user_id: str,
        category: str,
        content: str,
        confidence: float = 1.0,
        importance: float = 0.5,
        tags: list[str] | None = None,
        provenance: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        memory_id: str | None = None,
    ) -> UnifiedMemoryRecord:
        """Persist a memory record strictly scoped to user_id."""
        pass

    @abstractmethod
    def query_memories(
        self,
        user_id: str,
        category: str | None = None,
        tag: str | None = None,
        query: str = "",
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[UnifiedMemoryRecord]:
        """Query memory records belonging to user_id."""
        pass

    @abstractmethod
    def get_memory(self, memory_id: str, user_id: str) -> UnifiedMemoryRecord | None:
        """Get a single memory record if owned by user_id."""
        pass

    @abstractmethod
    def delete_memory(self, memory_id: str, user_id: str) -> bool:
        """Delete memory record if owned by user_id."""
        pass

    @abstractmethod
    def clear_memories(self, user_id: str) -> int:
        """Delete all memories for user_id."""
        pass


class BaseExperienceRepository(ABC):
    """Abstract repository for episodic experience records."""

    @abstractmethod
    def record_experience(
        self,
        user_id: str,
        task_description: str,
        plan_summary: str,
        action_sequence: list[str] | None = None,
        outcome: str = "success",
        reward_score: float = 1.0,
        lessons_learned: list[str] | None = None,
        provenance: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        experience_id: str | None = None,
    ) -> EpisodicExperienceRecord:
        """Record an episodic experience scoped to user_id."""
        pass

    @abstractmethod
    def query_experiences(
        self,
        user_id: str,
        outcome: str | None = None,
        query: str = "",
        limit: int = 20,
    ) -> list[EpisodicExperienceRecord]:
        """Query episodic experiences belonging to user_id."""
        pass

    @abstractmethod
    def get_experience(self, experience_id: str, user_id: str) -> EpisodicExperienceRecord | None:
        """Get a single experience record if owned by user_id."""
        pass

    @abstractmethod
    def delete_experience(self, experience_id: str, user_id: str) -> bool:
        """Delete experience record if owned by user_id."""
        pass


class BaseCheckpointRepository(ABC):
    """Abstract repository for system and user state checkpoints."""

    @abstractmethod
    def save_checkpoint(
        self,
        checkpoint_type: str,
        state_payload: dict[str, Any],
        user_id: str | None = None,
        checkpoint_id: str | None = None,
    ) -> str:
        """Persist a checkpoint (user_id is None for system checkpoints). Returns checkpoint_id."""
        pass

    @abstractmethod
    def get_latest_checkpoint(
        self,
        checkpoint_type: str,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Retrieve the latest checkpoint by type and optional user_id."""
        pass

    @abstractmethod
    def get_checkpoint(
        self,
        checkpoint_id: str,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Retrieve a specific checkpoint by ID."""
        pass

    @abstractmethod
    def list_checkpoints(
        self,
        checkpoint_type: str | None = None,
        user_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """List checkpoints with optional type and user filters."""
        pass

    @abstractmethod
    def delete_checkpoint(
        self,
        checkpoint_id: str,
        user_id: str | None = None,
    ) -> bool:
        """Delete a checkpoint."""
        pass
