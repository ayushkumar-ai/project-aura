"""Unit tests for M30 Durable Personal State Subsystem."""

import tempfile
from pathlib import Path
from core.durable_state_store import DurablePersonalStateStore
from core.personal_state_types import (
    MemoryCategory,
    PersonalStateSnapshot,
    StateSchemaVersion,
    UserPreferences,
)


def test_preferences_lifecycle():
    store = DurablePersonalStateStore()
    prefs = store.get_preferences()
    assert prefs.preferred_name == "User"
    assert prefs.verbosity == 2

    updated = store.update_preferences({"preferred_name": "Alice", "verbosity": 4})
    assert updated.preferred_name == "Alice"
    assert updated.verbosity == 4
    assert store.get_preferences().preferred_name == "Alice"


def test_memory_recording_and_query():
    store = DurablePersonalStateStore()
    mem1 = store.record_memory(
        category=MemoryCategory.SEMANTIC,
        content="User prefers Python for scripting",
        confidence=0.95,
        tags=["python", "preferences"],
    )
    mem2 = store.record_memory(
        category=MemoryCategory.CONVERSATIONAL,
        content="Discussed project architecture yesterday",
        confidence=0.8,
        tags=["project"],
    )

    all_semantic = store.query_memories(category=MemoryCategory.SEMANTIC)
    assert len(all_semantic) == 1
    assert all_semantic[0].record_id == mem1.record_id

    tagged_python = store.query_memories(tag="python")
    assert len(tagged_python) == 1

    query_res = store.query_memories(query="architecture")
    assert len(query_res) == 1
    assert query_res[0].record_id == mem2.record_id


def test_experience_recording_and_query():
    store = DurablePersonalStateStore()
    exp = store.record_experience(
        task_description="Deploy web service to cluster",
        plan_summary="Build docker, push image, run helm",
        outcome="success",
        reward_score=0.9,
        lessons_learned=["Always set healthcheck timeout"],
    )

    results = store.query_experiences(outcome="success")
    assert len(results) == 1
    assert results[0].experience_id == exp.experience_id
    assert "Always set healthcheck timeout" in results[0].lessons_learned


def test_snapshot_checksum_integrity():
    snap = PersonalStateSnapshot(snapshot_id="snap_test_1")
    snap.seal()
    assert snap.checksum != ""
    assert snap.verify_integrity() is True

    # Tamper with snapshot
    snap.user_preferences.preferred_name = "Tampered"
    assert snap.verify_integrity() is False


def test_persistence_and_recovery_across_restart():
    with tempfile.TemporaryDirectory() as tmpdir:
        store1 = DurablePersonalStateStore(storage_dir=tmpdir)
        store1.update_preferences({"preferred_name": "Bob", "interaction_style": "technical"})
        store1.record_memory(
            category=MemoryCategory.PREFERENCE,
            content="Preferred editor is VS Code",
            tags=["editor"],
        )
        store1.record_experience(
            task_description="Compile C++ extension",
            plan_summary="cmake -B build",
            outcome="success",
        )
        store1.save_snapshot()

        # Instantiate second store from same storage directory
        store2 = DurablePersonalStateStore(storage_dir=tmpdir)
        assert store2.get_preferences().preferred_name == "Bob"
        assert store2.get_preferences().interaction_style == "technical"

        mems = store2.query_memories(tag="editor")
        assert len(mems) == 1
        assert mems[0].content == "Preferred editor is VS Code"

        exps = store2.query_experiences(outcome="success")
        assert len(exps) == 1
        assert exps[0].task_description == "Compile C++ extension"

        valid, msg = store2.verify_integrity()
        assert valid is True
        assert msg == "valid"
