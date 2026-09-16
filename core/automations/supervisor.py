"""M53 — Autonomous Supervisor & Lifecycle Management."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from uuid import uuid4

from core.automations.condition_engine import ConditionEngine
from core.automations.reconciler import AutomationReconciler
from core.automations.scheduler import AutomationScheduler
from core.automations.types import (
    ActionTemplate,
    AutomationCycleDetectedError,
    AutomationNotFoundError,
    AutomationRecursionLimitExceededError,
    AutomationValidationError,
    TriggerType,
)
from core.repositories.base_automation import BaseAutomationRepository

logger = logging.getLogger("aura.automations.supervisor")


class AutonomousSupervisor:
    """Supervises proactive automation lifecycle, scheduling loops, reconciliation, and recursion bounds."""

    def __init__(
        self,
        repositories: Any,
        condition_engine: ConditionEngine | None = None,
        poll_interval: float = 5.0,
        reconcile_interval: float = 60.0,
        max_recursion_depth: int = 3,
        node_id: str | None = None,
    ) -> None:
        self.repos = repositories
        self.automation_repo: BaseAutomationRepository = (
            repositories.automations if hasattr(repositories, "automations") and repositories.automations else None
        )
        self.condition_engine = condition_engine or ConditionEngine(
            context_provider=getattr(repositories, "context_provider", None),
            model_gateway=getattr(repositories, "model_gateway", None),
        )
        self.poll_interval = poll_interval
        self.reconcile_interval = reconcile_interval
        self.max_recursion_depth = max_recursion_depth
        self.node_id = node_id or f"supervisor_{uuid4().hex[:8]}"

        self.scheduler = (
            AutomationScheduler(
                automation_repo=self.automation_repo,
                condition_engine=self.condition_engine,
                node_id=self.node_id,
            )
            if self.automation_repo
            else None
        )
        self.reconciler = (
            AutomationReconciler(
                automation_repo=self.automation_repo,
                task_repo=getattr(repositories, "tasks", None),
            )
            if self.automation_repo
            else None
        )

        self._running = False
        self._scheduler_task: asyncio.Task[None] | None = None
        self._reconciler_task: asyncio.Task[None] | None = None

    def validate_lineage(
        self,
        automation_id: str | list[str] = "",
        parent_automation_id: str | None = None,
        recursion_depth: int = 0,
    ) -> None:
        """Enforce strict cycle detection and recursion depth limits."""
        if isinstance(automation_id, (list, tuple)):
            if len(automation_id) > self.max_recursion_depth:
                raise AutomationRecursionLimitExceededError(
                    f"Lineage depth {len(automation_id)} exceeds maximum allowable bound {self.max_recursion_depth}"
                )
            if len(automation_id) != len(set(automation_id)):
                raise AutomationCycleDetectedError(
                    f"Recursion cycle detected in lineage: {automation_id}"
                )
            return

        if recursion_depth > self.max_recursion_depth:
            raise AutomationRecursionLimitExceededError(
                f"Recursion depth {recursion_depth} exceeds maximum allowable bound {self.max_recursion_depth}"
            )
        if parent_automation_id and parent_automation_id.strip() == str(automation_id).strip():
            raise AutomationCycleDetectedError(
                f"Direct recursion cycle detected: Automation '{automation_id}' cannot be its own parent"
            )

    def trigger_immediate(self, automation_id: str, user_id: str) -> dict[str, Any]:
        """Manually trigger an immediate execution of an automation under tenant isolation."""
        if not self.automation_repo:
            raise RuntimeError("Automation repository is not configured")

        auto = self.automation_repo.get_automation(automation_id, user_id)
        if not auto:
            raise AutomationNotFoundError(f"Automation {automation_id} not found")

        now = time.time()
        claimed = self.automation_repo.claim_due_automations(
            worker_id=self.node_id,
            limit=1,
            lease_ttl_seconds=120,
        )
        # If not claimable via due query, claim manually if active
        lease_token = str(uuid4())
        updated = self.automation_repo.update_automation(
            automation_id=automation_id,
            user_id=user_id,
            lease_owner=self.node_id,
            lease_token=lease_token,
            claimed_at=now,
            lease_expires_at=now + 120,
        )
        if not updated:
            raise RuntimeError("Failed to obtain lease for manual trigger")

        action_template = auto.get("action_template") or {}
        return self.automation_repo.execute_fenced_dispatch(
            automation_id=automation_id,
            user_id=user_id,
            lease_token=lease_token,
            slot_timestamp=now,
            trigger_timestamp=now,
            action_template=action_template,
            next_fire_at=auto.get("next_fire_at"),
        )

    async def start(self) -> None:
        """Start asynchronous background scheduler and reconciler loops."""
        if self._running or not self.scheduler or not self.reconciler:
            return
        self._running = True
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        self._reconciler_task = asyncio.create_task(self._reconciler_loop())
        logger.info(f"AutonomousSupervisor started (node_id={self.node_id})")

    async def stop(self) -> None:
        """Gracefully stop background loops and release active resources."""
        self._running = False
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
        if self._reconciler_task:
            self._reconciler_task.cancel()
            try:
                await self._reconciler_task
            except asyncio.CancelledError:
                pass
        logger.info(f"AutonomousSupervisor stopped (node_id={self.node_id})")

    async def _scheduler_loop(self) -> None:
        while self._running:
            try:
                if self.scheduler:
                    self.scheduler.tick()
            except Exception as e:
                logger.error(f"Scheduler tick encountered error: {e}")
            await asyncio.sleep(self.poll_interval)

    async def _reconciler_loop(self) -> None:
        while self._running:
            try:
                if self.reconciler:
                    self.reconciler.recover_stale_leases()
            except Exception as e:
                logger.error(f"Reconciler tick encountered error: {e}")
            await asyncio.sleep(self.reconcile_interval)
