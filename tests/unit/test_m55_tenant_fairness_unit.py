"""M55 — Multi-Tenant Concurrency Limits & Fairness Unit Tests.

Tests tenant quota enforcement, capacity reservations, non-negative underflow defense,
deficit round-robin ranking, and starvation prevention under heavy tenant load.
"""

import pytest

from core.fleet.fairness import TenantFairnessScheduler
from core.fleet.types import (
    TenantLimitExceededError,
    TenantWorkerLimitRecord,
    WorkerRecord,
    WorkerStatus,
)
from core.repositories.in_memory_fleet import InMemoryFleetRepository
from core.repositories.in_memory import InMemoryTaskRepository


@pytest.fixture
def fleet_repo() -> InMemoryFleetRepository:
    task_repo = InMemoryTaskRepository()
    return InMemoryFleetRepository(task_repo=task_repo)


@pytest.fixture
def fairness_scheduler(fleet_repo: InMemoryFleetRepository) -> TenantFairnessScheduler:
    return TenantFairnessScheduler(fleet_repo=fleet_repo)


def test_tenant_default_limits_provisioning(fairness_scheduler: TenantFairnessScheduler):
    """M55-F09: Unknown tenants are automatically provisioned with default limits."""
    can_admit = fairness_scheduler.can_admit_tenant("tenant_new")
    assert can_admit is True

    utilization = fairness_scheduler.get_tenant_utilization("tenant_new")
    assert utilization == 0.0


def test_tenant_slot_reservation_and_release(fairness_scheduler: TenantFairnessScheduler):
    """M55-F17: Slot reservation increments active count; release decrements cleanly."""
    t_id = "tenant_alpha"
    # Reserve slot
    new_count = fairness_scheduler.reserve_slot(t_id)
    assert new_count == 1
    assert fairness_scheduler.get_tenant_utilization(t_id) == 0.1  # 1 / 10

    # Release slot
    released_count = fairness_scheduler.release_slot(t_id)
    assert released_count == 0
    assert fairness_scheduler.get_tenant_utilization(t_id) == 0.0


def test_tenant_quota_exceeded_error(fleet_repo: InMemoryFleetRepository, fairness_scheduler: TenantFairnessScheduler):
    """M55-F09: Exceeding max_active_tasks raises TenantLimitExceededError."""
    t_id = "tenant_small"
    fleet_repo.set_tenant_limits(TenantWorkerLimitRecord(
        tenant_id=t_id,
        max_active_tasks=2,
        guaranteed_slots=1,
    ))

    # Fill capacity
    fairness_scheduler.reserve_slot(t_id)  # count = 1
    fairness_scheduler.reserve_slot(t_id)  # count = 2
    assert not fairness_scheduler.can_admit_tenant(t_id)

    with pytest.raises(TenantLimitExceededError):
        fairness_scheduler.reserve_slot(t_id)


def test_tenant_counter_underflow_prevention(fleet_repo: InMemoryFleetRepository):
    """M55-F18: Database / repository prevents active_task_count underflow below zero."""
    t_id = "tenant_underflow"
    with pytest.raises(ValueError):
        fleet_repo.adjust_tenant_active_count(t_id, -1)


def test_deficit_fairness_ranking(fleet_repo: InMemoryFleetRepository, fairness_scheduler: TenantFairnessScheduler):
    """M55-F10: Deficit scheduler ranks tenants with lower utilization first."""
    # Tenant 1: 5 active / 10 max = 0.5 ratio
    fleet_repo.set_tenant_limits(TenantWorkerLimitRecord(
        tenant_id="t_heavy", max_active_tasks=10, active_task_count=5
    ))
    # Tenant 2: 1 active / 10 max = 0.1 ratio
    fleet_repo.set_tenant_limits(TenantWorkerLimitRecord(
        tenant_id="t_medium", max_active_tasks=10, active_task_count=1
    ))
    # Tenant 3: 0 active / 10 max = 0.0 ratio
    fleet_repo.set_tenant_limits(TenantWorkerLimitRecord(
        tenant_id="t_idle", max_active_tasks=10, active_task_count=0
    ))

    ranked = fairness_scheduler.rank_tenants_by_deficit(["t_heavy", "t_medium", "t_idle"])
    ranked_ids = [r[0] for r in ranked]
    assert ranked_ids == ["t_idle", "t_medium", "t_heavy"]
