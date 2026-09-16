"""M53 — Automation Crash Recovery & Task Reconciler."""

from __future__ import annotations

import logging
from typing import Any

from core.repositories.base_automation import BaseAutomationRepository

logger = logging.getLogger("aura.automations.reconciler")


class AutomationReconciler:
    """Reconciles stale distributed leases and synchronizes downstream M52 task state."""

    def __init__(
        self,
        automation_repo: BaseAutomationRepository,
        task_repo: Any | None = None,
    ) -> None:
        self.automation_repo = automation_repo
        self.task_repo = task_repo

    def recover_stale_leases(self, limit: int = 50) -> int:
        """Recover expired leases so due automations can be claimed by healthy workers."""
        try:
            return self.automation_repo.recover_stale_leases(limit=limit)
        except Exception as e:
            logger.error(f"Error recovering stale leases: {e}")
            return 0

    def reconcile_run_tasks(self, automation_id: str, user_id: str, limit: int = 50) -> int:
        """Synchronize terminal state of linked M52 tasks back to automation_runs."""
        if not self.task_repo:
            return 0

        reconciled_count = 0
        try:
            runs = self.automation_repo.list_runs(automation_id=automation_id, user_id=user_id, limit=limit)
            for r in runs:
                if r.get("status") == "enqueued" and r.get("task_id"):
                    task = self.task_repo.get_task(r["task_id"], user_id)
                    if task and task.get("status") in ("completed", "failed", "cancelled", "timed_out"):
                        ok = self.automation_repo.reconcile_run_task_status(
                            run_id=r["id"],
                            task_status=task["status"],
                            error_message=task.get("error_message"),
                        )
                        if ok:
                            reconciled_count += 1
        except Exception as e:
            logger.error(f"Error reconciling runs for automation {automation_id}: {e}")

        return reconciled_count
