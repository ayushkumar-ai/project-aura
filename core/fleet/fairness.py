"""M55 — Tenant Fairness & Concurrency Limiting Engine.

Enforces multi-tenant quotas, evaluates deficit round-robin fairness,
prevents starvation from heavy tenants, and manages capacity accounting.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from core.fleet.types import TenantLimitExceededError, TenantWorkerLimitRecord
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from core.repositories.base_fleet import BaseFleetRepository

logger = logging.getLogger("aura.fleet.fairness")


class TenantFairnessScheduler:
    """Evaluates multi-tenant fair-share scheduling and manages active capacity."""

    def __init__(self, fleet_repo: BaseFleetRepository) -> None:
        self.fleet_repo = fleet_repo

    def can_admit_tenant(self, tenant_id: str) -> bool:
        """Check if tenant has remaining concurrent task capacity."""
        limits = self.fleet_repo.get_tenant_limits(tenant_id)
        return limits.has_capacity

    def reserve_slot(self, tenant_id: str) -> int:
        """Atomically reserve a concurrency slot for a tenant."""
        limits = self.fleet_repo.get_tenant_limits(tenant_id)
        if not limits.has_capacity:
            raise TenantLimitExceededError(
                f"Tenant {tenant_id} has exceeded maximum active task limit ({limits.active_task_count}/{limits.max_active_tasks})"
            )
        return self.fleet_repo.adjust_tenant_active_count(tenant_id, 1)

    def release_slot(self, tenant_id: str) -> int:
        """Atomically release an active concurrency slot for a tenant."""
        try:
            return self.fleet_repo.adjust_tenant_active_count(tenant_id, -1)
        except ValueError as e:
            logger.warning(f"Tenant slot release warning for {tenant_id}: {e}")
            return 0

    def get_tenant_utilization(self, tenant_id: str) -> float:
        """Calculate live capacity utilization ratio for a tenant."""
        limits = self.fleet_repo.get_tenant_limits(tenant_id)
        return limits.utilization_ratio

    def rank_tenants_by_deficit(self, tenant_ids: List[str]) -> List[Tuple[str, float]]:
        """Rank tenant IDs in ascending order of utilization ratio (lowest utilization first)."""
        scored = []
        for tid in set(tenant_ids):
            limits = self.fleet_repo.get_tenant_limits(tid)
            scored.append((tid, limits.utilization_ratio))
        scored.sort(key=lambda x: x[1])
        return scored
