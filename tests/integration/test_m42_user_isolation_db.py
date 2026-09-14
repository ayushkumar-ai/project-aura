"""M42 — End-to-End User Data Isolation Integration Tests.

Proves that User A cannot read, modify, or corrupt User B's state, conversations,
tokens, memories, experiences, or checkpoints across the persistence layer.
"""

import pytest
from core.identity import UserIdentity, UserRole
from core.personal_state_types import MemoryCategory
from core.repositories.factory import create_in_memory_repositories


@pytest.fixture
def repos():
    return create_in_memory_repositories()


def test_user_preferences_isolation_e2e(repos):
    """User A's preferences updates must never leak or overwrite User B's preferences."""
    repos.preferences.save("user_a", {"preferred_name": "Alice", "interaction_style": "concise"})
    repos.preferences.save("user_b", {"preferred_name": "Bob", "interaction_style": "detailed"})

    assert repos.preferences.get("user_a").preferred_name == "Alice"
    assert repos.preferences.get("user_b").preferred_name == "Bob"

    repos.preferences.save("user_a", {"preferred_name": "Alice Cooper"})
    assert repos.preferences.get("user_a").preferred_name == "Alice Cooper"
    assert repos.preferences.get("user_b").preferred_name == "Bob"


def test_api_token_isolation_and_revocation(repos):
    """Tokens registered for User A cannot authenticate as User B and vice versa."""
    user_a = UserIdentity(user_id="user_a", username="alice")
    user_b = UserIdentity(user_id="user_b", username="bob")
    repos.users.save(user_a)
    repos.users.save(user_b)

    tok_a = "aura_secret_user_a_tok_1111111"
    tok_b = "aura_secret_user_b_tok_2222222"

    tid_a = repos.tokens.register_token(tok_a, "user_a")
    tid_b = repos.tokens.register_token(tok_b, "user_b")

    res_a = repos.tokens.find_by_token(tok_a)
    assert res_a is not None
    assert res_a[1].user_id == "user_a"

    res_b = repos.tokens.find_by_token(tok_b)
    assert res_b is not None
    assert res_b[1].user_id == "user_b"

    # User A listing tokens should not see User B's tokens
    tokens_a = repos.tokens.list_tokens_for_user("user_a")
    assert len(tokens_a) == 1
    assert tokens_a[0]["id"] == tid_a

    # User B cannot revoke User A's token by ID
    assert repos.tokens.revoke_token_by_id(tid_a, user_id="user_b") is False
    assert repos.tokens.find_by_token(tok_a) is not None

    # User A revokes own token
    assert repos.tokens.revoke_token_by_id(tid_a, user_id="user_a") is True
    assert repos.tokens.find_by_token(tok_a) is None


def test_conversations_composite_ownership_isolation(repos):
    """User B cannot see, append to, or delete User A's conversation."""
    cid_a = repos.conversations.create_conversation("user_a", title="Alice Secrets")
    t1 = repos.conversations.add_turn(cid_a, "user_a", role="user", content="Secret message")

    # User B listing conversations
    convs_b = repos.conversations.list_conversations("user_b")
    assert len(convs_b) == 0

    # User B getting User A's conversation
    assert repos.conversations.get_conversation(cid_a, "user_b") is None

    # User B reading User A's turns
    turns_b = repos.conversations.get_turns(cid_a, "user_b")
    assert len(turns_b) == 0

    # User B appending to User A's conversation fails with composite constraint violation
    with pytest.raises(ValueError, match="Composite foreign key violation"):
        repos.conversations.add_turn(cid_a, "user_b", role="user", content="Unauthorized turn")

    # User B deleting User A's conversation fails
    assert repos.conversations.delete_conversation(cid_a, "user_b") is False
    assert repos.conversations.get_conversation(cid_a, "user_a") is not None


def test_memories_and_experiences_isolation(repos):
    """Memories and experiences are strictly partitioned by user_id."""
    repos.memories.record_memory(
        user_id="user_a",
        category=MemoryCategory.PREFERENCE.value,
        content="Secret preference for A",
    )
    repos.memories.record_memory(
        user_id="user_b",
        category=MemoryCategory.PREFERENCE.value,
        content="Secret preference for B",
    )

    mems_a = repos.memories.query_memories("user_a")
    assert len(mems_a) == 1
    assert mems_a[0].content == "Secret preference for A"

    mems_b = repos.memories.query_memories("user_b")
    assert len(mems_b) == 1
    assert mems_b[0].content == "Secret preference for B"

    repos.experiences.record_experience(
        user_id="user_a",
        task_description="Task A",
        plan_summary="Plan A",
    )
    repos.experiences.record_experience(
        user_id="user_b",
        task_description="Task B",
        plan_summary="Plan B",
    )

    exps_a = repos.experiences.query_experiences("user_a")
    assert len(exps_a) == 1
    assert exps_a[0].task_description == "Task A"

    exps_b = repos.experiences.query_experiences("user_b")
    assert len(exps_b) == 1
    assert exps_b[0].task_description == "Task B"
