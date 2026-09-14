"""M42 — Idempotent Legacy Local State Migration Tool for Project AURA.

Migrates local flat JSON snapshot state (.aura_checkpoints/personal_state.json)
into the authoritative PostgreSQL database or repository container.
Assigns legacy un-owned or default records to an explicit migration owner ID.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from app.config import Settings, settings
from core.database import DatabaseConnectionPool, MigrationRunner
from core.personal_state_types import PersonalStateSnapshot
from core.repositories.factory import create_repository_container

logger = logging.getLogger("aura.migration")


def migrate_local_state(
    checkpoint_dir: str | Path = ".aura_checkpoints",
    owner_id: str = "default",
    config: Settings | None = None,
    db_pool: DatabaseConnectionPool | None = None,
) -> dict[str, Any]:
    """Idempotently migrate flat JSON personal state to the repository persistence layer.
    
    Args:
        checkpoint_dir: Directory containing personal_state.json
        owner_id: User ID assigned to legacy records without an explicit owner
        config: AURA configuration settings
        db_pool: Optional live database connection pool
        
    Returns:
        Summary dict containing counts of migrated records.
    """
    chk_dir = Path(checkpoint_dir)
    state_file = chk_dir / "personal_state.json"
    backup_file = chk_dir / "personal_state.json.bak"

    target_file = None
    if state_file.exists():
        target_file = state_file
    elif backup_file.exists():
        target_file = backup_file

    if not target_file:
        logger.info(f"No legacy state file found in {chk_dir}. Nothing to migrate.")
        return {"status": "no_op", "memories_migrated": 0, "experiences_migrated": 0, "preferences_migrated": 0}

    try:
        raw_text = target_file.read_text(encoding="utf-8")
        raw_data = json.loads(raw_text)
        snapshot = PersonalStateSnapshot.from_dict(raw_data)
    except Exception as e:
        logger.error(f"Failed to read/parse snapshot from {target_file}: {e}")
        raise

    cfg = config or settings
    container = create_repository_container(config=cfg, db_pool=db_pool, auto_migrate=True)

    summary = {
        "status": "success",
        "snapshot_id": snapshot.snapshot_id,
        "schema_version": snapshot.schema_version.value,
        "preferences_migrated": 0,
        "memories_migrated": 0,
        "experiences_migrated": 0,
        "checkpoints_migrated": 0,
    }

    # 1. Migrate User Preferences
    if snapshot.user_preferences:
        prefs_dict = snapshot.user_preferences.to_dict()
        effective_owner = prefs_dict.get("user_id") or owner_id
        container.preferences.save(effective_owner, snapshot.user_preferences)
        summary["preferences_migrated"] += 1
        logger.info(f"Migrated user preferences for owner '{effective_owner}'")

    # 2. Migrate Unified Memories
    for mem in snapshot.memory_records:
        rec_owner = mem.user_id if (mem.user_id and mem.user_id != "default") else owner_id
        container.memories.record_memory(
            user_id=rec_owner,
            category=mem.category.value,
            content=mem.content,
            confidence=mem.confidence,
            importance=mem.importance,
            tags=mem.tags,
            provenance=mem.provenance,
            metadata=mem.metadata,
            memory_id=mem.record_id,
        )
        summary["memories_migrated"] += 1

    # 3. Migrate Episodic Experiences
    for exp in snapshot.episodic_experiences:
        exp_owner = exp.user_id if (exp.user_id and exp.user_id != "default") else owner_id
        container.experiences.record_experience(
            user_id=exp_owner,
            task_description=exp.task_description,
            plan_summary=exp.plan_summary,
            action_sequence=exp.action_sequence,
            outcome=exp.outcome,
            reward_score=exp.reward_score,
            lessons_learned=exp.lessons_learned,
            provenance=exp.provenance,
            metadata=exp.metadata,
            experience_id=exp.experience_id,
        )
        summary["experiences_migrated"] += 1

    # 4. Save checkpoint record of migration
    chk_id = container.checkpoints.save_checkpoint(
        checkpoint_type="legacy_migration",
        state_payload=snapshot.to_dict(),
        user_id=owner_id,
        checkpoint_id=f"mig_{snapshot.snapshot_id}",
    )
    summary["checkpoints_migrated"] += 1
    logger.info(f"Saved migration checkpoint '{chk_id}' for owner '{owner_id}'")

    return summary


def main() -> None:
    """CLI entrypoint for running legacy state migration."""
    parser = argparse.ArgumentParser(description="Migrate Project AURA legacy local JSON state to database.")
    parser.add_argument(
        "--checkpoint-dir",
        default=".aura_checkpoints",
        help="Directory containing personal_state.json (default: .aura_checkpoints)",
    )
    parser.add_argument(
        "--owner-id",
        default="default",
        help="User ID to assign to unowned legacy records (default: default)",
    )
    parser.add_argument(
        "--db-url",
        default="",
        help="Database connection URL (defaults to AURA_DATABASE_URL from env)",
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    cfg = Settings()
    if args.db_url:
        cfg.aura_database_url = args.db_url

    try:
        res = migrate_local_state(
            checkpoint_dir=args.checkpoint_dir,
            owner_id=args.owner_id,
            config=cfg,
        )
        print(json.dumps(res, indent=2))
    except Exception as e:
        logger.critical(f"Migration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
