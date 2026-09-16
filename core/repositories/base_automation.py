"""M53 — Base Automation Repository Interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseAutomationRepository(ABC):
    """Abstract base repository contract for proactive automations, runs, and leases."""

    @abstractmethod
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
        """Create and persist a new automation record."""
        pass

    @abstractmethod
    def get_automation(self, automation_id: str, user_id: str) -> dict[str, Any] | None:
        """Retrieve automation by ID strictly owned by user_id."""
        pass

    @abstractmethod
    def list_automations(
        self,
        user_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List automations for a tenant with optional status filtering."""
        pass

    @abstractmethod
    def count_automations(self, user_id: str, status: str | None = None) -> int:
        """Count automations for a tenant with optional status filtering."""
        pass

    @abstractmethod
    def update_automation(
        self,
        automation_id: str,
        user_id: str,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        """Update automation fields strictly owned by user_id."""
        pass

    @abstractmethod
    def delete_automation(self, automation_id: str, user_id: str) -> bool:
        """Delete an automation strictly owned by user_id."""
        pass

    @abstractmethod
    def pause_automation(self, automation_id: str, user_id: str) -> dict[str, Any] | None:
        """Transition automation status to paused."""
        pass

    @abstractmethod
    def resume_automation(
        self,
        automation_id: str,
        user_id: str,
        next_fire_at: float | None = None,
    ) -> dict[str, Any] | None:
        """Transition automation status back to active."""
        pass

    @abstractmethod
    def claim_due_automations(
        self,
        worker_id: str,
        limit: int = 10,
        lease_ttl_seconds: int = 120,
    ) -> list[dict[str, Any]]:
        """Atomically claim due automations using SELECT ... FOR UPDATE SKIP LOCKED."""
        pass

    @abstractmethod
    def renew_lease(
        self,
        automation_id: str,
        lease_token: str,
        lease_ttl_seconds: int = 120,
    ) -> bool:
        """Renew active lease heartbeat if lease_token matches."""
        pass

    @abstractmethod
    def release_lease(self, automation_id: str, lease_token: str) -> bool:
        """Explicitly release lease ownership if lease_token matches."""
        pass

    @abstractmethod
    def recover_stale_leases(self, limit: int = 50) -> int:
        """Recover expired leases and return count of recovered automations."""
        pass

    @abstractmethod
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
        """Execute the atomic, fenced dispatch transaction adhering to M53 specification."""
        pass

    @abstractmethod
    def get_run(self, run_id: str, user_id: str) -> dict[str, Any] | None:
        """Retrieve an automation run by ID strictly for user_id."""
        pass

    @abstractmethod
    def get_run_by_slot(self, automation_id: str, slot_timestamp: float) -> dict[str, Any] | None:
        """Retrieve an automation run by automation ID and slot timestamp."""
        pass

    @abstractmethod
    def list_runs(
        self,
        automation_id: str,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List execution runs for an automation."""
        pass

    @abstractmethod
    def record_run_terminal_state(
        self,
        run_id: str,
        status: str,
        error_message: str | None = None,
        condition_evaluation: dict[str, Any] | None = None,
        task_id: str | None = None,
    ) -> bool:
        """Record terminal state for a run."""
        pass

    @abstractmethod
    def reconcile_run_task_status(
        self,
        run_id: str,
        task_status: str,
        error_message: str | None = None,
    ) -> bool:
        """Reconcile run status based on downstream M52 task state."""
        pass
