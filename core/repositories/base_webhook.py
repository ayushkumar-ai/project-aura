"""M54 — Base Webhook and Event Gateway Repository Interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from core.webhooks.types import (
    WebhookEndpoint,
    WebhookSigningKey,
    InboundEvent,
    EventSubscription,
    EventDelivery,
    DeadLetterEvent,
    DeadLetterReplay,
    TenantWebhookCapacity,
    MaintenanceReport,
    WebhookEndpointStatus,
)


class BaseWebhookRepository(ABC):
    """Abstract interface for all webhook endpoints, keys, events, deliveries, capacities, and dead letters."""

    # Webhook Endpoints
    @abstractmethod
    def create_endpoint(self, endpoint: WebhookEndpoint) -> WebhookEndpoint:
        pass

    @abstractmethod
    def get_endpoint(self, endpoint_id: str, user_id: str | None = None) -> WebhookEndpoint | None:
        pass

    @abstractmethod
    def get_endpoint_by_path(self, user_id: str, path_suffix: str) -> WebhookEndpoint | None:
        pass

    @abstractmethod
    def list_endpoints(self, user_id: str) -> list[WebhookEndpoint]:
        pass

    @abstractmethod
    def update_endpoint_status(self, endpoint_id: str, user_id: str, status: WebhookEndpointStatus) -> bool:
        pass

    @abstractmethod
    def delete_endpoint(self, endpoint_id: str, user_id: str) -> bool:
        pass

    # Webhook Signing Keys
    @abstractmethod
    def create_signing_key(self, key: WebhookSigningKey) -> WebhookSigningKey:
        pass

    @abstractmethod
    def get_signing_keys_for_endpoint(self, endpoint_id: str, active_only: bool = True) -> list[WebhookSigningKey]:
        pass

    @abstractmethod
    def rotate_signing_key(self, endpoint_id: str, user_id: str, new_key: WebhookSigningKey) -> tuple[WebhookSigningKey, WebhookSigningKey | None]:
        pass

    @abstractmethod
    def revoke_signing_key(self, key_id: str, user_id: str) -> bool:
        pass

    # Inbound Events & Tenant Admission
    @abstractmethod
    def reserve_tenant_capacity_and_insert_event(
        self, user_id: str, event: InboundEvent
    ) -> tuple[bool, InboundEvent | None, bool]:
        """Atomically checks tenant capacity (< 100), reserves slot, and inserts accepted event.
        Returns: (is_admitted, resulting_event, is_duplicate)
        """
        pass

    @abstractmethod
    def find_inbound_event_by_provider_id(self, endpoint_id: str, provider_event_id: str) -> InboundEvent | None:
        pass

    @abstractmethod
    def get_inbound_event(self, event_id: str, user_id: str | None = None) -> InboundEvent | None:
        pass

    @abstractmethod
    def lease_due_inbound_events(
        self, limit: int = 50, worker_id: str = "worker", lease_seconds: int = 60
    ) -> list[InboundEvent]:
        pass

    @abstractmethod
    def mark_inbound_event_processed(
        self, event_id: str, task_id: str | None = None, lease_token: str | None = None
    ) -> bool:
        pass

    @abstractmethod
    def mark_inbound_event_failed(
        self, event_id: str, error_message: str, next_attempt_at: datetime, lease_token: str | None = None
    ) -> bool:
        pass

    @abstractmethod
    def mark_inbound_event_dead_lettered(
        self, event_id: str, error_message: str, lease_token: str | None = None
    ) -> bool:
        pass

    # Event Subscriptions
    @abstractmethod
    def create_subscription(self, subscription: EventSubscription) -> EventSubscription:
        pass

    @abstractmethod
    def get_subscription(self, subscription_id: str, user_id: str | None = None) -> EventSubscription | None:
        pass

    @abstractmethod
    def list_subscriptions(self, user_id: str, event_type: str | None = None) -> list[EventSubscription]:
        pass

    @abstractmethod
    def delete_subscription(self, subscription_id: str, user_id: str) -> bool:
        pass

    # Event Deliveries
    @abstractmethod
    def create_delivery(self, delivery: EventDelivery) -> EventDelivery:
        pass

    @abstractmethod
    def get_delivery(self, delivery_id: str, user_id: str | None = None) -> EventDelivery | None:
        pass

    @abstractmethod
    def list_deliveries(self, user_id: str, limit: int = 50) -> list[EventDelivery]:
        pass

    @abstractmethod
    def lease_due_deliveries(
        self, limit: int = 50, worker_id: str = "worker", lease_seconds: int = 30
    ) -> list[EventDelivery]:
        pass

    @abstractmethod
    def mark_delivery_delivered(
        self, delivery_id: str, status_code: int, lease_token: str | None = None
    ) -> bool:
        pass

    @abstractmethod
    def mark_delivery_retrying(
        self,
        delivery_id: str,
        next_attempt_at: datetime,
        last_error: str,
        status_code: int | None = None,
        lease_token: str | None = None,
    ) -> bool:
        pass

    @abstractmethod
    def mark_delivery_failed(
        self,
        delivery_id: str,
        last_error: str,
        status_code: int | None = None,
        lease_token: str | None = None,
    ) -> bool:
        pass

    @abstractmethod
    def mark_delivery_dead_lettered(
        self,
        delivery_id: str,
        reason: str,
        last_error: str,
        status_code: int | None = None,
        lease_token: str | None = None,
    ) -> bool:
        pass

    # Dead Letter Events & Replay
    @abstractmethod
    def create_dead_letter_event(self, dead_letter: DeadLetterEvent) -> DeadLetterEvent:
        pass

    @abstractmethod
    def get_dead_letter_event(self, dead_letter_id: str, user_id: str | None = None) -> DeadLetterEvent | None:
        pass

    @abstractmethod
    def list_dead_letter_events(self, user_id: str, limit: int = 50) -> list[DeadLetterEvent]:
        pass

    @abstractmethod
    def create_dead_letter_replay(
        self, replay: DeadLetterReplay, new_delivery: EventDelivery
    ) -> tuple[DeadLetterReplay, EventDelivery]:
        pass

    @abstractmethod
    def list_dead_letter_replays(self, dead_letter_id: str, user_id: str | None = None) -> list[DeadLetterReplay]:
        pass

    # Capacities & Maintenance
    @abstractmethod
    def get_tenant_capacity(self, user_id: str) -> TenantWebhookCapacity:
        pass

    @abstractmethod
    def reconcile_tenant_capacity(self, user_id: str) -> int:
        pass

    @abstractmethod
    def run_maintenance_cleanup(
        self, now: datetime, retention_days: int = 30, batch_limit: int = 100
    ) -> MaintenanceReport:
        pass
