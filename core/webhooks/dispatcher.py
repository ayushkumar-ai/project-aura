"""M54 — Asynchronous Inbound Event Dispatcher Worker."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from core.repositories.base_webhook import BaseWebhookRepository
    from core.repositories.base import BaseTaskRepository, BaseAutomationRepository

from core.webhooks.types import InboundEventStatus

logger = logging.getLogger("aura.webhooks.dispatcher")


class InboundEventDispatcher:
    """Asynchronously processes accepted inbound webhook events, creating M52 tasks and triggering M53 automations."""

    def __init__(
        self,
        webhook_repo: BaseWebhookRepository,
        task_repo: BaseTaskRepository | None = None,
        automation_repo: BaseAutomationRepository | None = None,
        worker_id: str = "m54_dispatcher",
    ) -> None:
        self.webhook_repo = webhook_repo
        self.task_repo = task_repo
        self.automation_repo = automation_repo
        self.worker_id = worker_id

    def dispatch_batch(self, limit: int = 50) -> int:
        """Claim and dispatch up to `limit` due inbound events."""
        events = self.webhook_repo.lease_due_inbound_events(limit=limit, worker_id=self.worker_id, lease_seconds=60)
        dispatched_count = 0

        for event in events:
            try:
                task_id = self._dispatch_single_event(event)
                # Mark processed (atomically releases capacity)
                self.webhook_repo.mark_inbound_event_processed(event.id, task_id=task_id, lease_token=event.lease_token)
                dispatched_count += 1
            except Exception as e:
                logger.warning(f"Inbound dispatch failed for event {event.id} (attempt {event.attempts}): {e}")
                if event.attempts >= event.max_attempts:
                    # Dead-letter
                    self.webhook_repo.mark_inbound_event_dead_lettered(event.id, error_message=str(e), lease_token=event.lease_token)
                else:
                    # Retry with exponential backoff
                    backoff_delay = 2.0 ** event.attempts
                    next_attempt = datetime.utcnow() + timedelta(seconds=backoff_delay)
                    self.webhook_repo.mark_inbound_event_failed(event.id, error_message=str(e), next_attempt_at=next_attempt, lease_token=event.lease_token)

        return dispatched_count

    def _dispatch_single_event(self, event: Any) -> str | None:
        """Create M52 background task and trigger matching M53 automations."""
        task_id: str | None = None

        # 1. Enqueue M52 Task if task_repo is configured
        if self.task_repo is not None:
            # Deterministic idempotency key
            idempotency_key = f"m54_inbound_{event.id}"
            task_title = f"Webhook Event: {event.event_type}"
            
            # Syntactic prompt injection containment
            contained_payload = f"<untrusted_source_content>\n{event.payload}\n</untrusted_source_content>"

            try:
                # Check if task repository has create_task
                created_task = self.task_repo.create_task(
                    user_id=event.user_id,
                    title=task_title,
                    goal=f"Automated processing of inbound webhook {event.id} ({event.event_type})",
                    context={"event_id": event.id, "event_type": event.event_type, "content": contained_payload},
                    idempotency_key=idempotency_key,
                )
                if isinstance(created_task, dict):
                    task_id = created_task.get("id")
                elif hasattr(created_task, "id"):
                    task_id = str(created_task.id)
            except Exception as e:
                logger.warning(f"M52 task creation encountered conflict or error: {e}")

        # 2. Trigger M53 Automations if automation_repo is configured
        if self.automation_repo is not None:
            try:
                # Look for active event-triggered automations
                automations = self.automation_repo.list_automations(user_id=event.user_id, status="active")
                for auto in automations:
                    tt = auto.trigger_type if hasattr(auto, "trigger_type") else auto.get("trigger_type")
                    if str(tt) == "event":
                        cfg = auto.trigger_config if hasattr(auto, "trigger_config") else auto.get("trigger_config", {})
                        expected_event = cfg.get("event_type", "*")
                        if expected_event in ("*", event.event_type):
                            # Slot deduplication using event_id
                            auto_id = auto.id if hasattr(auto, "id") else auto.get("id")
                            now = datetime.utcnow()
                            self.automation_repo.claim_automation_run(
                                automation_id=auto_id,
                                user_id=event.user_id,
                                trigger_timestamp=now,
                                slot_timestamp=event.received_at,
                            )
            except Exception as e:
                logger.warning(f"M53 automation evaluation for event {event.id} encountered error: {e}")

        return task_id

EventDispatcherService = InboundEventDispatcher
