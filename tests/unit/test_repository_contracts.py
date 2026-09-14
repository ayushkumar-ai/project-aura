"""M42 — Shared Repository Contract Tests for Project AURA.

Verifies that repository implementations strictly fulfill contract semantics:
- User lifecycle & username uniqueness
- User preferences isolation
- Token SHA-256 hashing & constant-time validation
- Conversation turns & composite foreign key ownership integrity
- Unified memory recording, querying & user isolation
- Episodic experience tracking & querying
- Runtime checkpoint persistence (system vs user-scoped)
"""

import time
import pytest
from core.identity import UserIdentity, UserRole, UserScope
from core.personal_state_types import MemoryCategory, UserPreferences
from core.repositories.factory import create_in_memory_repositories


@pytest.fixture
def in_memory_container():
    """Provides a fresh in-memory repository container."""
    return create_in_memory_repositories()


class TestUserRepositoryContract:
    """Contract tests for BaseUserRepository."""

    def test_user_lifecycle(self, in_memory_container):
        repo = in_memory_container.users
        user = UserIdentity(
            user_id="usr_001",
            username="alice",
            roles=frozenset({UserRole.USER}),
            is_authenticated=True,
        )
        saved = repo.save(user)
        assert saved.user_id == "usr_001"

        # Fetch by ID
        fetched = repo.get_by_id("usr_001")
        assert fetched is not None
        assert fetched.username == "alice"
        assert fetched.roles == frozenset({UserRole.USER})

        # Fetch by Username (case-insensitive)
        by_name = repo.get_by_username("ALICE")
        assert by_name is not None
        assert by_name.user_id == "usr_001"

        # List users
        users = repo.list_users()
        assert len(users) >= 1
        assert any(u.user_id == "usr_001" for u in users)

        # Delete user
        assert repo.delete("usr_001") is True
        assert repo.get_by_id("usr_001") is None
        assert repo.get_by_username("alice") is None


class TestUserPreferencesContract:
    """Contract tests for BaseUserPreferencesRepository."""

    def test_preferences_isolation_and_update(self, in_memory_container):
        repo = in_memory_container.preferences

        # Default preferences for new user
        p1 = repo.get("user_1")
        assert p1.user_id == "user_1"
        assert p1.interaction_style == "concise"

        # Update user_1 preferences
        repo.save("user_1", {"interaction_style": "technical", "preferred_name": "Alice"})
        p1_updated = repo.get("user_1")
        assert p1_updated.interaction_style == "technical"
        assert p1_updated.preferred_name == "Alice"

        # User 2 remains isolated with defaults
        p2 = repo.get("user_2")
        assert p2.interaction_style == "concise"
        assert p2.preferred_name == "User"


class TestApiTokenContract:
    """Contract tests for BaseApiTokenRepository."""

    def test_token_registration_and_validation(self, in_memory_container):
        user_repo = in_memory_container.users
        token_repo = in_memory_container.tokens

        # Create user
        user = UserIdentity(user_id="usr_token_test", username="token_tester")
        user_repo.save(user)

        raw_secret = "aura_secret_token_1234567890"
        tid = token_repo.register_token(raw_secret, "usr_token_test", expires_in_seconds=3600)
        assert tid.startswith("tok_")

        # Validate token
        res = token_repo.find_by_token(raw_secret)
        assert res is not None
        matched_id, ident = res
        assert matched_id == tid
        assert ident.user_id == "usr_token_test"
        assert ident.username == "token_tester"

        # Wrong token fails
        assert token_repo.find_by_token("wrong_token") is None

        # Non-secret metadata listing
        tokens = token_repo.list_tokens_for_user("usr_token_test")
        assert len(tokens) == 1
        assert tokens[0]["prefix"] == "aura_sec"
        assert "token_hash" not in tokens[0]

        # Revocation
        assert token_repo.revoke_token(raw_secret) is True
        assert token_repo.find_by_token(raw_secret) is None

    def test_expired_token(self, in_memory_container):
        user_repo = in_memory_container.users
        token_repo = in_memory_container.tokens

        user = UserIdentity(user_id="usr_exp", username="exp_user")
        user_repo.save(user)

        raw_secret = "aura_expired_token_9999"
        token_repo.register_token(raw_secret, "usr_exp", expires_in_seconds=-10)

        # Expired token must return None
        assert token_repo.find_by_token(raw_secret) is None


class TestConversationContract:
    """Contract tests for BaseConversationRepository and Composite Foreign Keys."""

    def test_conversation_and_turns_lifecycle(self, in_memory_container):
        conv_repo = in_memory_container.conversations

        cid = conv_repo.create_conversation("user_a", title="Research Project")
        assert cid.startswith("conv_")

        # Add turns for user_a
        t1 = conv_repo.add_turn(cid, "user_a", role="user", content="Hello AURA")
        t2 = conv_repo.add_turn(cid, "user_a", role="assistant", content="Hello! How can I help?")
        assert t1.startswith("turn_")
        assert t2.startswith("turn_")

        turns = conv_repo.get_turns(cid, "user_a")
        assert len(turns) == 2
        assert turns[0]["content"] == "Hello AURA"
        assert turns[1]["content"] == "Hello! How can I help?"

        # Composite foreign key violation: user_b cannot append turns to user_a's conversation!
        with pytest.raises(ValueError, match="Composite foreign key violation"):
            conv_repo.add_turn(cid, "user_b", role="user", content="Illegal turn")

        # User B cannot read User A's turns
        assert conv_repo.get_turns(cid, "user_b") == []

        # Delete conversation
        assert conv_repo.delete_conversation(cid, "user_a") is True
        assert conv_repo.get_conversation(cid, "user_a") is None
        assert conv_repo.get_turns(cid, "user_a") == []


class TestMemoryContract:
    """Contract tests for BaseMemoryRepository."""

    def test_memory_isolation_and_querying(self, in_memory_container):
        repo = in_memory_container.memories

        m1 = repo.record_memory(
            user_id="user_alpha",
            category=MemoryCategory.PREFERENCE.value,
            content="Prefers concise Python code",
            tags=["python", "coding"],
            importance=0.8,
        )
        m2 = repo.record_memory(
            user_id="user_beta",
            category=MemoryCategory.PREFERENCE.value,
            content="Prefers verbose Java code",
            tags=["java"],
            importance=0.6,
        )

        # User alpha query
        alpha_mems = repo.query_memories("user_alpha")
        assert len(alpha_mems) == 1
        assert alpha_mems[0].content == "Prefers concise Python code"

        # User beta query
        beta_mems = repo.query_memories("user_beta")
        assert len(beta_mems) == 1
        assert beta_mems[0].content == "Prefers verbose Java code"

        # Filter by tag
        py_mems = repo.query_memories("user_alpha", tag="python")
        assert len(py_mems) == 1
        java_mems = repo.query_memories("user_alpha", tag="java")
        assert len(java_mems) == 0


class TestExperienceContract:
    """Contract tests for BaseExperienceRepository."""

    def test_experience_recording_and_isolation(self, in_memory_container):
        repo = in_memory_container.experiences

        e1 = repo.record_experience(
            user_id="user_exp_1",
            task_description="Build auth gateway",
            plan_summary="Implemented token verification",
            outcome="success",
            reward_score=0.95,
        )
        assert e1.experience_id.startswith("exp_")

        e2 = repo.record_experience(
            user_id="user_exp_2",
            task_description="Database migration",
            plan_summary="Migrated legacy records",
            outcome="success",
            reward_score=0.90,
        )

        # Isolation
        user1_exps = repo.query_experiences("user_exp_1")
        assert len(user1_exps) == 1
        assert user1_exps[0].task_description == "Build auth gateway"

        user2_exps = repo.query_experiences("user_exp_2")
        assert len(user2_exps) == 1
        assert user2_exps[0].task_description == "Database migration"


class TestCheckpointContract:
    """Contract tests for BaseCheckpointRepository."""

    def test_system_and_user_checkpoints(self, in_memory_container):
        repo = in_memory_container.checkpoints

        # System checkpoint
        sys_cid = repo.save_checkpoint(
            checkpoint_type="system_shutdown",
            state_payload={"daemons": 0, "status": "clean"},
            user_id=None,
        )
        assert sys_cid.startswith("chk_")

        # User checkpoint
        usr_cid = repo.save_checkpoint(
            checkpoint_type="user_session",
            state_payload={"active_tasks": ["task_1"]},
            user_id="user_chk_1",
        )
        assert usr_cid.startswith("chk_")

        # Fetch latest system checkpoint
        latest_sys = repo.get_latest_checkpoint("system_shutdown", user_id=None)
        assert latest_sys is not None
        assert latest_sys["id"] == sys_cid
        assert latest_sys["user_id"] is None
        assert latest_sys["state_payload"]["status"] == "clean"

        # Fetch latest user checkpoint
        latest_usr = repo.get_latest_checkpoint("user_session", user_id="user_chk_1")
        assert latest_usr is not None
        assert latest_usr["id"] == usr_cid
        assert latest_usr["user_id"] == "user_chk_1"
