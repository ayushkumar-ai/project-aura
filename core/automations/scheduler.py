"""M53 — Proactive Automation Scheduler."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from uuid import uuid4

from core.automations.condition_engine import ConditionEngine
from core.automations.cron_parser import calculate_next_fire, CronExpression
from core.automations.types import (
    AutomationNotFoundError,
    CatchUpPolicy,
    LeaseFencingError,
    RunStatus,
    TriggerType,
)
from core.repositories.base_automation import BaseAutomationRepository

logger = logging.getLogger("aura.automations.scheduler")


class AutomationScheduler:
    """Polls due automations, evaluates conditions, and dispatches admitted M52 tasks."""

    def __init__(
        self,
        automation_repo: BaseAutomationRepository,
        condition_engine: ConditionEngine | None = None,
        node_id: str | None = None,
        batch_size: int = 10,
        lease_ttl_seconds: int = 120,
        max_runs_per_hour: int = 60,
    ) -> None:
        self.automation_repo = automation_repo
        self.condition_engine = condition_engine or ConditionEngine()
        self.node_id = node_id or f"node_{uuid4().hex[:8]}"
        self.batch_size = batch_size
        self.lease_ttl_seconds = lease_ttl_seconds
        self.max_runs_per_hour = max_runs_per_hour

    def tick(self, now: float | None = None) -> list[dict[str, Any]]:
        """Execute one polling and dispatch cycle."""
        curr_time = now if now is not None else time.time()
        claimed = self.automation_repo.claim_due_automations(
            worker_id=self.node_id,
            limit=self.batch_size,
            lease_ttl_seconds=self.lease_ttl_seconds,
        )
        if not claimed:
            return []

        results: list[dict[str, Any]] = []
        for auto in claimed:
            try:
                res = self._process_claimed_automation(auto, curr_time)
                if res:
                    results.append(res)
            except Exception as e:
                logger.error(f"Error processing automation {auto.get('id')}: {e}")
                # Release lease if processing raised unexpected exception
                try:
                    token = auto.get("lease_token")
                    if token:
                        self.automation_repo.release_lease(auto["id"], token)
                except Exception:
                    pass

        return results

    def _process_claimed_automation(self, auto: dict[str, Any], curr_time: float) -> dict[str, Any] | None:
        auto_id = auto["id"]
        user_id = auto["user_id"]
        lease_token = auto["lease_token"]
        trigger_type = auto.get("trigger_type", "recurring")
        trigger_cfg = auto.get("trigger_config") or {}
        cond_cfg = auto.get("condition_config")
        action_template = auto.get("action_template") or {}

        # 1. Determine slot timestamp
        slot_timestamp = auto.get("next_fire_at") or curr_time

        # 2. Calculate next fire timestamp
        next_fire_at: float | None = None
        if trigger_type == TriggerType.RECURRING.value or trigger_type == "recurring":
            cron_expr = trigger_cfg.get("cron")
            if cron_expr:
                try:
                    next_fire_at = calculate_next_fire(cron_expr, from_timestamp=curr_time)
                except Exception as e:
                    logger.warning(f"Failed to calculate next fire for cron '{cron_expr}': {e}")
        elif trigger_type == TriggerType.ONE_TIME.value or trigger_type == "one_time":
            next_fire_at = None  # Never fires again

        # 3. Evaluate condition (Three-Tier Engine)
        cond_passed, cond_metadata = self.condition_engine.evaluate(
            user_id=user_id,
            condition_config=cond_cfg,
        )

        # 4. Handle Condition Failure (0 quota, 0 tasks)
        if not cond_passed:
            run_id = str(uuid4())
            # Advance schedule and release lease
            self.automation_repo.update_automation(
                automation_id=auto_id,
                user_id=user_id,
                next_fire_at=next_fire_at,
                status="completed" if trigger_type in ("one_time", TriggerType.ONE_TIME.value) else "active",
            )
            self.automation_repo.release_lease(auto_id, lease_token)
            return {
                "automation_id": auto_id,
                "status": "condition_failed",
                "condition_evaluation": cond_metadata,
                "quota_charged": 0,
            }

        # 5. Execute Fenced Dispatch Transaction
        dispatch_res = self.automation_repo.execute_fenced_dispatch(
            automation_id=auto_id,
            user_id=user_id,
            lease_token=lease_token,
            slot_timestamp=slot_timestamp,
            trigger_timestamp=curr_time,
            action_template=action_template,
            next_fire_at=next_fire_at,
            max_runs_per_hour=self.max_runs_per_hour,
        )
        dispatch_res["automation_id"] = auto_id
        return dispatch_res
