"""M53 Unit Tests — Autonomous Supervisor Recursion & Cycle Bounds."""

import pytest

from core.automations.supervisor import AutonomousSupervisor
from core.automations.types import (
    AutomationCycleDetectedError,
    AutomationRecursionLimitExceededError,
)
from core.repositories.in_memory_automation import InMemoryAutomationRepository


def test_recursion_limit_enforced():
    """Verify recursion depth > 3 raises AutomationRecursionLimitExceededError."""
    repo = InMemoryAutomationRepository()
    class ReposContainer:
        automations = repo
    supervisor = AutonomousSupervisor(repositories=ReposContainer(), max_recursion_depth=3)

    # Valid depths <= 3
    supervisor.validate_lineage(automation_id="auto_1", parent_automation_id="auto_parent", recursion_depth=1)
    supervisor.validate_lineage(automation_id="auto_1", parent_automation_id="auto_parent", recursion_depth=3)

    # Exceeding depth 4
    with pytest.raises(AutomationRecursionLimitExceededError):
        supervisor.validate_lineage(automation_id="auto_1", parent_automation_id="auto_parent", recursion_depth=4)


def test_direct_cycle_detected():
    """Verify automation referencing itself raises AutomationCycleDetectedError."""
    repo = InMemoryAutomationRepository()
    class ReposContainer:
        automations = repo
    supervisor = AutonomousSupervisor(repositories=ReposContainer())

    with pytest.raises(AutomationCycleDetectedError):
        supervisor.validate_lineage(automation_id="auto_1", parent_automation_id="auto_1", recursion_depth=1)
