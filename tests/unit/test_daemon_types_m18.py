import pytest
from core.daemon_types import (
    DaemonStatus,
    SupervisorConfig,
    SupervisorTelemetry,
    CheckpointMetadata,
    sanitize_checkpoint_metadata,
    FORBIDDEN_PRIVILEGE_KEYS,
)
from core.provenance import wrap_tainted, is_tainted


def test_daemon_status_enum():
    assert DaemonStatus.STOPPED == "stopped"
    assert DaemonStatus.STARTING == "starting"
    assert DaemonStatus.RUNNING == "running"
    assert DaemonStatus.PAUSED == "paused"
    assert DaemonStatus.STOPPING == "stopping"
    assert DaemonStatus.FAILED == "failed"


def test_supervisor_config_defaults_and_validation():
    cfg = SupervisorConfig()
    assert cfg.heartbeat_interval_seconds == 1.0
    assert cfg.scheduler_interval_seconds == 2.0
    assert cfg.checkpoint_retention_count == 5
    assert cfg.auto_recover_on_startup is True

    # Validation errors
    with pytest.raises(ValueError):
        SupervisorConfig(heartbeat_interval_seconds=-1.0)
    with pytest.raises(ValueError):
        SupervisorConfig(checkpoint_dir="")
    with pytest.raises(ValueError):
        SupervisorConfig(checkpoint_retention_count=0)
    with pytest.raises(TypeError):
        SupervisorConfig(auto_recover_on_startup="yes")  # type: ignore


def test_supervisor_telemetry_creation():
    tel = SupervisorTelemetry(
        status=DaemonStatus.RUNNING,
        uptime_seconds=42.5,
        total_heartbeats=100,
        total_events_dispatched=5,
        total_goals_stepped=3,
        total_checkpoints_saved=2,
        total_errors=0,
        active_workers=1,
        scheduler_queue_depth=2,
        active_locks_count=1,
        pending_clarifications_count=1,
        budget_utilization={"active_goals_count": 1},
    )
    assert tel.status == DaemonStatus.RUNNING
    assert tel.uptime_seconds == 42.5
    assert tel.total_heartbeats == 100
    assert tel.active_workers == 1


def test_checkpoint_metadata_validation():
    meta = CheckpointMetadata(
        checkpoint_id="ckpt_12345",
        created_at=1700000000.0,
        version="1.0",
        goal_count=3,
        task_count=3,
        lock_count=1,
        clarification_count=0,
        event_queue_size=2,
        is_clean_shutdown=True,
    )
    assert meta.checkpoint_id == "ckpt_12345"
    assert meta.is_clean_shutdown is True

    with pytest.raises(ValueError):
        CheckpointMetadata(checkpoint_id="", created_at=1700000000.0)
    with pytest.raises(ValueError):
        CheckpointMetadata(checkpoint_id="valid", created_at=-5.0)


def test_sanitize_checkpoint_metadata_strips_forbidden_keys():
    tainted_val = wrap_tainted("untrusted_content", source_type="web")
    raw_meta = {
        "valid_key": "safe_value",
        "is_authorized": True,
        "bypass_policy": True,
        "skip_approval": True,
        "approved": "yes",
        "auto_approve": True,
        "permission": "admin",
        "authorized": True,
        "role_override": "root",
        "system_override": True,
        "nested": {
            "child_safe": 123,
            "is_authorized": True,
            "bypass_auth": True,
        },
        "tainted_item": tainted_val,
        "callable_fn": lambda x: x,
    }

    clean = sanitize_checkpoint_metadata(raw_meta)
    assert "valid_key" in clean
    assert clean["valid_key"] == "safe_value"
    assert "nested" in clean
    assert clean["nested"]["child_safe"] == 123
    assert "tainted_item" in clean
    # For JSON serialization, TaintedValue is serialized as canonical dict
    assert isinstance(clean["tainted_item"], dict)
    assert clean["tainted_item"]["__tainted__"] is True
    assert clean["tainted_item"]["raw_value"] == "untrusted_content"

    # Ensure none of the forbidden keys are present
    for k in FORBIDDEN_PRIVILEGE_KEYS:
        assert k not in clean
        assert k not in clean["nested"]
    assert "callable_fn" not in clean


def test_sanitize_restored_metadata_preserves_tainted_instances():
    tainted_val = wrap_tainted("untrusted_content", source_type="web")
    raw_meta = {
        "valid_key": "safe_value",
        "is_authorized": True,
        "nested": {
            "child_safe": 123,
            "is_authorized": True,
        },
        "tainted_item": tainted_val,
        "callable_fn": lambda x: x,
    }

    from core.daemon_types import sanitize_restored_metadata
    clean = sanitize_restored_metadata(raw_meta)
    assert "valid_key" in clean
    assert clean["valid_key"] == "safe_value"
    assert "nested" in clean
    assert clean["nested"]["child_safe"] == 123
    assert "tainted_item" in clean
    assert is_tainted(clean["tainted_item"])
    assert "is_authorized" not in clean
    assert "is_authorized" not in clean["nested"]
    assert "callable_fn" not in clean

