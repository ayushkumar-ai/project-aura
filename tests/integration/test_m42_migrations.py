"""M42 — Migration & Idempotency Integration Tests for Project AURA."""

import json
import time
from pathlib import Path
import pytest

from core.personal_state_types import (
    EpisodicExperienceRecord,
    MemoryCategory,
    PersonalStateSnapshot,
    StateSchemaVersion,
    UnifiedMemoryRecord,
    UserPreferences,
)
from core.repositories.factory import create_in_memory_repositories
from scripts.migrate_local_state_to_postgres import migrate_local_state
from app.config import Settings


def test_legacy_state_migration_idempotent(tmp_path: Path):
    """Verify that migrate_local_state successfully imports legacy JSON state and is idempotent."""
    chk_dir = tmp_path / ".aura_checkpoints"
    chk_dir.mkdir(parents=True, exist_ok=True)
    state_file = chk_dir / "personal_state.json"

    # Create dummy snapshot
    snap = PersonalStateSnapshot(
        snapshot_id="snap_test_legacy_001",
        schema_version=StateSchemaVersion.CURRENT,
        created_at=time.time(),
        user_preferences=UserPreferences(user_id="default", interaction_style="technical", preferred_name="LegacyDev"),
        memory_records=[
            UnifiedMemoryRecord(
                record_id="mem_leg_1",
                category=MemoryCategory.PREFERENCE,
                content="Legacy user prefers dark mode",
                user_id="default",
                tags=["legacy", "ui"],
            ),
            UnifiedMemoryRecord(
                record_id="mem_leg_2",
                category=MemoryCategory.SEMANTIC,
                content="AURA was deployed in 2026",
                user_id="alice",
                tags=["history"],
            ),
        ],
        episodic_experiences=[
            EpisodicExperienceRecord(
                experience_id="exp_leg_1",
                task_description="Initial deployment",
                plan_summary="Setup database and server",
                user_id="default",
                outcome="success",
            )
        ],
    )
    snap.seal()

    state_file.write_text(json.dumps(snap.to_dict()), encoding="utf-8")

    test_settings = Settings(
        aura_env="development",
        aura_persistence_backend="memory",
    )

    # First Migration Run
    res1 = migrate_local_state(
        checkpoint_dir=chk_dir,
        owner_id="admin_migrated",
        config=test_settings,
    )

    assert res1["status"] == "success"
    assert res1["preferences_migrated"] == 1
    assert res1["memories_migrated"] == 2
    assert res1["experiences_migrated"] == 1
    assert res1["checkpoints_migrated"] == 1

    # Second Migration Run (Idempotency check)
    res2 = migrate_local_state(
        checkpoint_dir=chk_dir,
        owner_id="admin_migrated",
        config=test_settings,
    )

    assert res2["status"] == "success"
    assert res2["memories_migrated"] == 2
