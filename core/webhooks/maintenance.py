"""M54 — Webhook Maintenance Service for M53 Scheduler Integration."""

from __future__ import annotations

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from core.repositories.base_webhook import BaseWebhookRepository
import logging
from datetime import datetime

from core.webhooks.types import MaintenanceReport

logger = logging.getLogger("aura.webhooks.maintenance")


class WebhookMaintenanceService:
    """Bounded, idempotent maintenance operations invoked exclusively by the M53 maintenance loop."""

    def __init__(self, webhook_repo: BaseWebhookRepository) -> None:
        self.webhook_repo = webhook_repo

    def run_maintenance_once(
        self,
        now: datetime | None = None,
        retention_days: int = 30,
        batch_limit: int = 100,
    ) -> MaintenanceReport:
        """Executes bounded maintenance tasks:
        1. Prunes dead-letter records older than retention_days (in bounded batches <= batch_limit).
        2. Reclaims expired leases for inbound_events and event_deliveries.
        3. Reconciles tenant_webhook_capacities counters against active in-flight event rows.
        """
        if now is None:
            now = datetime.utcnow()

        logger.info(f"Running M54 maintenance cleanup (retention={retention_days}d, batch_limit={batch_limit})")
        report = self.webhook_repo.run_maintenance_cleanup(now=now, retention_days=retention_days, batch_limit=batch_limit)
        logger.info(f"M54 maintenance completed: {report.to_dict()}")
        return report
