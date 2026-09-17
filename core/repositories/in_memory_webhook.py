"""M54 — In-Memory Webhook and Event Gateway Repository Implementation."""

from __future__ import annotations

import copy
import logging
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any

from core.repositories.base_webhook import BaseWebhookRepository
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
    WebhookKeyStatus,
    InboundEventStatus,
    DeliveryStatus,
    DeadLetterType,
    PayloadCollisionError,
    TenantCapacityExceededError,
)

logger = logging.getLogger("aura.repositories.in_memory_webhook")


class InMemoryWebhookRepository(BaseWebhookRepository):
    """Thread-safe in-memory repository implementing all M54 webhook contracts."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._endpoints: dict[str, WebhookEndpoint] = {}
        self._signing_keys: dict[str, WebhookSigningKey] = {}
        self._inbound_events: dict[str, InboundEvent] = {}
        self._subscriptions: dict[str, EventSubscription] = {}
        self._deliveries: dict[str, EventDelivery] = {}
        self._dead_letters: dict[str, DeadLetterEvent] = {}
        self._dead_letter_replays: dict[str, DeadLetterReplay] = {}
        self._capacities: dict[str, TenantWebhookCapacity] = {}

    # --- Endpoints ---
    def create_endpoint(self, endpoint: WebhookEndpoint) -> WebhookEndpoint:
        with self._lock:
            # Check unique (user_id, path_suffix)
            for ep in self._endpoints.values():
                if ep.user_id == endpoint.user_id and ep.path_suffix == endpoint.path_suffix:
                    raise ValueError(f"Endpoint with path_suffix '{endpoint.path_suffix}' already exists for user")
            self._endpoints[endpoint.id] = copy.deepcopy(endpoint)
            return copy.deepcopy(endpoint)

    def get_endpoint(self, endpoint_id: str, user_id: str | None = None) -> WebhookEndpoint | None:
        with self._lock:
            ep = self._endpoints.get(endpoint_id)
            if not ep:
                return None
            if user_id and ep.user_id != user_id:
                return None
            return copy.deepcopy(ep)

    def get_endpoint_by_path(self, user_id: str, path_suffix: str) -> WebhookEndpoint | None:
        with self._lock:
            for ep in self._endpoints.values():
                if ep.user_id == user_id and ep.path_suffix == path_suffix:
                    return copy.deepcopy(ep)
            return None

    def list_endpoints(self, user_id: str) -> list[WebhookEndpoint]:
        with self._lock:
            return [
                copy.deepcopy(ep)
                for ep in self._endpoints.values()
                if ep.user_id == user_id
            ]

    def update_endpoint(self, endpoint_id: str, user_id: str, **kwargs: Any) -> WebhookEndpoint | None:
        with self._lock:
            ep = self._endpoints.get(endpoint_id)
            if not ep or ep.user_id != user_id:
                return None
            for k, v in kwargs.items():
                if hasattr(ep, k) and v is not None:
                    setattr(ep, k, v)
            ep.updated_at = datetime.utcnow()
            return copy.deepcopy(ep)

    def update_endpoint_status(self, endpoint_id: str, user_id: str, status: WebhookEndpointStatus) -> bool:
        with self._lock:
            ep = self._endpoints.get(endpoint_id)
            if not ep or ep.user_id != user_id:
                return False
            ep.status = status
            ep.updated_at = datetime.utcnow()
            return True

    def delete_endpoint(self, endpoint_id: str, user_id: str) -> bool:
        with self._lock:
            ep = self._endpoints.get(endpoint_id)
            if not ep or ep.user_id != user_id:
                return False
            del self._endpoints[endpoint_id]
            # Cascade delete keys & inbound events in memory
            self._signing_keys = {k: v for k, v in self._signing_keys.items() if v.endpoint_id != endpoint_id}
            return True

    # --- Signing Keys ---
    def create_signing_key(self, key: WebhookSigningKey) -> WebhookSigningKey:
        with self._lock:
            self._signing_keys[key.id] = copy.deepcopy(key)
            return copy.deepcopy(key)

    add_signing_key = create_signing_key

    def get_signing_keys_for_endpoint(self, endpoint_id: str, active_only: bool = True) -> list[WebhookSigningKey]:
        with self._lock:
            keys = [
                copy.deepcopy(k)
                for k in self._signing_keys.values()
                if k.endpoint_id == endpoint_id
            ]
            if active_only:
                now = datetime.utcnow()
                return [
                    k for k in keys
                    if k.key_status == WebhookKeyStatus.ACTIVE
                    or (k.key_status == WebhookKeyStatus.RETIRING and (not k.expires_at or k.expires_at > now))
                ]
            return keys

    def rotate_signing_key(
        self, endpoint_id: str, user_id: str, new_key: WebhookSigningKey
    ) -> tuple[WebhookSigningKey, WebhookSigningKey | None]:
        with self._lock:
            ep = self._endpoints.get(endpoint_id)
            if not ep or ep.user_id != user_id:
                raise ValueError("Endpoint not found or unauthorized")

            old_active: WebhookSigningKey | None = None
            now = datetime.utcnow()
            grace_expiry = now + timedelta(seconds=86400)

            for key in self._signing_keys.values():
                if key.endpoint_id == endpoint_id and key.key_status == WebhookKeyStatus.ACTIVE:
                    key.key_status = WebhookKeyStatus.RETIRING
                    key.expires_at = grace_expiry
                    old_active = copy.deepcopy(key)

            self._signing_keys[new_key.id] = copy.deepcopy(new_key)
            return copy.deepcopy(new_key), old_active

    def revoke_signing_key(self, key_id: str, user_id: str) -> bool:
        with self._lock:
            key = self._signing_keys.get(key_id)
            if not key or key.user_id != user_id:
                return False
            key.key_status = WebhookKeyStatus.REVOKED
            key.revoked_at = datetime.utcnow()
            return True

    # --- Inbound Events & Tenant Admission ---
    def find_inbound_event_by_provider_id(self, endpoint_id: str, provider_event_id: str) -> InboundEvent | None:
        with self._lock:
            for evt in self._inbound_events.values():
                if evt.endpoint_id == endpoint_id and evt.provider_event_id == provider_event_id:
                    return copy.deepcopy(evt)
            return None

    def reserve_tenant_capacity_and_insert_event(
        self, user_id: str, event: InboundEvent
    ) -> tuple[bool, InboundEvent | None, bool]:
        with self._lock:
            # Check for existing provider_event_id on endpoint
            existing = self.find_inbound_event_by_provider_id(event.endpoint_id, event.provider_event_id)
            if existing:
                if existing.payload_sha256 == event.payload_sha256:
                    # Duplicate: return cached accepted event with duplicate=True, no capacity change
                    return True, existing, True
                else:
                    # Collision: same ID, different hash -> reject with Collision error
                    raise PayloadCollisionError(
                        f"Duplicate provider_event_id '{event.provider_event_id}' with mismatched payload hash"
                    )

            # Check capacity
            cap = self._capacities.setdefault(user_id, TenantWebhookCapacity(user_id=user_id, in_flight_count=0, max_capacity=100))
            if cap.in_flight_count >= cap.max_capacity:
                return False, None, False

            # Reserve slot & insert
            cap.in_flight_count += 1
            cap.updated_at = datetime.utcnow()

            event.status = InboundEventStatus.ACCEPTED
            event.attempts = 0
            self._inbound_events[event.id] = copy.deepcopy(event)
            return True, copy.deepcopy(event), False

    def get_inbound_event(self, event_id: str, user_id: str | None = None) -> InboundEvent | None:
        with self._lock:
            evt = self._inbound_events.get(event_id)
            if not evt:
                return None
            if user_id and evt.user_id != user_id:
                return None
            return copy.deepcopy(evt)

    def lease_due_inbound_events(
        self, limit: int = 50, worker_id: str = "worker", lease_seconds: int = 60
    ) -> list[InboundEvent]:
        with self._lock:
            now = datetime.utcnow()
            leased: list[InboundEvent] = []

            for evt in self._inbound_events.values():
                if evt.status in (InboundEventStatus.ACCEPTED, InboundEventStatus.FAILED):
                    if evt.next_attempt_at <= now:
                        # Check lease
                        if not evt.lease_expires_at or evt.lease_expires_at < now:
                            evt.status = InboundEventStatus.PROCESSING
                            evt.attempts += 1
                            evt.last_attempt_at = now
                            evt.lease_owner = worker_id
                            evt.lease_token = str(uuid.uuid4())
                            evt.claimed_at = now
                            evt.lease_expires_at = now + timedelta(seconds=lease_seconds)
                            leased.append(copy.deepcopy(evt))
                            if len(leased) >= limit:
                                break
            return leased

    def mark_inbound_event_processed(
        self, event_id: str, task_id: str | None = None, lease_token: str | None = None
    ) -> bool:
        with self._lock:
            evt = self._inbound_events.get(event_id)
            if not evt:
                return False
            # Check non-terminal state and lease token if provided
            if evt.status not in (InboundEventStatus.ACCEPTED, InboundEventStatus.PROCESSING, InboundEventStatus.FAILED):
                return False
            if lease_token and evt.lease_token != lease_token:
                return False

            evt.status = InboundEventStatus.PROCESSED
            evt.task_id = task_id or evt.task_id
            evt.processed_at = datetime.utcnow()
            evt.lease_owner = None
            evt.lease_token = None
            evt.lease_expires_at = None

            # Exactly-once capacity release
            cap = self._capacities.get(evt.user_id)
            if cap and cap.in_flight_count > 0:
                cap.in_flight_count = max(0, cap.in_flight_count - 1)
                cap.updated_at = datetime.utcnow()

            return True

    def mark_inbound_event_failed(
        self, event_id: str, error_message: str, next_attempt_at: datetime, lease_token: str | None = None
    ) -> bool:
        with self._lock:
            evt = self._inbound_events.get(event_id)
            if not evt:
                return False
            if evt.status not in (InboundEventStatus.ACCEPTED, InboundEventStatus.PROCESSING, InboundEventStatus.FAILED):
                return False
            if lease_token and evt.lease_token != lease_token:
                return False

            evt.status = InboundEventStatus.FAILED
            evt.error_message = error_message
            evt.next_attempt_at = next_attempt_at
            evt.lease_owner = None
            evt.lease_token = None
            evt.lease_expires_at = None
            return True

    def mark_inbound_event_dead_lettered(
        self, event_id: str, error_message: str, lease_token: str | None = None
    ) -> bool:
        with self._lock:
            evt = self._inbound_events.get(event_id)
            if not evt:
                return False
            if evt.status not in (InboundEventStatus.ACCEPTED, InboundEventStatus.PROCESSING, InboundEventStatus.FAILED):
                return False
            if lease_token and evt.lease_token != lease_token:
                return False

            evt.status = InboundEventStatus.DEAD_LETTERED
            evt.error_message = error_message
            evt.lease_owner = None
            evt.lease_token = None
            evt.lease_expires_at = None

            # Record in dead_letter_events
            dl_id = f"dl_{uuid.uuid4()}"
            dl_event = DeadLetterEvent(
                id=dl_id,
                dead_letter_type=DeadLetterType.INBOUND,
                inbound_event_id=evt.id,
                user_id=evt.user_id,
                reason="max_dispatch_attempts_exceeded",
                final_error=error_message,
                attempts=evt.attempts,
                payload=evt.payload,
            )
            self._dead_letters[dl_id] = dl_event

            # Exactly-once capacity release
            cap = self._capacities.get(evt.user_id)
            if cap and cap.in_flight_count > 0:
                cap.in_flight_count = max(0, cap.in_flight_count - 1)
                cap.updated_at = datetime.utcnow()

            return True

    # --- Event Subscriptions ---
    def create_subscription(self, subscription: EventSubscription) -> EventSubscription:
        with self._lock:
            self._subscriptions[subscription.id] = copy.deepcopy(subscription)
            return copy.deepcopy(subscription)

    def get_subscription(self, subscription_id: str, user_id: str | None = None) -> EventSubscription | None:
        with self._lock:
            sub = self._subscriptions.get(subscription_id)
            if not sub:
                return None
            if user_id and sub.user_id != user_id:
                return None
            return copy.deepcopy(sub)

    def list_subscriptions(self, user_id: str, event_type: str | None = None) -> list[EventSubscription]:
        with self._lock:
            subs = [
                copy.deepcopy(s)
                for s in self._subscriptions.values()
                if s.user_id == user_id
            ]
            if event_type:
                return [s for s in subs if s.event_type_filter in ("*", event_type)]
            return subs

    def update_subscription(self, subscription_id: str, user_id: str, **kwargs: Any) -> EventSubscription | None:
        with self._lock:
            sub = self._subscriptions.get(subscription_id)
            if not sub or sub.user_id != user_id:
                return None
            for k, v in kwargs.items():
                if hasattr(sub, k) and v is not None:
                    setattr(sub, k, v)
            sub.updated_at = datetime.utcnow()
            return copy.deepcopy(sub)

    def delete_subscription(self, subscription_id: str, user_id: str) -> bool:
        with self._lock:
            sub = self._subscriptions.get(subscription_id)
            if not sub or sub.user_id != user_id:
                return False
            del self._subscriptions[subscription_id]
            return True

    # --- Event Deliveries ---
    def create_delivery(self, delivery: EventDelivery) -> EventDelivery:
        with self._lock:
            self._deliveries[delivery.id] = copy.deepcopy(delivery)
            return copy.deepcopy(delivery)

    def get_delivery(self, delivery_id: str, user_id: str | None = None) -> EventDelivery | None:
        with self._lock:
            deliv = self._deliveries.get(delivery_id)
            if not deliv:
                return None
            if user_id and deliv.user_id != user_id:
                return None
            return copy.deepcopy(deliv)

    def list_deliveries(self, user_id: str, limit: int = 50) -> list[EventDelivery]:
        with self._lock:
            delivs = [
                copy.deepcopy(d)
                for d in self._deliveries.values()
                if d.user_id == user_id
            ]
            delivs.sort(key=lambda d: d.created_at, reverse=True)
            return delivs[:limit]

    def lease_due_deliveries(
        self, limit: int = 50, worker_id: str = "worker", lease_seconds: int = 30
    ) -> list[EventDelivery]:
        with self._lock:
            now = datetime.utcnow()
            leased: list[EventDelivery] = []

            for deliv in self._deliveries.values():
                if deliv.status in (DeliveryStatus.PENDING, DeliveryStatus.RETRYING):
                    if deliv.next_attempt_at <= now:
                        if not deliv.lease_expires_at or deliv.lease_expires_at < now:
                            deliv.status = DeliveryStatus.DELIVERING
                            deliv.attempts += 1
                            deliv.last_attempt_at = now
                            deliv.lease_owner = worker_id
                            deliv.lease_token = str(uuid.uuid4())
                            deliv.claimed_at = now
                            deliv.lease_expires_at = now + timedelta(seconds=lease_seconds)
                            leased.append(copy.deepcopy(deliv))
                            if len(leased) >= limit:
                                break
            return leased

    def mark_delivery_delivered(
        self, delivery_id: str, status_code: int, lease_token: str | None = None
    ) -> bool:
        with self._lock:
            deliv = self._deliveries.get(delivery_id)
            if not deliv:
                return False
            if deliv.status not in (DeliveryStatus.PENDING, DeliveryStatus.DELIVERING, DeliveryStatus.RETRYING):
                return False
            if lease_token and deliv.lease_token != lease_token:
                return False

            deliv.status = DeliveryStatus.DELIVERED
            deliv.last_response_status = status_code
            deliv.completed_at = datetime.utcnow()
            deliv.lease_owner = None
            deliv.lease_token = None
            deliv.lease_expires_at = None
            return True

    def mark_delivery_retrying(
        self,
        delivery_id: str,
        next_attempt_at: datetime,
        last_error: str,
        status_code: int | None = None,
        lease_token: str | None = None,
    ) -> bool:
        with self._lock:
            deliv = self._deliveries.get(delivery_id)
            if not deliv:
                return False
            if deliv.status not in (DeliveryStatus.PENDING, DeliveryStatus.DELIVERING, DeliveryStatus.RETRYING):
                return False
            if lease_token and deliv.lease_token != lease_token:
                return False

            deliv.status = DeliveryStatus.RETRYING
            deliv.next_attempt_at = next_attempt_at
            deliv.last_error = last_error
            deliv.last_response_status = status_code
            deliv.lease_owner = None
            deliv.lease_token = None
            deliv.lease_expires_at = None
            return True

    def mark_delivery_failed(
        self,
        delivery_id: str,
        last_error: str,
        status_code: int | None = None,
        lease_token: str | None = None,
    ) -> bool:
        with self._lock:
            deliv = self._deliveries.get(delivery_id)
            if not deliv:
                return False
            if deliv.status not in (DeliveryStatus.PENDING, DeliveryStatus.DELIVERING, DeliveryStatus.RETRYING):
                return False
            if lease_token and deliv.lease_token != lease_token:
                return False

            deliv.status = DeliveryStatus.FAILED
            deliv.last_error = last_error
            deliv.last_response_status = status_code
            deliv.completed_at = datetime.utcnow()
            deliv.lease_owner = None
            deliv.lease_token = None
            deliv.lease_expires_at = None
            return True

    def mark_delivery_dead_lettered(
        self,
        delivery_id: str,
        reason: str,
        last_error: str,
        status_code: int | None = None,
        lease_token: str | None = None,
    ) -> bool:
        with self._lock:
            deliv = self._deliveries.get(delivery_id)
            if not deliv:
                return False
            if deliv.status not in (DeliveryStatus.PENDING, DeliveryStatus.DELIVERING, DeliveryStatus.RETRYING):
                return False
            if lease_token and deliv.lease_token != lease_token:
                return False

            deliv.status = DeliveryStatus.DEAD_LETTERED
            deliv.last_error = last_error
            deliv.last_response_status = status_code
            deliv.completed_at = datetime.utcnow()
            deliv.lease_owner = None
            deliv.lease_token = None
            deliv.lease_expires_at = None

            # Create immutable dead letter record
            dl_id = f"dl_{uuid.uuid4()}"
            dl_event = DeadLetterEvent(
                id=dl_id,
                dead_letter_type=DeadLetterType.OUTBOUND,
                delivery_id=deliv.id,
                user_id=deliv.user_id,
                reason=reason,
                final_error=last_error,
                attempts=deliv.attempts,
                payload=deliv.payload,
            )
            self._dead_letters[dl_id] = dl_event
            return True

    # --- Dead Letter & Replay ---
    def create_dead_letter_event(self, dead_letter: DeadLetterEvent) -> DeadLetterEvent:
        with self._lock:
            self._dead_letters[dead_letter.id] = copy.deepcopy(dead_letter)
            return copy.deepcopy(dead_letter)

    def get_dead_letter_event(self, dead_letter_id: str, user_id: str | None = None) -> DeadLetterEvent | None:
        with self._lock:
            dl = self._dead_letters.get(dead_letter_id)
            if not dl:
                return None
            if user_id and dl.user_id != user_id:
                return None
            return copy.deepcopy(dl)

    def list_dead_letter_events(self, user_id: str, limit: int = 50) -> list[DeadLetterEvent]:
        with self._lock:
            dls = [
                copy.deepcopy(d)
                for d in self._dead_letters.values()
                if d.user_id == user_id
            ]
            dls.sort(key=lambda d: d.created_at, reverse=True)
            return dls[:limit]

    def create_dead_letter_replay(
        self, replay: DeadLetterReplay, new_delivery: EventDelivery
    ) -> tuple[DeadLetterReplay, EventDelivery]:
        with self._lock:
            dl = self._dead_letters.get(replay.dead_letter_id)
            if not dl:
                raise ValueError("Dead-letter record not found")
            if dl.user_id != replay.user_id or dl.user_id != new_delivery.user_id:
                raise ValueError("Cross-tenant dead-letter replay unauthorized")

            # Historical dead_letter record remains 100% byte-identical and immutable!
            self._deliveries[new_delivery.id] = copy.deepcopy(new_delivery)
            self._dead_letter_replays[replay.id] = copy.deepcopy(replay)
            return copy.deepcopy(replay), copy.deepcopy(new_delivery)

    def list_dead_letter_replays(self, dead_letter_id: str, user_id: str | None = None) -> list[DeadLetterReplay]:
        with self._lock:
            reps = [
                copy.deepcopy(r)
                for r in self._dead_letter_replays.values()
                if r.dead_letter_id == dead_letter_id and (user_id is None or r.user_id == user_id)
            ]
            return reps

    # --- Capacities & Maintenance ---
    def get_tenant_capacity(self, user_id: str) -> TenantWebhookCapacity:
        with self._lock:
            cap = self._capacities.setdefault(user_id, TenantWebhookCapacity(user_id=user_id, in_flight_count=0, max_capacity=100))
            return copy.deepcopy(cap)

    def reconcile_tenant_capacity(self, user_id: str) -> int:
        with self._lock:
            actual = sum(
                1 for evt in self._inbound_events.values()
                if evt.user_id == user_id and evt.status in (InboundEventStatus.ACCEPTED, InboundEventStatus.PROCESSING, InboundEventStatus.FAILED)
            )
            cap = self._capacities.setdefault(user_id, TenantWebhookCapacity(user_id=user_id, in_flight_count=0, max_capacity=100))
            diff = abs(cap.in_flight_count - actual)
            cap.in_flight_count = actual
            cap.updated_at = datetime.utcnow()
            return diff

    def run_maintenance_cleanup(
        self, now: datetime, retention_days: int = 30, batch_limit: int = 100
    ) -> MaintenanceReport:
        with self._lock:
            cutoff = now - timedelta(days=retention_days)
            pruned = 0
            to_delete: list[str] = []

            for dl_id, dl in self._dead_letters.items():
                if dl.created_at < cutoff:
                    to_delete.append(dl_id)
                    pruned += 1
                    if pruned >= batch_limit:
                        break

            for dl_id in to_delete:
                del self._dead_letters[dl_id]

            # Reclaim expired leases
            reclaimed_inbound = 0
            for evt in self._inbound_events.values():
                if evt.status == InboundEventStatus.PROCESSING and evt.lease_expires_at and evt.lease_expires_at < now:
                    evt.status = InboundEventStatus.FAILED
                    evt.lease_owner = None
                    evt.lease_token = None
                    evt.lease_expires_at = None
                    reclaimed_inbound += 1

            reclaimed_deliveries = 0
            for deliv in self._deliveries.values():
                if deliv.status == DeliveryStatus.DELIVERING and deliv.lease_expires_at and deliv.lease_expires_at < now:
                    deliv.status = DeliveryStatus.RETRYING
                    deliv.lease_owner = None
                    deliv.lease_token = None
                    deliv.lease_expires_at = None
                    reclaimed_deliveries += 1

            # Reconcile all tenant capacities
            reconciled = 0
            for uid in list(self._capacities.keys()):
                diff = self.reconcile_tenant_capacity(uid)
                if diff > 0:
                    reconciled += 1

            return MaintenanceReport(
                pruned_dead_letters=pruned,
                reclaimed_inbound_leases=reclaimed_inbound,
                reclaimed_delivery_leases=reclaimed_deliveries,
                reconciled_capacities=reconciled,
                timestamp=now,
            )
