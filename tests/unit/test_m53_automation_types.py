"""M53 Unit Tests — Domain Models & Serialization."""

from core.automations.types import (
    ActionTemplate,
    Automation,
    AutomationRun,
    AutomationStatus,
    CatchUpPolicy,
    ConditionConfig,
    RunStatus,
    TriggerConfig,
    TriggerType,
)


def test_automation_model_serialization():
    """Verify Automation dataclass to_dict and enum handling."""
    auto = Automation(
        id="auto_123",
        user_id="user_1",
        name="Nightly Backup",
        description="Run nightly backup task",
        status=AutomationStatus.ACTIVE,
        trigger_type=TriggerType.RECURRING,
        trigger_config={"cron": "0 0 * * *"},
        condition_config={"tier": 1},
        action_template={"title": "Backup", "goal": "Run backup"},
        fire_count=5,
    )
    d = auto.to_dict()
    assert d["id"] == "auto_123"
    assert d["status"] == "active"
    assert d["trigger_type"] == "recurring"
    assert d["fire_count"] == 5


def test_run_model_serialization():
    """Verify AutomationRun dataclass to_dict serialization."""
    run = AutomationRun(
        id="run_456",
        automation_id="auto_123",
        user_id="user_1",
        task_id="task_789",
        status=RunStatus.ENQUEUED,
        trigger_timestamp=1700000000.0,
        slot_timestamp=1700000000.0,
    )
    d = run.to_dict()
    assert d["id"] == "run_456"
    assert d["status"] == "enqueued"
    assert d["task_id"] == "task_789"
