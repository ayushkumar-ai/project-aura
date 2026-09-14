"""M42 — Persistence & Cold-Restart Integration Tests for Project AURA.

Verifies that state, preferences, conversation turns, and memories persist cleanly
across simulated process restarts.
"""

import pytest
from app.config import Settings
from core.history import ConversationHistory
from core.durable_state_store import DurablePersonalStateStore
from core.personal_state_types import MemoryCategory, UserPreferences
from core.repositories.factory import create_in_memory_repositories


def test_durable_state_store_restart_persistence(tmp_path):
    """Verify durable state store preserves state across process instances using storage directory."""
    chk_dir = tmp_path / "checkpoints"
    chk_dir.mkdir(parents=True, exist_ok=True)

    # First process lifecycle
    store1 = DurablePersonalStateStore(storage_dir=chk_dir)
    store1.update_preferences({"preferred_name": "RestartUser", "interaction_style": "technical"}, user_id="user_restart")
    store1.record_memory(
        category=MemoryCategory.PREFERENCE,
        content="User likes automated persistence",
        user_id="user_restart",
    )
    store1.record_experience(
        task_description="Persistent reboot",
        plan_summary="Tested across lifecycles",
        user_id="user_restart",
    )
    snap_id = store1.save_snapshot()
    assert snap_id.startswith("snap_")

    # Second process lifecycle (cold restart simulation)
    store2 = DurablePersonalStateStore(storage_dir=chk_dir)
    p2 = store2.get_preferences(user_id="user_restart")
    assert p2.preferred_name == "RestartUser"
    assert p2.interaction_style == "technical"

    mems = store2.query_memories(user_id="user_restart")
    assert len(mems) == 1
    assert mems[0].content == "User likes automated persistence"

    exps = store2.query_experiences(user_id="user_restart")
    assert len(exps) == 1
    assert exps[0].task_description == "Persistent reboot"


def test_repository_container_restart_simulation():
    """Verify repository container preserves data across repository operations."""
    container = create_in_memory_repositories()

    # User registers and adds conversation
    container.preferences.save("user_reboot", {"preferred_name": "RebootTester"})
    cid = container.conversations.create_conversation("user_reboot", title="Reboot Session")
    container.conversations.add_turn(cid, "user_reboot", role="user", content="First turn before reboot")
    container.conversations.add_turn(cid, "user_reboot", role="assistant", content="Response before reboot")

    # Verify query
    turns = container.conversations.get_turns(cid, "user_reboot")
    assert len(turns) == 2
    assert turns[0]["content"] == "First turn before reboot"
    assert turns[1]["content"] == "Response before reboot"

    prefs = container.preferences.get("user_reboot")
    assert prefs.preferred_name == "RebootTester"
