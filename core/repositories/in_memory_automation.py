"""M53 — In-Memory Automation Repository Implementation."""

from __future__ import annotations

import copy
import logging
import threading
import time
from typing import Any
from uuid import uuid4

from core.automations.types import (
    AutomationNotFoundError,
    AutomationQuotaExceededError,
    LeaseFencingError,
    TransactionDeadlineExceededError,
)
from core.repositories.base_automation import BaseAutomationRepository

logger = logging.getLogger("aura.repositories.in_memory_automation")


class InMemoryAutomationRepository(BaseAutomationRepository):
    """Hermetic thread-safe in-memory automation repository for testing and standalone operation."""

    def __init__(self, task_repo: Any | None = None) -> None:
        self.task_repo = task_repo
        self._lock = threading.RLock()
        self._automations: dict[str, dict[str, Any]] = {}
        self._runs: dict[str, dict[str, Any]] = {}
        self._hourly_usage: dict[str, dict[str, int]] = {}

    def create_automation(
        self,
        user_id: str,
        name: str,
        trigger_type: str,
        trigger_config: dict[str, Any],
        condition_config: dict[str, Any] | None = None,
        action_template: dict[str, Any] | None = None,
        description: str = "",
        max_runs: int | None = None,
        cooldown_seconds: int = 60,
        metadata: dict[str, Any] | None = None,
        next_fire_at: float | None = None,
        automation_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            eff_id = (automation_id or str(uuid4())).strip()
            now = time.time()
            record = {
                "id": eff_id,
                "user_id": user_id,
                "name": name.strip(),
                "description": description.strip(),
                "status": "active",
                "trigger_type": trigger_type.strip(),
                "trigger_config": copy.deepcopy(trigger_config),
                "condition_config": copy.deepcopy(condition_config or {}),
                "action_template": copy.deepcopy(action_template or {}),
                "next_fire_at": next_fire_at,
                "last_fired_at": None,
                "fire_count": 0,
                "max_runs": max_runs,
                "cooldown_seconds": int(cooldown_seconds),
                "lease_owner": None,
                "lease_token": None,
                "claimed_at": None,
                "lease_expires_at": None,
                "metadata": copy.deepcopy(metadata or {}),
                "created_at": now,
                "updated_at": now,
            }
            self._automations[eff_id] = record
            return copy.deepcopy(record)

    def get_automation(self, automation_id: str, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            rec = self._automations.get(automation_id)
            if rec and rec["user_id"] == user_id:
                return copy.deepcopy(rec)
            return None

    def list_automations(
        self,
        user_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._lock:
            matches = [
                rec for rec in self._automations.values()
                if rec["user_id"] == user_id and (status is None or rec["status"] == status)
            ]
            matches.sort(key=lambda x: x["created_at"], reverse=True)
            return copy.deepcopy(matches[offset : offset + limit])

    def count_automations(self, user_id: str, status: str | None = None) -> int:
        with self._lock:
            return sum(
                1 for rec in self._automations.values()
                if rec["user_id"] == user_id and (status is None or rec["status"] == status)
            )

    def update_automation(
        self,
        automation_id: str,
        user_id: str,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        with self._lock:
            rec = self._automations.get(automation_id)
            if not rec or rec["user_id"] != user_id:
                return None

            allowed = {
                "name", "description", "status", "trigger_config", "condition_config",
                "action_template", "max_runs", "cooldown_seconds", "metadata",
                "next_fire_at", "last_fired_at", "fire_count", "lease_owner",
                "lease_token", "claimed_at", "lease_expires_at"
            }
            for k, v in kwargs.items():
                if k in allowed:
                    rec[k] = copy.deepcopy(v) if isinstance(v, (dict, list)) else v
            rec["updated_at"] = time.time()
            return copy.deepcopy(rec)

    def delete_automation(self, automation_id: str, user_id: str) -> bool:
        with self._lock:
            rec = self._automations.get(automation_id)
            if rec and rec["user_id"] == user_id:
                del self._automations[automation_id]
                return True
            return False

    def pause_automation(self, automation_id: str, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            rec = self._automations.get(automation_id)
            if not rec or rec["user_id"] != user_id:
                return None
            rec["status"] = "paused"
            rec["next_fire_at"] = None
            rec["lease_owner"] = None
            rec["lease_token"] = None
            rec["claimed_at"] = None
            rec["lease_expires_at"] = None
            rec["updated_at"] = time.time()
            return copy.deepcopy(rec)

    def resume_automation(
        self,
        automation_id: str,
        user_id: str,
        next_fire_at: float | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            rec = self._automations.get(automation_id)
            if not rec or rec["user_id"] != user_id:
                return None
            rec["status"] = "active"
            if next_fire_at is not None:
                rec["next_fire_at"] = next_fire_at
            rec["lease_owner"] = None
            rec["lease_token"] = None
            rec["claimed_at"] = None
            rec["lease_expires_at"] = None
            rec["updated_at"] = time.time()
            return copy.deepcopy(rec)

    def claim_due_automations(
        self,
        worker_id: str,
        limit: int = 10,
        lease_ttl_seconds: int = 120,
    ) -> list[dict[str, Any]]:
        with self._lock:
            now = time.time()
            due = [
                rec for rec in self._automations.values()
                if rec["status"] == "active"
                and rec["next_fire_at"] is not None
                and rec["next_fire_at"] <= now
                and (rec["lease_expires_at"] is None or rec["lease_expires_at"] < now)
            ]
            due.sort(key=lambda x: x["next_fire_at"] or 0)
            claimed: list[dict[str, Any]] = []

            for rec in due[:limit]:
                token = str(uuid4())
                rec["lease_owner"] = worker_id
                rec["lease_token"] = token
                rec["claimed_at"] = now
                rec["lease_expires_at"] = now + lease_ttl_seconds
                rec["updated_at"] = now
                claimed.append(copy.deepcopy(rec))

            return claimed

    def renew_lease(
        self,
        automation_id: str,
        lease_token: str,
        lease_ttl_seconds: int = 120,
    ) -> bool:
        with self._lock:
            now = time.time()
            rec = self._automations.get(automation_id)
            if not rec or rec.get("lease_token") != lease_token:
                return False
            rec["lease_expires_at"] = now + lease_ttl_seconds
            rec["updated_at"] = now
            return True

    def release_lease(self, automation_id: str, lease_token: str) -> bool:
        with self._lock:
            rec = self._automations.get(automation_id)
            if not rec or rec.get("lease_token") != lease_token:
                return False
            rec["lease_owner"] = None
            rec["lease_token"] = None
            rec["claimed_at"] = None
            rec["lease_expires_at"] = None
            rec["updated_at"] = time.time()
            return True

    def recover_stale_leases(self, limit: int = 50) -> int:
        with self._lock:
            now = time.time()
            count = 0
            for rec in self._automations.values():
                if rec["status"] == "active" and rec["lease_expires_at"] and rec["lease_expires_at"] < now:
                    rec["lease_owner"] = None
                    rec["lease_token"] = None
                    rec["claimed_at"] = None
                    rec["lease_expires_at"] = None
                    rec["updated_at"] = now
                    count += 1
                    if count >= limit:
                        break
            return count

    def execute_fenced_dispatch(
        self,
        automation_id: str,
        user_id: str,
        lease_token: str,
        slot_timestamp: float,
        trigger_timestamp: float,
        action_template: dict[str, Any],
        next_fire_at: float | None,
        max_runs_per_hour: int = 60,
        timeout_seconds: float = 5.0,
        lock_timeout_ms: int = 3000,
        statement_timeout_ms: int = 3000,
    ) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            rec = self._automations.get(automation_id)
            if not rec or rec["user_id"] != user_id:
                raise AutomationNotFoundError(f"Automation {automation_id} not found")

            # Fencing check
            if rec.get("lease_token") != lease_token or rec.get("lease_expires_at") is None or rec["lease_expires_at"] <= now or rec["status"] != "active":
                raise LeaseFencingError("Lease expired or invalid before lock acquisition")

            # Check if run exists for slot
            for existing in self._runs.values():
                if existing["automation_id"] == automation_id and existing["slot_timestamp"] == slot_timestamp:
                    rec["lease_owner"] = None
                    rec["lease_token"] = None
                    rec["claimed_at"] = None
                    rec["lease_expires_at"] = None
                    rec["last_fired_at"] = now
                    rec["fire_count"] += 1
                    rec["next_fire_at"] = next_fire_at
                    rec["updated_at"] = now
                    return {
                        "id": existing["id"],
                        "run_id": existing["id"],
                        "task_id": existing.get("task_id"),
                        "status": existing["status"],
                        "quota_charged": 0,
                    }

            # Check hourly quota
            hour_bucket = time.strftime("%Y-%m-%d-%H", time.gmtime(now))
            user_hourly = self._hourly_usage.setdefault(user_id, {})
            current_usage = user_hourly.get(hour_bucket, 0)

            run_id = str(uuid4())
            if current_usage >= max_runs_per_hour:
                run_rec = {
                    "id": run_id,
                    "automation_id": automation_id,
                    "user_id": user_id,
                    "task_id": None,
                    "status": "skipped",
                    "trigger_timestamp": trigger_timestamp,
                    "slot_timestamp": slot_timestamp,
                    "lease_token": lease_token,
                    "condition_evaluation": None,
                    "error_message": "hourly_quota_exhausted",
                    "reconciled_at": now,
                    "created_at": now,
                    "completed_at": now,
                }
                self._runs[run_id] = run_rec
                rec["lease_owner"] = None
                rec["lease_token"] = None
                rec["claimed_at"] = None
                rec["lease_expires_at"] = None
                rec["last_fired_at"] = now
                rec["fire_count"] += 1
                rec["next_fire_at"] = next_fire_at
                rec["updated_at"] = now
                if max_runs_per_hour == 0:
                    raise AutomationQuotaExceededError("Hourly quota exhausted (limit=0)")
                return {
                    "id": run_id,
                    "run_id": run_id,
                    "task_id": None,
                    "status": "skipped",
                    "error": "hourly_quota_exhausted",
                    "quota_charged": 0,
                }

            # Admitted: Charge quota and enqueue
            user_hourly[hour_bucket] = current_usage + 1
            task_id = str(uuid4())
            run_rec = {
                "id": run_id,
                "automation_id": automation_id,
                "user_id": user_id,
                "task_id": task_id,
                "status": "enqueued",
                "trigger_timestamp": trigger_timestamp,
                "slot_timestamp": slot_timestamp,
                "lease_token": lease_token,
                "condition_evaluation": None,
                "error_message": None,
                "reconciled_at": None,
                "created_at": now,
                "completed_at": None,
            }
            self._runs[run_id] = run_rec

            # Advance schedule and release lease
            rec["lease_owner"] = None
            rec["lease_token"] = None
            rec["claimed_at"] = None
            rec["lease_expires_at"] = None
            rec["last_fired_at"] = now
            rec["fire_count"] += 1
            rec["next_fire_at"] = next_fire_at
            rec["updated_at"] = now

            return {
                "id": run_id,
                "run_id": run_id,
                "task_id": task_id,
                "status": "enqueued",
                "quota_charged": 1,
            }

    def get_run(self, run_id: str, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run and run["user_id"] == user_id:
                return copy.deepcopy(run)
            return None

    def get_run_by_slot(self, automation_id: str, slot_timestamp: float) -> dict[str, Any] | None:
        with self._lock:
            for run in self._runs.values():
                if run["automation_id"] == automation_id and run["slot_timestamp"] == slot_timestamp:
                    return copy.deepcopy(run)
            return None

    def list_runs(
        self,
        automation_id: str,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._lock:
            runs = [
                r for r in self._runs.values()
                if r["automation_id"] == automation_id and r["user_id"] == user_id
            ]
            runs.sort(key=lambda x: x["slot_timestamp"], reverse=True)
            return copy.deepcopy(runs[offset : offset + limit])

    def record_run_terminal_state(
        self,
        run_id: str,
        status: str,
        error_message: str | None = None,
        condition_evaluation: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> bool:
        with self._lock:
            run = self._runs.get(run_id)
            if not run:
                return False
            run["status"] = status
            if error_message:
                run["error_message"] = error_message
            if condition_evaluation:
                run["condition_evaluation"] = copy.deepcopy(condition_evaluation)
            if task_id:
                run["task_id"] = task_id
            run["reconciled_at"] = time.time()
            if status in ("completed", "failed", "cancelled", "skipped"):
                run["completed_at"] = time.time()
            return True

    def reconcile_run_task_status(
        self,
        run_id: str,
        task_status: str,
        error_message: str | None = None,
    ) -> bool:
        with self._lock:
            run = self._runs.get(run_id)
            if not run:
                return False
            run["status"] = task_status
            if error_message:
                run["error_message"] = error_message
            run["reconciled_at"] = time.time()
            if task_status in ("completed", "failed", "cancelled"):
                run["completed_at"] = time.time()
            return True
